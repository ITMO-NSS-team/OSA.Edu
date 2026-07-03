import type { DocumentBlock, Evidence, ExtractedDocument, Rule, RuleResult, TermFinding, TermKind } from "../types.js";

interface TermPolicy {
  kind: TermKind;
  requiresExpansion: boolean;
  requiresRussianExplanation: boolean;
}

const KNOWN: Record<string, TermPolicy> = {
  LLM: { kind: "abbreviation", requiresExpansion: true, requiresRussianExplanation: true },
  API: { kind: "abbreviation", requiresExpansion: true, requiresRussianExplanation: true },
  MCP: { kind: "protocol", requiresExpansion: true, requiresRussianExplanation: true },
  RAG: { kind: "method_name", requiresExpansion: true, requiresRussianExplanation: true },
  RPS: { kind: "method_name", requiresExpansion: true, requiresRussianExplanation: true },
  NDCG: { kind: "metric", requiresExpansion: true, requiresRussianExplanation: true },
  AC1: { kind: "metric", requiresExpansion: false, requiresRussianExplanation: false },
  GPT: { kind: "model_name", requiresExpansion: false, requiresRussianExplanation: false },
  BERT: { kind: "model_name", requiresExpansion: false, requiresRussianExplanation: false },
  BGE: { kind: "model_name", requiresExpansion: false, requiresRussianExplanation: false },
  GTE: { kind: "model_name", requiresExpansion: false, requiresRussianExplanation: false },
  ORAG: { kind: "method_name", requiresExpansion: false, requiresRussianExplanation: false },
  BM25: { kind: "method_name", requiresExpansion: false, requiresRussianExplanation: false },
  "TF–IDF": { kind: "compound_term", requiresExpansion: false, requiresRussianExplanation: false },
  "TF-IDF": { kind: "compound_term", requiresExpansion: false, requiresRussianExplanation: false },
  "RECALL@10": { kind: "metric", requiresExpansion: false, requiresRussianExplanation: false },
  "NDCG@K": { kind: "metric", requiresExpansion: false, requiresRussianExplanation: false }
};

const IGNORE = new Set(["РФ", "СССР", "ИИ", "ГОСТ", "ВКР", "ГЛАВА", "ВВЕДЕНИЕ", "ЗАКЛЮЧЕНИЕ", "СПИСОК", "РЕФЕРАТ", "SYNOPSIS"]);
const TOKEN_PATTERN = /(?<![\p{L}\p{N}_])(?:TF[–-]IDF|Recall@\d+|NDCG@[A-Z\d]+|GPT(?:-[\w.]+)?|Qwen\d[\w.-]*|MiniLM|[A-ZА-ЯЁ]{2,10}(?:\d+)?)(?![\p{L}\p{N}_])/gu;

export function runAbbreviationCheck(rule: Rule, document: ExtractedDocument): RuleResult {
  if (rule.id === "CORE-4-2" || rule.id === "SOFT-077") return headingCheck(rule, document);

  const findings = analyzeTerms(document);
  const hardProblems = findings.filter((item) => {
    if (rule.id === "CORE-4-3") return item.requiresRussianExplanation && (item.status === "missing_russian_explanation" || item.status === "missing_expansion");
    if (rule.id === "CORE-4-1" || rule.id === "SOFT-056" || rule.id === "SOFT-151") return item.requiresExpansion && item.status === "missing_expansion";
    return item.status === "missing_expansion" || item.status === "missing_russian_explanation";
  });

  const heading = rule.id === "CORE-12" ? headingTerms(document) : [];
  const evidence = dedupeEvidence([
    ...hardProblems.flatMap((item) => item.firstUse ? [item.firstUse] : []),
    ...heading.map((item) => item.evidence)
  ]).slice(0, 18);
  const findingIds = [
    ...hardProblems.map((item) => `term:${normalizeTerm(item.term)}:${rule.id === "CORE-4-3" ? "translation" : "expansion"}`),
    ...heading.map((item) => `term:${normalizeTerm(item.term)}:heading`)
  ];

  if (evidence.length) {
    const terms = [...new Set([...hardProblems.map((item) => item.term), ...heading.map((item) => item.term)])];
    const explanation = rule.id === "CORE-4-3"
      ? `Иностранные обозначения впервые используются без русского объяснения: ${terms.join(", ")}.`
      : rule.id === "CORE-12"
        ? `Обнаружены необъяснённые обозначения или обозначения в заголовках: ${terms.join(", ")}.`
        : `Обозначения впервые используются без требуемой конструкции «полный термин (обозначение)»: ${terms.join(", ")}.`;
    return {
      ruleId: rule.id, status: "violation", severity: rule.severity, explanation,
      fix: "Для обычных аббревиатур и протоколов дать русский полный термин при первом употреблении; названия моделей, метрик и методов оформлять по их типу, не смешивая их в один список.",
      confidence: 1, evidence, checkedBy: "detector", evidenceStatus: "verified",
      findingIds: [...new Set(findingIds)], termFindings: findings
    };
  }

  const review = findings.filter((item) => item.status === "review");
  if (review.length) return {
    ruleId: rule.id, status: "uncertain", severity: rule.severity,
    explanation: `Найдены обозначения неизвестного или неоднозначного типа: ${review.slice(0, 10).map((item) => item.term).join(", ")}. Они не признаны нарушением автоматически.`,
    confidence: 0, evidence: review.flatMap((item) => item.firstUse ? [item.firstUse] : []).slice(0, 10),
    checkedBy: "detector", evidenceStatus: review.some((item) => item.firstUse) ? "verified" : "not_required", termFindings: findings
  };

  return {
    ruleId: rule.id, status: "pass", severity: rule.severity,
    explanation: rule.id === "CORE-4-3" ? "Для распознанных обозначений, требующих русского объяснения, оно найдено." : "Для распознанных обозначений, требующих раскрытия, первое употребление оформлено корректно.",
    confidence: 1, evidence: [], checkedBy: "detector", evidenceStatus: "not_required", termFindings: findings
  };
}

function headingCheck(rule: Rule, document: ExtractedDocument): RuleResult {
  const hits = headingTerms(document);
  if (!hits.length) return { ruleId: rule.id, status: "pass", severity: rule.severity, explanation: "В названии работы, оглавлении и распознанных заголовках аббревиатуры не обнаружены.", confidence: 1, evidence: [], checkedBy: "detector", evidenceStatus: "not_required" };
  return {
    ruleId: rule.id, status: "violation", severity: rule.severity,
    explanation: `В заголовках обнаружены обозначения: ${[...new Set(hits.map((item) => item.term))].join(", ")}.`,
    fix: "Раскрыть термин в заголовке или заменить сокращение полным названием.",
    confidence: 1, evidence: dedupeEvidence(hits.map((item) => item.evidence)).slice(0, 15), checkedBy: "detector", evidenceStatus: "verified",
    findingIds: [...new Set(hits.map((item) => `term:${normalizeTerm(item.term)}:heading`))]
  };
}

export function analyzeTerms(document: ExtractedDocument): TermFinding[] {
  const findings: TermFinding[] = [];
  const seen = new Set<string>();
  for (const block of narrativeBlocks(document)) {
    for (const match of block.text.matchAll(TOKEN_PATTERN)) {
      const raw = canonicalTerm(match[0]);
      const key = normalizeTerm(raw);
      if (seen.has(key) || IGNORE.has(raw) || isRoman(raw)) continue;
      seen.add(key);
      const policy = classify(raw);
      const expansion = expansionAt(block.text, match.index || 0, match[0].length);
      const firstUse = toEvidence(block, contextualQuote(block.text, match.index || 0, match[0].length));
      let status: TermFinding["status"] = "ok";
      if (policy.kind === "unknown") status = "review";
      else if (policy.requiresExpansion && !expansion.expanded) status = "missing_expansion";
      else if (policy.requiresRussianExplanation && !expansion.russian) status = "missing_russian_explanation";
      findings.push({ term: raw, kind: policy.kind, firstUse, expansion: expansion.evidence ? toEvidence(block, expansion.evidence) : undefined, requiresExpansion: policy.requiresExpansion, requiresRussianExplanation: policy.requiresRussianExplanation, status });
    }
  }
  return findings;
}

function headingTerms(document: ExtractedDocument) {
  const result: Array<{ term: string; evidence: Evidence }> = [];
  for (const candidate of headingCandidates(document)) for (const match of candidate.text.matchAll(TOKEN_PATTERN)) {
    const term = canonicalTerm(match[0]);
    if (IGNORE.has(term) || isRoman(term)) continue;
    const policy = classify(term);
    if (policy.kind === "model_name" && !/^(?:LLM|MCP|API)$/u.test(term)) continue;
    result.push({ term, evidence: toEvidence(candidate.block, candidate.text) });
  }
  return result;
}

function classify(term: string): TermPolicy {
  const direct = KNOWN[term] || KNOWN[term.toLocaleUpperCase("ru")];
  if (direct) return direct;
  if (/^(?:Recall|NDCG|F1)@/iu.test(term)) return { kind: "metric", requiresExpansion: false, requiresRussianExplanation: false };
  if (/^(?:GPT|Qwen|MiniLM|BERT|BGE|GTE)/u.test(term)) return { kind: "model_name", requiresExpansion: false, requiresRussianExplanation: false };
  if (/^[A-ZА-ЯЁ]{2,5}\d?$/u.test(term)) return { kind: "unknown", requiresExpansion: false, requiresRussianExplanation: false };
  return { kind: "unknown", requiresExpansion: false, requiresRussianExplanation: false };
}

function expansionAt(text: string, index: number, tokenLength: number) {
  const before = text.slice(Math.max(0, index - 360), index);
  const after = text.slice(index + tokenLength, index + tokenLength + 50);
  const direct = before.match(/([^.!?;:\n()]{2,220})\(\s*$/u);
  const comma = before.match(/\(\s*([^()]{2,220}),\s*$/u);
  const closesParenthesis = /^\s*\)/u.test(after);
  const phrase = (comma?.[1] || direct?.[1] || "").trim();
  const words = phrase.split(/[\s–—-]+/u).filter(Boolean);
  const expanded = closesParenthesis && words.length >= 2;
  const latinTail = /(?:[A-Za-z][A-Za-z-]*\s+){1,}[A-Za-z][A-Za-z-]*$/u.test(phrase);
  const russian = expanded && /[а-яё]/iu.test(phrase) && !latinTail;
  return { expanded, russian, evidence: expanded ? phrase : "" };
}

function narrativeBlocks(document: ExtractedDocument) {
  const excluded = new Set(blocksFromMap(document, new Set(["bibliography", "appendices"])).map((block) => block.id));
  const contentsPages = contentsPageRange(document);
  return document.blocks.filter((block) => {
    if (excluded.has(block.id) || block.type === "bibliography" || looksLikeContents(block.text) || isLikelyCodeOrPrompt(block.text) || isLikelyPublicationEntry(block.text) || isAllCapsHeading(block.text) || (block.page !== undefined && contentsPages.has(block.page))) return false;
    const letters = block.text.match(/\p{L}/gu) || [];
    const cyrillic = block.text.match(/[А-ЯЁа-яё]/gu) || [];
    return letters.length > 0 && cyrillic.length / letters.length >= 0.28;
  });
}

function headingCandidates(document: ExtractedDocument) {
  const result: Array<{ block: DocumentBlock; text: string }> = [];
  if (document.fields.title) result.push({ block: document.fields.title, text: document.fields.title.text });
  for (const block of document.blocks) for (const line of block.text.split(/\n+/).map((item) => item.replace(/(?:\.\s*){4,}.*$/u, "").trim()).filter(Boolean)) {
    if (/^(?:ГЛАВА\s+\d+|\d+(?:\.\d+)+\.?\s+\p{L}|Введение|Заключение|Список\s+(?:литературы|сокращений)|Приложение\s+\d+)/iu.test(line)) result.push({ block, text: line });
  }
  return result;
}

function blocksFromMap(document: ExtractedDocument, types: Set<string>) {
  if (!document.map) return [];
  const index = new Map(document.blocks.map((block, position) => [block.id, position]));
  const blocks: DocumentBlock[] = [];
  for (const element of document.map.elements.filter((item) => types.has(item.type))) {
    const start = index.get(element.startBlockId), end = index.get(element.endBlockId);
    if (start === undefined || end === undefined || start > end) continue;
    blocks.push(...document.blocks.slice(start, end + 1));
  }
  return uniqueBlocks(blocks);
}

function contentsPageRange(document: ExtractedDocument) {
  const start = document.blocks.find((block) => /(?:^|\n)\s*(?:\d+\s*)?(?:оглавление|содержание)/iu.test(block.text))?.page;
  if (start === undefined) return new Set<number>();
  const end = document.blocks.find((block) => block.page !== undefined && block.page > start && /(?:^|\n)\s*(?:\d+\s*)?(?:реферат|synopsis|введение)/iu.test(block.text))?.page;
  const pages = new Set<number>();
  for (let page = start; page < (end ?? start + 1); page++) pages.add(page);
  return pages;
}

function canonicalTerm(value: string) { return value.replace(/TF-IDF/u, "TF–IDF"); }
function normalizeTerm(value: string) { return canonicalTerm(value).toLocaleUpperCase("ru"); }
function isRoman(value: string) { return /^(?:I|II|III|IV|V|VI|VII|VIII|IX|X)$/u.test(value); }
function looksLikeContents(value: string) { const compact = value.replace(/\s+/g, " "); return /оглавление|содержание/iu.test(compact) || (/(?:\.\s*){5,}/u.test(compact) && /\b(?:глава|введение|заключение|раздел)\b/iu.test(compact)); }
function isLikelyPublicationEntry(value: string) { return /(?:\bDOI\s*:|\/\/|\bet\s+al\.|Подано\s+на\s+(?:конференцию|рецензирование)|Submitted\s+to|\b(?:AAAI|ACM|EMNLP|GECCO|ICML|NeurIPS)\b[^.!?\n]{0,80}(?:Conference|Workshop|Companion|20\d{2}))/iu.test(value); }
function isLikelyCodeOrPrompt(value: string) { return /(?:Requirements:|Rules:|Generate ONLY|Return ONLY|No commentary|\{[a-z_][^}]*\}|```|^\s*(?:def|class|import|from|SELECT|INSERT|UPDATE)\b)/imu.test(value); }
function isAllCapsHeading(value: string) { const compact = value.replace(/[^А-ЯЁа-яёA-Za-z ]/gu, "").trim(); return compact.length >= 4 && compact.length <= 120 && compact === compact.toLocaleUpperCase("ru") && !/[а-яё]/u.test(compact); }
function contextualQuote(text: string, index: number, length: number) { return text.slice(Math.max(0, index - 80), Math.min(text.length, index + length + 160)).replace(/\s+/g, " ").trim(); }
function toEvidence(block: DocumentBlock, quote: string): Evidence { return { quote: quote.replace(/\s+/g, " ").trim(), blockId: block.id, location: block.location, page: block.page, verified: true }; }
function dedupeEvidence(items: Evidence[]) { const seen = new Set<string>(); return items.filter((item) => { const key = `${item.blockId}|${item.quote.toLocaleLowerCase("ru").replace(/\s+/g, " ")}`; if (seen.has(key)) return false; seen.add(key); return true; }); }
function uniqueBlocks(items: DocumentBlock[]) { const seen = new Set<string>(); return items.filter((item) => !seen.has(item.id) && Boolean(seen.add(item.id))); }
