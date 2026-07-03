import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { CheckProfile, Rule, RuleLayer, RuleMode, RuleScope, Severity } from "../types.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const dataDir = path.resolve(__dirname, "../../rules-data");

let cache: { core: Rule[]; soft: Rule[] } | undefined;

export async function loadRuleRegistry(): Promise<{ core: Rule[]; soft: Rule[]; all: Rule[] }> {
  if (!cache) {
    const [coreText, softText] = await Promise.all([
      fs.readFile(path.join(dataDir, "core.csv"), "utf8"),
      fs.readFile(path.join(dataDir, "soft.csv"), "utf8")
    ]);
    cache = {
      core: parseCore(coreText),
      soft: parseSoft(softText)
    };
  }
  return { ...cache, all: [...cache.core, ...cache.soft] };
}

export async function rulesForProfile(profile: CheckProfile, additionalCriteria = ""): Promise<Rule[]> {
  const registry = await loadRuleRegistry();
  const base = profile === "full" ? registry.all : registry.core;
  return [...base, ...parseUserRules(additionalCriteria)];
}

function parseCore(text: string): Rule[] {
  const rows = parseDelimited(text, ",");
  return rows.slice(1).filter((row) => row.length >= 3 && row[1]?.trim()).map((row, index) => {
    const sourceNumber = row[1].trim();
    const meta = inferMeta("core", sourceNumber, row[0], row[2]);
    return {
      id: `CORE-${sourceNumber.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "")}`,
      sourceNumber,
      category: cleanGroup(row[0]),
      title: makeTitle(row[2]),
      requirement: row[2].trim(),
      correctExample: emptyToUndefined(row[3]),
      incorrectExample: emptyToUndefined(row[4]),
      sourceLabel: "ShalytoAI_csv_rules.txt",
      sourceLine: index + 2,
      layer: "core",
      ...meta,
      keywords: tokenize(`${row[0]} ${row[2]} ${row[4] || ""}`)
    };
  });
}

function parseSoft(text: string): Rule[] {
  const rows = parseDelimited(text, ";");
  return rows.slice(1).filter((row) => row.length >= 3 && row[0]?.trim()).map((row, index) => {
    const sourceNumber = row[0].trim();
    const meta = inferMeta("soft", sourceNumber, row[1], row[2]);
    return {
      id: `SOFT-${sourceNumber.padStart(3, "0")}`,
      sourceNumber,
      category: row[1].trim(),
      title: makeTitle(row[2]),
      requirement: row[2].trim(),
      incorrectExample: emptyToUndefined(row[3]),
      sourceLabel: "ShalytoAI_csv_rules(soft).txt",
      sourceLine: index + 2,
      layer: "soft",
      ...meta,
      keywords: tokenize(`${row[1]} ${row[2]} ${row[3] || ""}`)
    };
  });
}

function parseUserRules(value: string): Rule[] {
  return value.split(/\r?\n/).map((line) => line.trim()).filter((line) => line.length >= 8).slice(0, 30).map((requirement, index) => ({
    id: `USR-${String(index + 1).padStart(2, "0")}`,
    sourceNumber: String(index + 1),
    category: "Дополнительные требования",
    title: makeTitle(requirement),
    requirement,
    sourceLabel: "Пользовательские требования",
    sourceLine: index + 1,
    layer: "user" as const,
    mode: "semantic" as const,
    scope: "document" as const,
    severity: "major" as const,
    weight: 3,
    keywords: tokenize(requirement)
  }));
}

function inferMeta(layer: RuleLayer, number: string, category: string, requirement: string): Pick<Rule, "mode" | "scope" | "severity" | "weight" | "detectorId"> {
  const key = `${layer}:${number}`;
  const detectorId = DETECTOR_BY_RULE[key];
  const scope = SCOPE_OVERRIDES[key] || inferScope(category, requirement, key);
  const mode: RuleMode = MODE_OVERRIDES[key] || (detectorId ? "deterministic" : STRUCTURAL_RULES.has(key) ? "structural" : isManualScope(scope) ? "manual" : "semantic");
  const severity = inferSeverity(category, requirement, key);
  return { mode, scope, severity, weight: severityWeight(severity), detectorId };
}

function inferScope(category: string, requirement: string, key: string): RuleScope {
  const value = `${category} ${requirement}`.toLocaleLowerCase("ru");
  if (/презентац|слайд/.test(value)) return "presentation";
  if (/доклад|вопрос|совет|защит[аеу]|одежд|паспорт|говорить|не читать/.test(value) && !/положени.*защит/.test(value)) return "defense";
  if (/процесс работы|вылежаться|руководител|word|pdf|llm|итерац/.test(value)) return "process";
  if (key === "core:6.1" || key === "core:6.2" || key === "core:6.3" || /^soft:(49|50|51|52)$/.test(key)) return "title";
  if (key === "core:6.4" || key === "soft:53") return "goal";
  if (/положени.*выносим/.test(value)) return "defense_statements";
  if (/список литератур|библиограф|источник/.test(value)) return "bibliography";
  if (/формул/.test(value)) return "formula";
  if (/рисунк|таблиц|график|подрисуноч|оси/.test(value)) return "figure_table";
  if (/нумерованн|ненумерованн|пункт.*списк/.test(value)) return "list";
  if (/глав/.test(value)) return "chapter";
  return "document";
}

function inferSeverity(category: string, requirement: string, key: string): Severity {
  const value = `${category} ${requirement}`.toLocaleLowerCase("ru");
  if (/положени.*защит|научн.*новизн|прототип|цель.*диссертац|диссертац.*научн/.test(value)) return "critical";
  if (/структур|глава|вывод|заключени|внедрен|названи|ссылка.*рис|список литератур/.test(value)) return "major";
  if (/пробел|кавыч|тире|дефис|буква ё|точк|числитель|жаргон|слово|инициал|процент|дроб/.test(value)) return "minor";
  if (isManualScope(inferScope(category, requirement, key))) return "info";
  return "major";
}

function isManualScope(scope: RuleScope) {
  return scope === "presentation" || scope === "defense" || scope === "process";
}

function severityWeight(severity: Severity) {
  return ({ critical: 5, major: 3, minor: 1, info: 0.25 })[severity];
}

function makeTitle(requirement: string) {
  const compact = requirement.replace(/\s+/g, " ").trim();
  const sentence = compact.split(/[.!?](?:\s|$)/)[0] || compact;
  return sentence.length <= 92 ? sentence : `${sentence.slice(0, 89).trim()}…`;
}

function cleanGroup(value: string) {
  return value.replace(/^Группа\s+\d+\.\s*/i, "").trim();
}

function emptyToUndefined(value?: string) {
  const cleaned = value?.trim();
  return cleaned && cleaned !== "–" ? cleaned : undefined;
}

export function tokenize(value: string): string[] {
  return [...new Set(value.toLocaleLowerCase("ru")
    .replace(/ё/g, "е")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .split(/\s+/)
    .filter((token) => token.length >= 3 && !STOP_WORDS.has(token)))];
}

export function parseDelimited(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  const normalized = text.replace(/^\uFEFF/, "");
  for (let index = 0; index < normalized.length; index++) {
    const char = normalized[index];
    if (char === '"') {
      if (quoted && normalized[index + 1] === '"') { field += '"'; index += 1; }
      else quoted = !quoted;
      continue;
    }
    if (!quoted && char === delimiter) { row.push(field); field = ""; continue; }
    if (!quoted && (char === "\n" || char === "\r")) {
      if (char === "\r" && normalized[index + 1] === "\n") index += 1;
      row.push(field); field = "";
      if (row.some((item) => item.length)) rows.push(row);
      row = [];
      continue;
    }
    field += char;
  }
  row.push(field);
  if (row.some((item) => item.length)) rows.push(row);
  return rows;
}


const SCOPE_OVERRIDES: Record<string, RuleScope> = {
  "core:1.6": "defense_statements", "core:2.1": "document", "core:5.1": "document", "core:5.2": "document",
  "core:7.3": "figure_table", "core:8.3": "document", "core:14": "document", "core:15": "chapter", "core:16": "document"
};
const MODE_OVERRIDES: Record<string, RuleMode> = {
  "core:1.6": "structural", "core:2.1": "semantic", "core:5.1": "manual", "core:5.2": "manual",
  "core:7.3": "manual", "core:8.2": "semantic", "core:8.3": "semantic", "core:14": "semantic", "core:15": "semantic", "core:16": "manual"
};

const STRUCTURAL_RULES = new Set([
  "core:1.6", "core:4.1", "core:4.2", "core:4.3", "core:5.3", "core:5.4", "core:7.1", "core:7.2", "core:7.4", "core:7.5", "core:8.1", "core:8.3", "core:9.1", "core:9.4", "core:12", "core:13", "core:14", "core:15", "core:18", "core:19",
  ...[7,8,9,10,11,12,13,14,15,16,18,23,25,26,49,52,54,55,56,57,62,64,65,66,67,68,69,70,71,72,73,77,78,79,95,96,97,98,100,103,105,110,120,121,123,128,129,135,139,140,141,143,152,155,160].map((n) => `soft:${n}`)
]);

const DETECTOR_BY_RULE: Record<string, string> = {
  "core:3.1": "personal-pronouns",
  "core:3.2": "small-numerals",
  "core:3.3": "numbered-list-capitalization",
  "core:3.4": "numbered-list-ending",
  "core:3.5": "lexical-replacements",
  "core:3.6": "obvious-claims",
  "core:3.7": "praise-claims",
  "core:5.5": "dash-spacing",
  "core:5.6": "quote-consistency",
  "core:5.7": "spacing",
  "core:6.1": "title-process",
  "core:6.2": "title-vague-efficiency",
  "core:6.3": "title-length",
  "core:9.2": "bibliography-junk",
  "core:9.3": "bibliography-pages",
  "core:11.1": "yo-letter",
  "core:11.2": "forbidden-sentence-start",
  "core:11.3": "forbidden-abbreviations",
  "core:11.4": "colon-a-imenno",
  "core:11.5": "to-est",
  "core:11.6": "diminutives",
  "core:17": "see-figure",
  "core:20": "first-new",
  "soft:27": "personal-pronouns",
  "soft:28": "numbered-list-capitalization",
  "soft:29": "heading-final-period",
  "soft:30": "numbered-list-ending",
  "soft:31": "bullet-list-ending",
  "soft:32": "dash-spacing",
  "soft:34": "quote-consistency",
  "soft:35": "small-numerals",
  "soft:37": "yo-letter",
  "soft:38": "lexical-replacements",
  "soft:39": "praise-claims",
  "soft:40": "diminutives",
  "soft:42": "forbidden-sentence-start",
  "soft:44": "colon-and-to-est",
  "soft:45": "above-written",
  "soft:46": "lexical-cliches",
  "soft:47": "jargon",
  "soft:48": "first-new",
  "soft:50": "title-process",
  "soft:51": "title-length",
  "soft:53": "goal-infinitive",
  "soft:59": "python-capitalization",
  "soft:60": "initials-order",
  "soft:74": "decimal-comma",
  "soft:75": "thousands-spacing",
  "soft:76": "percent-spacing",
  "soft:121": "specialty-dot",
  "soft:124": "method-tuning",
  "soft:125": "method-behavior",
  "soft:126": "performance-verb",
  "soft:127": "model-shows",
  "soft:130": "tasks-solved",
  "soft:132": "analogovye",
  "soft:134": "roman-ending",
  "soft:137": "implemented-in-company",
  "soft:138": "yo-letter",
  "soft:144": "formula-wording",
  "soft:145": "next-respectively",
  "soft:146": "receiver-successor",
  "soft:147": "colloquial-errors",
  "soft:149": "present-work",
  "soft:152": "format-parent-word",
  "soft:155": "document-garbage",
  "soft:159": "transition-to-conclusions",
  "soft:161": "results-word"
};

const STOP_WORDS = new Set(["это", "как", "что", "для", "или", "при", "также", "так", "все", "всех", "его", "ее", "она", "они", "над", "под", "без", "после", "перед", "если", "где", "когда", "который", "которые", "данных", "работы", "работа", "главе", "глава", "раздел", "текст", "текста", "тексте", "быть", "может", "должен", "должна", "должны", "было", "были", "будет", "будут", "чтобы", "этого", "этой", "этих", "этом", "такой", "такие", "такого", "требование", "совет"]);
