import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { emptyUsage } from "../llm.js";
import { trimBlocksForElement } from "../document/semantic-ranges.js";
import type { DocumentBlock, DocumentElementType, DocumentMap, ExtractedDocument, Rule } from "../types.js";
import { collectUniqueNumberedItems } from "../document/numbered-items.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const routingPath = path.resolve(__dirname, "../../config/rule-routing.json");

export type CheckStrategy = "deterministic" | "structural" | "llm" | "manual" | "unavailable";
export type ContextSelector = DocumentElementType | "major_sections" | "title_goal" | "scientific_core" | "defense_chapters" | "defense_chapter_matrix" | "chapter_conclusions" | "conclusion_global" | "implementation_context";
interface RoutingSpec { strategy: CheckStrategy; selectors?: ContextSelector[]; exhaustive?: boolean; allowPass?: boolean; reason?: string; }
interface RoutingFile { version: number; rules: Record<string, RoutingSpec>; }
let routingCache: RoutingFile | undefined;

export type ScientificResultKind = "method" | "classification" | "benchmark" | "software" | "model" | "other";
export interface CheckFragment { id: string; type: DocumentElementType | "virtual"; label: string; blocks: DocumentBlock[]; complete: boolean; selector?: ContextSelector; resultKind?: ScientificResultKind; }
export interface RoutedRule { rule: Rule; strategy: CheckStrategy; fragmentIds: string[]; exhaustive: boolean; allowPass: boolean; reason?: string; explicit: boolean; }

export async function buildRouting(input: { document: ExtractedDocument; map: DocumentMap; rules: Rule[] }) {
  const fragments = buildFragments(input.document, input.map);
  const config = await loadRouting();
  const routed = input.rules.map((rule) => routeRule(rule, fragments, config.rules[rule.id]));
  const explicitRules = routed.filter((item) => item.explicit).length;
  const fallbackRules = routed.length - explicitRules;
  return { fragments, routed, strategy: fallbackRules === 0 ? "explicit-map" as const : explicitRules === 0 ? "scope-fallback" as const : "mixed" as const, explicitRules, fallbackRules, usage: emptyUsage() };
}

async function loadRouting() { if (!routingCache) routingCache = JSON.parse(await fs.readFile(routingPath, "utf8")) as RoutingFile; return routingCache; }

function buildFragments(document: ExtractedDocument, map: DocumentMap): CheckFragment[] {
  const blockIndex = new Map(document.blocks.map((block, index) => [block.id, index]));
  const fragments: CheckFragment[] = [];
  for (const element of map.elements) {
    const start = blockIndex.get(element.startBlockId); const end = blockIndex.get(element.endBlockId);
    if (start === undefined || end === undefined || start > end) continue;
    const blocks = trimBlocksForElement(element.type, document.blocks.slice(start, end + 1)); if (!blocks.length) continue;
    fragments.push({ id: element.id, type: element.type, selector: element.type, label: element.label, blocks, complete: element.state === "confirmed" });
  }
  const byType = (type: DocumentElementType) => fragments.filter((fragment) => fragment.type === type);
  const all = (...groups: CheckFragment[][]) => groups.flat();
  const addVirtual = (selector: ContextSelector, label: string, source: CheckFragment[], blocks: DocumentBlock[], complete = source.length > 0 && source.every((item) => item.complete)) => {
    const unique = uniqueBlocks(blocks); if (unique.length) fragments.push({ id: `virtual-${selector}`, type: "virtual", selector, label, blocks: unique, complete });
  };
  const title = byType("title"), introduction = byType("introduction"), goal = byType("goal"), tasks = byType("tasks"), defense = byType("defense_statements"), chapters = byType("chapter"), explicitConclusions = byType("chapter_conclusions"), conclusion = byType("conclusion");
  const derivedConclusions = explicitConclusions.length ? explicitConclusions : chapters.map((chapter, index) => deriveChapterConclusion(chapter, index)).filter(Boolean) as CheckFragment[];
  fragments.push(...derivedConclusions.filter((item) => !fragments.some((existing) => existing.id === item.id)));
  addVirtual("title_goal", "Название и цель", all(title, goal, introduction), [...title.flatMap((item) => item.blocks), ...goal.flatMap((item) => item.blocks)]);
  const scientificSource = all(title, introduction, goal, tasks, defense, chapters, conclusion);
  addVirtual("scientific_core", "Научное ядро работы", scientificSource, [...title.flatMap((item) => item.blocks), ...introduction.flatMap((item) => item.blocks), ...chapters.flatMap((item) => selectChapterSummary(item.blocks)), ...conclusion.flatMap((item) => item.blocks)]);
  addVirtual("defense_chapters", "Положения, аналоги и соответствующие главы", all(defense, chapters, derivedConclusions), [...defense.flatMap((item) => item.blocks), ...chapters.flatMap((item) => selectChapterEvidence(item.blocks)), ...derivedConclusions.flatMap((item) => item.blocks)]);
  const statements = splitDefenseStatements(defense);
  const targetChapters = statements.length && chapters.length > statements.length ? chapters.slice(chapters.length - statements.length) : chapters.slice(0, statements.length);
  for (let index = 0; index < Math.min(statements.length, targetChapters.length); index++) {
    const statement = statements[index]; const chapter = targetChapters[index];
    fragments.push({
      id: `virtual-defense-chapter-${index + 1}`, type: "virtual", selector: "defense_chapter_matrix",
      label: `Положение ${index + 1} ↔ ${chapter.label}`, blocks: uniqueBlocks([statement, ...chapter.blocks]),
      complete: chapter.complete && defense.every((item) => item.complete), resultKind: inferResultKind(statement.text)
    });
  }
  for (const item of derivedConclusions) if (!item.selector) item.selector = "chapter_conclusions";
  addVirtual("conclusion_global", "Цель, задачи, выводы глав и заключение", all(title, goal, tasks, defense, derivedConclusions, conclusion), [...title.flatMap((item) => item.blocks), ...goal.flatMap((item) => item.blocks), ...tasks.flatMap((item) => item.blocks), ...defense.flatMap((item) => item.blocks), ...derivedConclusions.flatMap((item) => item.blocks), ...conclusion.flatMap((item) => item.blocks)]);
  const implementationBlocks = uniqueBlocks([...document.blocks.filter((block) => /внедрен|использовани[ея]\s+результат|акт(?:а|ы|ов)?\s+внедрен|репозитор|github|gitlab|открыт(?:ое|ый)\s+по|программ(?:ное|ный)\s+средств/iu.test(block.text)), ...chapters.flatMap((item) => item.blocks.slice(0, 2)), ...conclusion.flatMap((item) => item.blocks)]);
  addVirtual("implementation_context", "Внедрение и практическое использование", all(introduction, chapters, conclusion), implementationBlocks, implementationBlocks.length > 0);
  return fragments;
}

function deriveChapterConclusion(chapter: CheckFragment, index: number): CheckFragment | undefined {
  const start = chapter.blocks.findIndex((block) => /^(?:\d+(?:\.\d+)*\.?\s*)?выводы(?:\s+по\s+главе)?\.?$/iu.test(block.text.trim()));
  if (start < 0) return undefined;
  return { id: `${chapter.id}-conclusions`, type: "chapter_conclusions", selector: "chapter_conclusions", label: `Выводы: ${chapter.label || `глава ${index + 1}`}`, blocks: chapter.blocks.slice(start), complete: chapter.complete };
}
function selectChapterSummary(blocks: DocumentBlock[]) { return uniqueBlocks([...blocks.slice(0, 4), ...blocks.filter((block) => /предложен|разработан|прототип|аналог|эксперимент|результат|вывод|сравнен|превосход|отлича/iu.test(block.text)), ...blocks.slice(-10)]).slice(0, 45); }
function selectChapterEvidence(blocks: DocumentBlock[]) { return uniqueBlocks([...blocks.slice(0, 3), ...blocks.filter((block) => /аналог|прототип|базов(?:ый|ая|ое)|сравнен|недостат|отлича|предложен|результат|эксперимент/iu.test(block.text)), ...blocks.slice(-12)]).slice(0, 55); }

function routeRule(rule: Rule, fragments: CheckFragment[], explicitSpec?: RoutingSpec): RoutedRule {
  const spec = explicitSpec || fallbackSpec(rule); const fragmentIds = spec.strategy === "llm" ? expandSelectors(spec.selectors || fallbackSelectors(rule), fragments) : [];
  return { rule, strategy: spec.strategy, fragmentIds, exhaustive: Boolean(spec.exhaustive) && fragmentIds.length > 0 && fragmentIds.every((id) => fragments.find((item) => item.id === id)?.complete), allowPass: spec.allowPass !== false, reason: spec.reason, explicit: Boolean(explicitSpec) };
}
function fallbackSpec(rule: Rule): RoutingSpec { if (rule.detectorId || rule.mode === "deterministic") return { strategy: "deterministic" }; if (rule.mode === "structural") return { strategy: "structural" }; if (rule.mode === "manual" || ["presentation", "defense", "process"].includes(rule.scope)) return { strategy: "manual", reason: "Правило требует другого артефакта или ручного наблюдения." }; return { strategy: "llm", selectors: fallbackSelectors(rule), exhaustive: rule.scope !== "document" }; }
function fallbackSelectors(rule: Rule): ContextSelector[] { if (rule.scope === "title") return ["title"]; if (rule.scope === "goal") return ["title_goal"]; if (rule.scope === "defense_statements") return ["defense_statements"]; if (rule.scope === "chapter") return ["chapter"]; if (rule.scope === "bibliography") return ["bibliography"]; return ["major_sections"]; }
function expandSelectors(selectors: ContextSelector[], fragments: CheckFragment[]) { const result: string[] = []; for (const selector of selectors) { if (selector === "defense_chapter_matrix") { result.push(...fragments.filter((item) => item.selector === "defense_chapter_matrix").map((item) => item.id)); continue; } if (selector === "major_sections") { result.push(...fragments.filter((item) => ["introduction", "chapter", "conclusion"].includes(item.type)).map((item) => item.id)); continue; } if (selector === "chapter_conclusions") { result.push(...fragments.filter((item) => item.type === "chapter_conclusions" || item.selector === "chapter_conclusions").map((item) => item.id)); continue; } const virtual = fragments.find((item) => item.selector === selector && item.type === "virtual"); if (virtual) { result.push(virtual.id); continue; } result.push(...fragments.filter((item) => item.type === selector).map((item) => item.id)); } return [...new Set(result)]; }

function splitDefenseStatements(fragments: CheckFragment[]) {
  const blocks = uniqueBlocks(fragments.flatMap((fragment) => fragment.blocks));
  return collectUniqueNumberedItems(blocks).map((item) => ({
    ...item.source,
    id: `${item.source.id}-statement-${item.number}`,
    location: `${item.source.location}, положение ${item.number}`,
    text: `${item.number}. ${item.text}`
  }));
}
function inferResultKind(text: string): ScientificResultKind {
  const normalized = text.replace(/^\s*\d+\.\s*/, "").toLocaleLowerCase("ru");
  if (/^классификаци|систематизаци/u.test(normalized)) return "classification";
  if (/^бенчмарк|^набор\s+данных|^датасет|протокол\s+оцен/u.test(normalized)) return "benchmark";
  if (/^программ(?:ный|ная)\s+(?:комплекс|система)|^фреймворк/u.test(normalized)) return "software";
  if (/^модел/u.test(normalized)) return "model";
  if (/^метод|^алгоритм|^технолог/u.test(normalized)) return "method";
  return "other";
}

function uniqueBlocks(blocks: DocumentBlock[]) { const seen = new Set<string>(); return blocks.filter((block) => !seen.has(block.id) && Boolean(seen.add(block.id))); }
