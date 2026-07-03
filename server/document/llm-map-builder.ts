import crypto from "node:crypto";
import { modelDefinition } from "../defaults.js";
import { askStructuredJson } from "../llm.js";
import type { DocumentBlock, DocumentElementType, DocumentMap, DocumentMapElement, DocumentMapIssue, ExtractedDocument, LlmUsageStats, Provider } from "../types.js";
import { extractBestTitle } from "./title.js";

const TYPES = new Set<DocumentElementType>([
  "title", "abstract", "introduction", "goal", "tasks", "defense_statements", "chapter",
  "chapter_conclusions", "conclusion", "bibliography", "appendices", "other"
]);

export async function buildDocumentMap(input: {
  document: ExtractedDocument;
  provider: Provider;
  model: string;
  prompt: string;
  onProgress?: (done: number, total: number, message: string) => Promise<void> | void;
  isCancelled?: () => Promise<boolean> | boolean;
}): Promise<DocumentMap> {
  if (await input.isCancelled?.()) throw new Error("Построение структуры отменено.");
  await input.onProgress?.(0, 1, "Передача всей работы в выбранную LLM");
  const userMessage = structureMessage(input.document.blocks);
  const maxChars = positiveInt(process.env.STRUCTURE_MAX_INPUT_CHARS, 2_500_000);
  if (userMessage.length > maxChars) {
    throw new Error(`Документ слишком большой для одношагового построения структуры: ${userMessage.length.toLocaleString("ru-RU")} символов при лимите ${maxChars.toLocaleString("ru-RU")}. Увеличьте STRUCTURE_MAX_INPUT_CHARS или сократите служебные приложения.`);
  }
  const definition = modelDefinition(input.model);
  const charsPerToken = positiveNumber(process.env.LLM_CHARS_PER_TOKEN, 3);
  const estimatedTokens = Math.ceil((input.prompt.length + userMessage.length) / charsPerToken);
  if (definition && estimatedTokens > Math.floor(definition.contextTokens * 0.85)) {
    throw new Error(`Оценочный объём запроса — ${estimatedTokens.toLocaleString("ru-RU")} токенов, что слишком близко к контекстному лимиту модели ${definition.label} (${definition.contextTokens.toLocaleString("ru-RU")}). Выберите DeepSeek V4 Flash, Nemotron или другую модель с контекстом 1 млн токенов.`);
  }

  const response = await askStructuredJson({
    provider: input.provider,
    model: input.model,
    systemPrompt: input.prompt,
    userMessage,
    operation: "structure",
    packets: 1,
    candidates: input.document.blocks.length
  });
  if (await input.isCancelled?.()) throw new Error("Построение структуры отменено.");

  const parsed = parseStructure(response.value, input.document.blocks);
  await input.onProgress?.(1, 1, "Структура готова к проверке пользователем");
  return {
    version: 2,
    createdAt: new Date().toISOString(),
    provider: input.provider,
    model: input.model,
    promptHash: hash(input.prompt),
    status: parsed.issues.some((issue) => issue.severity === "warning") ? "partial" : "ready",
    elements: parsed.elements,
    relations: [],
    issues: parsed.issues,
    warnings: parsed.warnings,
    usage: response.usage,
    extraction: {
      totalBlocks: input.document.blocks.length,
      processedBlocks: input.document.blocks.length,
      totalBatches: 1,
      processedBatches: 1
    },
    review: { required: true, confirmedByUser: false }
  };
}

export function mapCanBeReused(map: DocumentMap | undefined, provider: Provider, model: string, prompt: string) {
  return Boolean(map && map.version === 2 && map.provider === provider && map.model === model && map.promptHash === hash(prompt));
}

export function refreshMapElements(map: DocumentMap, blocks: DocumentBlock[]) {
  const index = new Map(blocks.map((block, position) => [block.id, position]));
  map.elements = map.elements.map((element) => materializeElement(element, blocks, index)).filter((item): item is DocumentMapElement => Boolean(item));
  map.issues = validateMap(map.elements, blocks);
  map.status = map.issues.some((item) => item.severity === "warning") ? "partial" : "ready";
  return map;
}

function structureMessage(blocks: DocumentBlock[]) {
  const content = blocks.map((block) => `BLOCK ${block.id} | ${block.location}${block.page ? ` | page=${block.page}` : ""} | type=${block.type}\n${block.text}`).join("\n\n");
  return `DOCUMENT_BLOCKS (${blocks.length}):\n\n${content}`;
}

function parseStructure(value: unknown, blocks: DocumentBlock[]) {
  const blockIndex = new Map(blocks.map((block, index) => [block.id, index]));
  const rawSections = arrayField(value, "sections");
  const warnings: string[] = [];
  const elements: DocumentMapElement[] = [];

  for (let rawIndex = 0; rawIndex < rawSections.length; rawIndex++) {
    const record = rawSections[rawIndex];
    const type = text(record.type) as DocumentElementType;
    if (!TYPES.has(type)) { warnings.push(`Секция ${rawIndex + 1} отброшена: неизвестный тип «${text(record.type)}».`); continue; }
    const startBlockId = text(record.startBlockId);
    const endBlockId = text(record.endBlockId);
    const start = blockIndex.get(startBlockId);
    const end = blockIndex.get(endBlockId);
    if (start === undefined || end === undefined || start > end) {
      warnings.push(`Секция ${rawIndex + 1} отброшена: некорректные границы ${startBlockId}…${endBlockId}.`);
      continue;
    }
    const element = materializeElement({
      id: `section-${elements.length + 1}`,
      type,
      label: text(record.label) || typeLabel(type),
      startBlockId,
      endBlockId,
      blockIds: stringArray(record.anchorBlockIds).filter((id) => blockIndex.has(id) && (blockIndex.get(id) ?? -1) >= start && (blockIndex.get(id) ?? Infinity) <= end).slice(0, 5),
      pages: [], text: "", quote: text(record.quote),
      confidence: clamp(record.confidence),
      state: record.state === "ambiguous" ? "ambiguous" : "confirmed",
      source: "llm",
      note: text(record.note) || undefined
    }, blocks, blockIndex);
    if (element) elements.push(element);
  }

  elements.sort((a, b) => (blockIndex.get(a.startBlockId) ?? 0) - (blockIndex.get(b.startBlockId) ?? 0));
  elements.forEach((element, index) => { element.id = `section-${index + 1}`; });
  const issues = [...parseIssues(value, elements), ...validateMap(elements, blocks)];
  return { elements, issues: dedupeIssues(issues), warnings };
}

function materializeElement(element: DocumentMapElement, blocks: DocumentBlock[], index: Map<string, number>) {
  const start = index.get(element.startBlockId);
  const end = index.get(element.endBlockId);
  if (start === undefined || end === undefined || start > end) return undefined;
  const range = blocks.slice(start, end + 1);
  const anchors = element.blockIds.filter((id) => index.has(id) && (index.get(id) ?? -1) >= start && (index.get(id) ?? Infinity) <= end);
  const anchorBlocks = anchors.map((id) => blocks[index.get(id)!]).filter(Boolean);
  const first = anchorBlocks[0] || range[0];
  const rangeText = range.map((block) => block.text).join(" ");
  const requestedQuote = exactQuote(element.quote, range);
  const fallbackQuote = element.type === "title" ? extractBestTitle(range, blocks)?.text || "" : element.type === "goal" ? extractGoal(rangeText) : "";
  return {
    ...element,
    blockIds: anchors.length ? anchors : [range[0].id],
    pages: [...new Set(range.map((block) => block.page).filter((page): page is number => typeof page === "number"))],
    text: compact(rangeText, 900),
    quote: compact(requestedQuote || fallbackQuote || first.text, 500),
    label: element.label.slice(0, 180)
  };
}


function exactQuote(value: string, blocks: DocumentBlock[]) {
  const requested = value.replace(/\s+/g, " ").trim();
  if (requested.length < 4) return "";
  const normalizedRequested = normalizeForMatch(requested);
  return blocks.some((block) => normalizeForMatch(block.text).includes(normalizedRequested)) ? requested : "";
}
function normalizeForMatch(value: string) { return value.toLocaleLowerCase("ru").replace(/ё/g, "е").replace(/[«»“”]/g, '"').replace(/\s+/g, " ").trim(); }

function extractGoal(value: string) {
  const normalized = value.replace(/\s+/g, " ").trim();
  const match = normalized.match(/(?:Цель(?:ю)?\s+(?:диссертационной\s+)?работы(?:\s+является|\s*[:.–-])?\s*)([^.]{15,700}\.)/iu);
  return match ? `${normalized.slice(match.index || 0, (match.index || 0) + match[0].length)}`.trim() : "";
}

function validateMap(elements: DocumentMapElement[], blocks: DocumentBlock[]): DocumentMapIssue[] {
  const issues: DocumentMapIssue[] = [];
  if (!elements.length) issues.push({ code: "empty_structure", severity: "warning", message: "LLM не вернула ни одного проверяемого диапазона.", elementIds: [] });
  if (!elements.some((item) => item.type === "introduction")) issues.push({ code: "missing_introduction", severity: "warning", message: "Не найден крупный диапазон введения.", elementIds: [] });
  if (!elements.some((item) => item.type === "chapter")) issues.push({ code: "missing_chapters", severity: "warning", message: "Не найдены главы основной части.", elementIds: [] });
  if (!elements.some((item) => item.type === "conclusion")) issues.push({ code: "missing_conclusion", severity: "warning", message: "Не найдено заключение.", elementIds: [] });
  for (const type of ["goal", "tasks", "defense_statements"] as DocumentElementType[]) {
    if (!elements.some((item) => item.type === type)) issues.push({ code: `missing_${type}`, severity: "info", message: `Не найден отдельный диапазон «${typeLabel(type)}». Проверьте введение вручную.`, elementIds: [] });
  }
  const blockIds = new Set(blocks.map((block) => block.id));
  for (const element of elements) {
    if (!blockIds.has(element.startBlockId) || !blockIds.has(element.endBlockId)) issues.push({ code: "invalid_boundaries", severity: "warning", message: `У секции «${element.label}» недействительные границы.`, elementIds: [element.id] });
    if (element.state === "ambiguous") issues.push({ code: "ambiguous_section", severity: "info", message: `Секция «${element.label}» отмечена моделью как неоднозначная.`, elementIds: [element.id] });
  }
  return issues;
}

function parseIssues(value: unknown, elements: DocumentMapElement[]) {
  const raw = arrayField(value, "issues");
  return raw.map((record): DocumentMapIssue | null => {
    const message = text(record.message);
    if (!message) return null;
    const indexes = Array.isArray(record.sectionIndexes) ? record.sectionIndexes.map(Number).filter(Number.isInteger) : [];
    return {
      code: text(record.code) || "model_issue",
      severity: record.severity === "info" ? "info" : "warning",
      message,
      elementIds: indexes.map((index) => elements[index]?.id).filter(Boolean)
    };
  }).filter((item): item is DocumentMapIssue => Boolean(item));
}

function arrayField(value: unknown, key: string): Record<string, unknown>[] { const raw = value && typeof value === "object" ? (value as Record<string, unknown>)[key] : undefined; return Array.isArray(raw) ? raw.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : []; }
function text(value: unknown) { return typeof value === "string" ? value.trim() : ""; }
function stringArray(value: unknown) { return Array.isArray(value) ? value.map(text).filter(Boolean) : []; }
function clamp(value: unknown) { const number = typeof value === "number" ? value : Number(value); return Number.isFinite(number) ? Math.max(0, Math.min(1, number)) : 0.5; }
function compact(value: string, limit: number) { const normalized = value.replace(/\s+/g, " ").trim(); return normalized.length <= limit ? normalized : `${normalized.slice(0, limit - 1)}…`; }
function hash(value: string) { return crypto.createHash("sha256").update(value).digest("hex").slice(0, 16); }
function positiveInt(value: string | undefined, fallback: number) { const number = Number(value); return Number.isFinite(number) && number > 0 ? Math.floor(number) : fallback; }
function positiveNumber(value: string | undefined, fallback: number) { const number = Number(value); return Number.isFinite(number) && number > 0 ? number : fallback; }
function dedupeIssues(items: DocumentMapIssue[]) { const seen = new Set<string>(); return items.filter((item) => { const key = `${item.code}|${item.message}`; if (seen.has(key)) return false; seen.add(key); return true; }); }
function typeLabel(type: DocumentElementType): string { const labels: Partial<Record<DocumentElementType, string>> = { title: "Название", abstract: "Аннотация", introduction: "Введение", goal: "Цель", tasks: "Задачи", defense_statements: "Положения на защиту", chapter: "Глава", chapter_conclusions: "Выводы по главе", conclusion: "Заключение", bibliography: "Библиография", appendices: "Приложения", other: "Другой фрагмент" }; return labels[type] || "Фрагмент"; }
