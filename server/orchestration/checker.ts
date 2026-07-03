import { applyConsistencyChecks } from "../checking/consistency.js";
import { runDeterministic } from "../checking/deterministic.js";
import { runStructural } from "../checking/structural.js";
import { trimBlocksForElement } from "../document/semantic-ranges.js";
import { extractBestTitle } from "../document/title.js";
import { askStructuredJson, emptyUsage, isFatalProviderError, mergeUsage } from "../llm.js";
import { configuredRateLimits } from "../llm/rate-limiter.js";
import { buildRouting, type CheckFragment, type RoutedRule } from "../routing/rule-router.js";
import { rulesForProfile } from "../rules/registry.js";
import type { CheckProfile, CoverageMatrixItem, CoverageMatrixRow, DocumentBlock, Evidence, ExtractedDocument, LlmUsageStats, Provider, Rule, RuleResult } from "../types.js";

interface FragmentResult extends RuleResult { fragmentId: string; }
interface RoutingStats { strategy: "explicit-map" | "scope-fallback" | "mixed"; fragments: number; checkRequests: number; explicitRules: number; fallbackRules: number; }

const ABSENCE_RULES: Record<string, string[]> = {
  "CORE-2-3": ["analogs", "prototype", "prototype_disadvantages"],
  "CORE-15": ["analogs_inside_chapter", "prototype_inside_chapter", "prototype_disadvantages_inside_chapter"],
  "CORE-8-2": ["comparison_with_prototype_in_chapter_conclusions"]
};

export async function checkDocument(input: {
  document: ExtractedDocument;
  provider: Provider;
  model: string;
  prompt: string;
  profile: CheckProfile;
  additionalCriteria: string;
  onlyRuleIds?: string[];
  onProgress?: (done: number, total: number, message: string) => Promise<void> | void;
  isCancelled?: () => Promise<boolean> | boolean;
}): Promise<{ rules: Rule[]; results: RuleResult[]; warnings: string[]; llmUsage: LlmUsageStats; routing: RoutingStats }> {
  if (!input.document.map?.review.confirmedByUser) throw new Error("Структура документа не подтверждена пользователем.");
  hydrateFieldsFromConfirmedMap(input.document);
  const allRules = await rulesForProfile(input.profile, input.additionalCriteria);
  const selected = input.onlyRuleIds?.length ? new Set(input.onlyRuleIds) : undefined;
  const rules = selected ? allRules.filter((rule) => selected.has(rule.id)) : allRules;
  const warnings: string[] = [];
  const llmUsage = emptyUsage();
  const routing = await buildRouting({ document: input.document, map: input.document.map, rules });
  mergeUsage(llmUsage, routing.usage);

  const localResults = new Map<string, RuleResult>();
  const llmRouted: RoutedRule[] = [];
  for (const routed of routing.routed) {
    if (routed.strategy === "deterministic") localResults.set(routed.rule.id, normalizeLocalResult(routed, runDeterministic(routed.rule, input.document)));
    else if (routed.strategy === "structural") localResults.set(routed.rule.id, normalizeLocalResult(routed, runStructural(routed.rule, input.document)));
    else if (routed.strategy === "manual") localResults.set(routed.rule.id, manualResult(routed.rule, routed.reason));
    else if (routed.strategy === "unavailable") localResults.set(routed.rule.id, notChecked(routed.rule, routed.reason || "Надёжная автоматическая проверка недоступна."));
    else llmRouted.push(routed);
  }

  const fragmentById = new Map(routing.fragments.map((item) => [item.id, item]));
  const assignments = new Map<string, Rule[]>();
  for (const routed of llmRouted) for (const fragmentId of routed.fragmentIds) {
    const list = assignments.get(fragmentId) || [];
    list.push(routed.rule);
    assignments.set(fragmentId, list);
  }
  const maxRules = positiveInt(process.env.RULES_PER_FRAGMENT_REQUEST, 12);
  const requests = [...assignments.entries()].flatMap(([fragmentId, fragmentRules]) => chunk(fragmentRules, maxRules).map((rulesPart) => ({ fragmentId, rules: rulesPart })));
  const rawResults: FragmentResult[] = [];
  let next = 0;
  let completed = 0;
  let stop = false;
  const workers = Math.min(configuredRateLimits(input.provider).maxConcurrent, Math.max(1, requests.length));
  const packetAttempts = positiveInt(process.env.CHECK_PACKET_MAX_ATTEMPTS, 2);

  const worker = async () => {
    while (!stop) {
      if (await input.isCancelled?.()) return;
      const current = requests[next++];
      if (!current) return;
      const fragment = fragmentById.get(current.fragmentId);
      if (!fragment) continue;
      let finalError: Error | undefined;
      for (let packetAttempt = 1; packetAttempt <= packetAttempts; packetAttempt++) {
        try {
          const response = await askStructuredJson({
            provider: input.provider,
            model: input.model,
            systemPrompt: input.prompt,
            userMessage: buildCheckMessage(input.document.map!, fragment, current.rules),
            operation: "check",
            packets: 1,
            candidates: current.rules.length
          });
          mergeUsage(llmUsage, response.usage);
          rawResults.push(...parseFragmentResults(response.value, fragment, current.rules));
          finalError = undefined;
          break;
        } catch (error) {
          finalError = error as Error;
          const attached = (error as Error & { llmUsage?: LlmUsageStats }).llmUsage;
          if (attached) mergeUsage(llmUsage, attached);
          if (isFatalProviderError(error)) { stop = true; break; }
          if (packetAttempt < packetAttempts) await sleep(600 * packetAttempt);
        }
      }
      if (finalError) {
        warnings.push(`Фрагмент «${fragment.label}» не проверен: ${finalError.message}`);
        rawResults.push(...current.rules.map((rule) => failedFragment(rule, fragment.id, finalError!.message)));
      }
      completed++;
      await input.onProgress?.(completed, Math.max(1, requests.length), `Фрагменты ${completed}/${requests.length}`);
    }
  };
  await Promise.all(Array.from({ length: workers }, () => worker()));

  const routedByRule = new Map(routing.routed.map((item) => [item.rule.id, item]));
  const initialResults = rules.map((rule) => {
    const local = localResults.get(rule.id);
    if (local) return local;
    const routed = routedByRule.get(rule.id);
    if (!routed?.fragmentIds.length) return notChecked(rule, routed?.reason || "Для правила не найден обязательный смысловой фрагмент.");
    return aggregateRule(rule, routed, rawResults.filter((item) => item.ruleId === rule.id));
  });
  const results = applyConsistencyChecks(initialResults);
  return {
    rules: allRules,
    results,
    warnings,
    llmUsage,
    routing: {
      strategy: routing.strategy,
      fragments: routing.fragments.length,
      checkRequests: requests.length,
      explicitRules: routing.explicitRules,
      fallbackRules: routing.fallbackRules
    }
  };
}

function hydrateFieldsFromConfirmedMap(document: ExtractedDocument) {
  const map = document.map;
  if (!map) return;
  const index = new Map(document.blocks.map((block, position) => [block.id, position]));
  const elements = (type: string) => map.elements.filter((item) => item.type === type);
  const elementBlocks = (type: string) => elements(type).flatMap((item) => {
    const start = index.get(item.startBlockId), end = index.get(item.endBlockId);
    return start === undefined || end === undefined || start > end ? [] : trimBlocksForElement(item.type, document.blocks.slice(start, end + 1));
  });
  const preciseBlock = (type: string) => {
    const element = elements(type)[0];
    if (!element) return undefined;
    const start = index.get(element.startBlockId), end = index.get(element.endBlockId);
    const source = document.blocks[start ?? -1];
    if (!source || start === undefined || end === undefined || start > end) return undefined;
    const range = document.blocks.slice(start, end + 1);
    if (type === "title") return extractBestTitle(range, document.blocks) || document.fields.title;
    const precise = element.quote?.trim();
    return precise ? { ...source, text: precise } : source;
  };
  const title = preciseBlock("title") || document.fields.title;
  const goal = preciseBlock("goal") || document.fields.goal;
  const tasks = elementBlocks("tasks");
  const defenseStatements = elementBlocks("defense_statements");
  const bibliographyBlocks = elementBlocks("bibliography");
  document.fields = {
    title,
    goal,
    tasks: tasks.length ? tasks : document.fields.tasks,
    defenseStatements: defenseStatements.length ? defenseStatements : document.fields.defenseStatements,
    chapterHeadings: elements("chapter").map((item) => document.blocks[index.get(item.startBlockId) ?? -1]).filter(Boolean),
    conclusionHeadings: [
      ...map.elements.filter((item) => item.type === "chapter_conclusions" || item.type === "conclusion").map((item) => document.blocks[index.get(item.startBlockId) ?? -1]).filter(Boolean),
      ...document.fields.conclusionHeadings
    ],
    bibliographyBlocks: bibliographyBlocks.length ? bibliographyBlocks : document.fields.bibliographyBlocks
  };
}

function normalizeLocalResult(routed: RoutedRule, result: RuleResult): RuleResult {
  const checked = result.status === "not_checked" ? 0 : 1;
  const coverage = { candidateCount: 1, checkedCandidateCount: checked, packetCount: 1, checkedPacketCount: checked, fraction: checked, exhaustive: checked === 1 };
  if (result.status === "pass" && !routed.allowPass) return { ...result, status: "uncertain", explanation: `${result.explanation} ${routed.reason || "Полная область не подтверждена."}`, coverage: { ...coverage, exhaustive: false } };
  return { ...result, coverage, checkedFragments: [routed.strategy] };
}

function buildCheckMessage(map: NonNullable<ExtractedDocument["map"]>, fragment: CheckFragment, rules: Rule[]) {
  const mapSummary = map.elements.map((item) => `${item.type} | ${item.label} | ${item.startBlockId}…${item.endBlockId}`).join("\n");
  const blocks = fragment.blocks.map((block) => `BLOCK ${block.id} | ${block.location}${block.page ? ` | page=${block.page}` : ""}\n${block.text}`).join("\n\n");
  const ruleText = rules.map((rule) => {
    const required = ABSENCE_RULES[rule.id];
    const absence = required ? `\nТИП ПРОВЕРКИ: отсутствие элемента. Просмотри ВСЕ ${fragment.blocks.length} блоков фрагмента. Помимо обычных полей верни absenceCheck: {complete: boolean, checkedBlockCount: number, items: [{name, status: \"found\"|\"not_found\"|\"ambiguous\", evidence: [{blockId, quote}]}]}. Обязательные элементы: ${required.join(", ")}. Статус violation допускается без цитаты только если complete=true, checkedBlockCount=${fragment.blocks.length} и хотя бы один обязательный элемент имеет status=\"not_found\".` : "";
    return `RULE ${rule.id}\nКатегория: ${rule.category}\nТребование: ${rule.requirement}\nКорректный пример: ${rule.correctExample || "—"}\nПример нарушения: ${rule.incorrectExample || "—"}${absence}`;
  }).join("\n\n");
  return `DOCUMENT_MAP:\n${mapSummary}\n\nCHECK_FRAGMENT:\nid=${fragment.id}\nlabel=${fragment.label}\ncomplete=${fragment.complete}\ntotalBlocks=${fragment.blocks.length}\n\n${blocks}\n\nRULES:\n${ruleText}`;
}

export function parseFragmentResults(value: unknown, fragment: CheckFragment, rules: Rule[]): FragmentResult[] {
  const records = Array.isArray((value as { results?: unknown })?.results) ? (value as { results: unknown[] }).results : [];
  const byId = new Map<string, Record<string, unknown>>();
  for (const item of records) if (item && typeof item === "object") byId.set(text((item as Record<string, unknown>).ruleId), item as Record<string, unknown>);
  const blockMap = new Map(fragment.blocks.map((block) => [block.id, block]));
  return rules.map((rule) => {
    const record = byId.get(rule.id);
    if (!record) return failedFragment(rule, fragment.id, "LLM не вернула результат для правила.");
    let status = parseStatus(record.status);
    const evidence = parseEvidence(record.evidence, blockMap);
    const matrix = parseCoverageMatrix(record.absenceCheck, fragment, blockMap, ABSENCE_RULES[rule.id]);
    const validAbsenceViolation = Boolean(matrix?.complete && matrix.items.some((item) => item.status === "not_found"));
    let evidenceStatus: RuleResult["evidenceStatus"] = evidence.length ? "verified" : validAbsenceViolation ? "coverage_verified" : "not_required";
    if (status === "violation" && !evidence.length && !validAbsenceViolation) { status = "uncertain"; evidenceStatus = "rejected"; }
    if (status === "pass" && (!fragment.complete || (matrix && !matrix.complete))) status = "uncertain";
    return {
      ruleId: rule.id,
      fragmentId: fragment.id,
      status,
      severity: rule.severity,
      explanation: text(record.explanation) || "Недостаточно данных для объяснения.",
      fix: text(record.fix) || undefined,
      confidence: 0,
      evidence,
      evidenceStatus,
      checkedBy: "llm",
      checkedFragments: [fragment.id],
      coverageMatrix: matrix ? [matrix] : undefined,
      findingIds: matrix ? matrix.items.filter((item) => item.status === "not_found").map((item) => `absence:${rule.id}:${fragment.id}:${item.name}`) : undefined
    };
  });
}

function parseCoverageMatrix(value: unknown, fragment: CheckFragment, blocks: Map<string, DocumentBlock>, required: string[] | undefined): CoverageMatrixRow | undefined {
  if (!required || !value || typeof value !== "object") return undefined;
  const record = value as Record<string, unknown>;
  const checkedBlocks = Number(record.checkedBlockCount);
  const totalBlocks = fragment.blocks.length;
  const rawItems = Array.isArray(record.items) ? record.items : [];
  const byName = new Map<string, CoverageMatrixItem>();
  for (const raw of rawItems) {
    if (!raw || typeof raw !== "object") continue;
    const item = raw as Record<string, unknown>;
    const name = text(item.name);
    if (!required.includes(name)) continue;
    const status = item.status === "found" || item.status === "not_found" || item.status === "ambiguous" ? item.status : "ambiguous";
    byName.set(name, { name, status, evidence: parseEvidence(item.evidence, blocks) });
  }
  const items = required.map((name) => byName.get(name) || { name, status: "ambiguous" as const, evidence: [] });
  const complete = record.complete === true && fragment.complete && Number.isFinite(checkedBlocks) && checkedBlocks >= totalBlocks && required.every((name) => byName.has(name));
  return { fragmentId: fragment.id, label: fragment.label, complete, checkedBlocks: Number.isFinite(checkedBlocks) ? Math.min(Math.max(0, checkedBlocks), totalBlocks) : 0, totalBlocks, items };
}

function aggregateRule(rule: Rule, routed: RoutedRule, items: FragmentResult[]): RuleResult {
  const checked = items.filter((item) => item.status !== "not_checked");
  const coverage = {
    candidateCount: routed.fragmentIds.length,
    checkedCandidateCount: checked.length,
    packetCount: routed.fragmentIds.length,
    checkedPacketCount: checked.length,
    fraction: routed.fragmentIds.length ? checked.length / routed.fragmentIds.length : 0,
    exhaustive: routed.exhaustive && checked.length === routed.fragmentIds.length
  };
  const matrices = checked.flatMap((item) => item.coverageMatrix || []);
  const violations = checked.filter((item) => item.status === "violation" && (item.evidence.length || item.coverageMatrix?.some((row) => row.complete && row.items.some((cell) => cell.status === "not_found"))));
  if (violations.length) return {
    ruleId: rule.id,
    status: "violation",
    severity: rule.severity,
    explanation: unique(violations.map((item) => item.explanation)).join(" "),
    fix: violations.find((item) => item.fix)?.fix,
    confidence: 0,
    evidence: dedupeEvidence(violations.flatMap((item) => item.evidence)),
    evidenceStatus: violations.some((item) => item.evidence.length) ? "verified" : "coverage_verified",
    checkedBy: "llm",
    coverage,
    checkedFragments: unique(items.map((item) => item.fragmentId)),
    coverageMatrix: matrices.length ? matrices : undefined,
    findingIds: unique(violations.flatMap((item) => item.findingIds || []))
  };
  if (!checked.length) return { ...notChecked(rule, `Не удалось проверить обязательные фрагменты: ${routed.fragmentIds.join(", ")}.`), coverage, checkedFragments: routed.fragmentIds };
  const allPass = checked.every((item) => item.status === "pass");
  const matricesComplete = !matrices.length || matrices.every((row) => row.complete && row.items.every((cell) => cell.status === "found"));
  if (allPass && coverage.exhaustive && routed.allowPass && matricesComplete) return {
    ruleId: rule.id,
    status: "pass",
    severity: rule.severity,
    explanation: `Проверена вся назначенная область (${checked.length} фрагм.); подтверждённых нарушений не найдено.`,
    confidence: 0,
    evidence: [],
    evidenceStatus: matrices.length ? "coverage_verified" : "not_required",
    checkedBy: "llm",
    coverage,
    checkedFragments: unique(items.map((item) => item.fragmentId)),
    coverageMatrix: matrices.length ? matrices : undefined
  };
  const details = unique(checked.map((item) => item.explanation)).join(" ");
  const reason = !routed.allowPass ? routed.reason : coverage.exhaustive ? "Часть ответов или ячеек матрицы осталась неопределённой." : "Проверена не вся обязательная область правила.";
  return {
    ruleId: rule.id,
    status: "uncertain",
    severity: rule.severity,
    explanation: `${details} ${reason || ""}`.trim(),
    confidence: 0,
    evidence: dedupeEvidence(checked.flatMap((item) => item.evidence)),
    evidenceStatus: checked.some((item) => item.evidenceStatus === "rejected") ? "rejected" : checked.some((item) => item.evidence.length) ? "verified" : matrices.length ? "coverage_verified" : "not_required",
    checkedBy: "llm",
    coverage,
    checkedFragments: unique(items.map((item) => item.fragmentId)),
    coverageMatrix: matrices.length ? matrices : undefined,
    findingIds: unique(checked.flatMap((item) => item.findingIds || []))
  };
}

function manualResult(rule: Rule, reason?: string): RuleResult { return { ruleId: rule.id, status: "not_applicable", severity: rule.severity, explanation: reason || "Требуется другой артефакт или ручное наблюдение.", confidence: 0, evidence: [], evidenceStatus: "not_required", checkedBy: "system" }; }
function notChecked(rule: Rule, explanation: string): RuleResult { return { ruleId: rule.id, status: "not_checked", severity: rule.severity, explanation, confidence: 0, evidence: [], evidenceStatus: "not_required", checkedBy: "system" }; }
function failedFragment(rule: Rule, fragmentId: string, explanation: string): FragmentResult { return { ...notChecked(rule, explanation), fragmentId, checkedBy: "llm", checkedFragments: [fragmentId] }; }
function parseStatus(value: unknown): RuleResult["status"] { return value === "pass" || value === "violation" || value === "uncertain" || value === "not_applicable" ? value : "uncertain"; }
function parseEvidence(value: unknown, blocks: Map<string, DocumentBlock>): Evidence[] {
  if (!Array.isArray(value)) return [];
  const result: Evidence[] = [];
  for (const item of value.slice(0, 20)) {
    if (!item || typeof item !== "object") continue;
    const record = item as Record<string, unknown>;
    const block = blocks.get(text(record.blockId));
    const quote = text(record.quote).replace(/\s+/g, " ");
    if (!block || quote.length < 4 || quote.length > 600 || !normalize(block.text).includes(normalize(quote))) continue;
    result.push({ quote, blockId: block.id, location: block.location, page: block.page, verified: true });
  }
  return dedupeEvidence(result);
}
function normalize(value: string) { return value.toLocaleLowerCase("ru").replace(/ё/g, "е").replace(/[«»“”]/g, '"').replace(/\s+/g, " ").trim(); }
function text(value: unknown) { return typeof value === "string" ? value.trim() : ""; }
function positiveInt(value: string | undefined, fallback: number) { const number = Number(value); return Number.isFinite(number) && number > 0 ? Math.floor(number) : fallback; }
function chunk<T>(items: T[], size: number) { const result: T[][] = []; for (let i = 0; i < items.length; i += size) result.push(items.slice(i, i + size)); return result; }
function unique(items: string[]) { return [...new Set(items.filter(Boolean))]; }
function dedupeEvidence(items: Evidence[]) { const seen = new Set<string>(); return items.filter((item) => { const key = `${item.blockId}|${normalize(item.quote)}`; if (seen.has(key)) return false; seen.add(key); return true; }); }
function sleep(ms: number) { return new Promise((resolve) => setTimeout(resolve, ms)); }
