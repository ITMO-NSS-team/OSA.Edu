import type { DocumentBlock, Evidence, ExtractedDocument, Rule, RuleResult } from "../types.js";

interface Entry { number?: number; raw: string; block: DocumentBlock; }
type AuthorFormat = "surname_initials" | "initials_surname" | "full_name" | "unknown";

export function runBibliographyRule(rule: Rule, document: ExtractedDocument): RuleResult {
  const entries = parseEntries(document);
  if (!entries.length) return unresolved(rule, "Список литературы не удалось разобрать на отдельные записи.");
  if (rule.id === "CORE-9-1") return bibliographyConsistency(rule, entries);
  if (rule.id === "CORE-9-2") return bibliographyJunk(rule, entries);
  if (rule.id === "CORE-9-3") return bibliographyPages(rule, entries);
  if (rule.id === "CORE-18") return initialsOrder(rule, entries);
  return unresolved(rule, "Для библиографического правила нет специализированного детектора.");
}

function bibliographyConsistency(rule: Rule, entries: Entry[]): RuleResult {
  const formats = new Map<AuthorFormat, Evidence[]>();
  for (const entry of entries) {
    const authorPart = extractAuthorPart(entry.raw);
    const format = classifyAuthorFormat(authorPart);
    if (format === "unknown") continue;
    const list = formats.get(format) || [];
    list.push(evidence(entry, compactAuthorQuote(entry.raw, authorPart)));
    formats.set(format, list);
  }
  if (!formats.size) return unresolved(rule, "Формат авторов в библиографических записях не удалось распознать.");
  if (formats.size === 1) {
    const only = [...formats.keys()][0];
    if (only === "surname_initials") return pass(rule, "В распознанных записях используется единый формат «фамилия, затем инициалы». ");
    return unresolved(rule, `Распознан единый, но не целевой формат авторов: ${formatLabel(only)}. Требуется сверка с принятым стандартом оформления.`);
  }
  const ev = dedupeEvidence([...formats.values()].flatMap((items) => items.slice(0, 2)));
  return violation(rule, `В источниках одного типа смешаны форматы авторов: ${[...formats.keys()].map(formatLabel).join(", ")}.`, ev, "Выбрать один формат авторов и применить его ко всем источникам одного типа.", ["bibliography:author-style"]);
}

function bibliographyJunk(rule: Rule, entries: Entry[]): RuleResult {
  const hits: Array<{ id: string; evidence: Evidence; label: string }> = [];
  const patterns: Array<{ id: string; label: string; regex: RegExp }> = [
    { id: "isbn", label: "ISBN", regex: /\bISBN\b/iu },
    { id: "editor", label: "ed. by / редактор", regex: /\b(?:ed\.\s+by|edited\s+by|под\s+ред\.)\b/iu },
    { id: "ampersand", label: "символ & между авторами", regex: /\s&\s/u },
    { id: "surname-comma", label: "запятая после фамилии перед инициалами", regex: /\b[A-ZА-ЯЁ][\p{L}-]+,\s+[A-ZА-ЯЁ]\./u },
    { id: "dash-separator", label: "библиографическое тире-разделитель", regex: /\s[—–]\s(?:(?:19|20)\d{2}\.|(?:Vol\.|Т\.|№|URL\s*:))/iu }
  ];
  for (const entry of entries) for (const spec of patterns) {
    const match = entry.raw.match(spec.regex);
    if (!match) continue;
    hits.push({ id: spec.id, label: spec.label, evidence: evidence(entry, contextualQuote(entry.raw, entry.raw.indexOf(match[0]), match[0].length)) });
  }
  if (!hits.length) return pass(rule, "В распознанных библиографических записях не обнаружены перечисленные правилом запрещённые элементы.");
  return violation(rule, `Обнаружены запрещённые элементы библиографии: ${[...new Set(hits.map((item) => item.label))].join(", ")}.`, dedupeEvidence(hits.map((item) => item.evidence)).slice(0, 15), "Удалить запрещённые элементы и унифицировать разделители библиографических записей.", [...new Set(hits.map((item) => `bibliography:junk:${item.id}`))]);
}

function bibliographyPages(rule: Rule, entries: Entry[]): RuleResult {
  const definite: Evidence[] = [];
  const suspicious: Evidence[] = [];
  for (const entry of entries) {
    for (const match of entry.raw.matchAll(/\bP\.\s*(\d+)\s*[-–—]\s*(\d+)\b/giu)) definite.push(evidence(entry, contextualQuote(entry.raw, match.index || 0, match[0].length)));
    for (const match of entry.raw.matchAll(/\bP\.\s*(\d{1,9})(?!\s*[-–—])/giu)) suspicious.push(evidence(entry, contextualQuote(entry.raw, match.index || 0, match[0].length)));
  }
  if (definite.length) return violation(rule, "Для диапазона страниц использовано «P.» вместо «Pp.». ", dedupeEvidence(definite), "Для диапазона использовать «Pp. 45–51».", ["bibliography:pages:p-range"]);
  if (suspicious.length) return {
    ruleId: rule.id, status: "uncertain", severity: rule.severity,
    explanation: "Обнаружены записи вида «P. число». Это может быть одной страницей или ошибочно обозначенным номером статьи; автоматически подтвердить корректность нельзя.",
    fix: "Если это номер статьи, использовать «Art.»; если диапазон — «Pp.». ", confidence: 0,
    evidence: dedupeEvidence(suspicious).slice(0, 12), checkedBy: "detector", evidenceStatus: "verified", findingIds: ["bibliography:pages:single-p"]
  };
  return pass(rule, "Неверные диапазоны страниц вида «P. 45–51» не обнаружены.");
}

function initialsOrder(rule: Rule, entries: Entry[]): RuleResult {
  const bad: Evidence[] = [];
  const ambiguous: Evidence[] = [];
  let canonical = 0;
  for (const entry of entries) {
    const authorPart = extractAuthorPart(entry.raw);
    const format = classifyAuthorFormat(authorPart);
    if (format === "surname_initials") canonical++;
    else if (format === "initials_surname") bad.push(evidence(entry, compactAuthorQuote(entry.raw, authorPart)));
    else if (format === "full_name") ambiguous.push(evidence(entry, compactAuthorQuote(entry.raw, authorPart)));
  }
  if (bad.length) return violation(rule, "В библиографии обнаружены инициалы перед фамилией.", dedupeEvidence(bad), "Переставить инициалы после фамилии.", ["bibliography:authors:initials-before-family"]);
  if (ambiguous.length) return {
    ruleId: rule.id, status: "uncertain", severity: rule.severity,
    explanation: "В ряде записей используются полные имена вместо конструкции «фамилия + инициалы». Отсутствие инициалов не позволяет считать правило выполненным.",
    fix: "Привести авторов к формату «Фамилия И. О.» либо к иному явно принятому единому стандарту.", confidence: 0,
    evidence: dedupeEvidence(ambiguous).slice(0, 12), checkedBy: "detector", evidenceStatus: "verified", findingIds: ["bibliography:authors:full-names"]
  };
  if (canonical > 0) return pass(rule, "В распознанных записях авторы оформлены как «фамилия, затем инициалы». ");
  return unresolved(rule, "Инициалы и порядок имён в библиографии не удалось надёжно распознать.");
}

function parseEntries(document: ExtractedDocument): Entry[] {
  const blocks = document.fields.bibliographyBlocks.length ? document.fields.bibliographyBlocks : document.blocks.filter((block) => block.type === "bibliography");
  const result: Entry[] = [];
  for (const block of blocks) {
    const matches = [...block.text.matchAll(/(?:^|\n|\s)(\d{1,3})\.\s+(?=[A-ZА-ЯЁ])/gmu)];
    if (!matches.length) { if (block.text.trim().length > 20) result.push({ raw: block.text.trim(), block }); continue; }
    for (let index = 0; index < matches.length; index++) {
      const match = matches[index];
      const start = (match.index || 0) + match[0].search(/\d/);
      const end = index + 1 < matches.length ? (matches[index + 1].index || block.text.length) : block.text.length;
      result.push({ number: Number(match[1]), raw: block.text.slice(start, end).trim(), block });
    }
  }
  return result;
}

function extractAuthorPart(raw: string) {
  const withoutNumber = raw.replace(/^\d+\.\s*/u, "");
  const slash = withoutNumber.match(/\/\s*([^/]{3,260}?)(?:\/\/|\.(?:\s|$))/u);
  if (slash) return slash[1].trim();
  const prefix = withoutNumber.match(/^([^.!?]{3,220}?\b(?:et\s+al\.|[A-ZА-ЯЁ]\.)[^.!?]*?)\.\s/u);
  if (prefix) return prefix[1].trim();
  const fullNames = withoutNumber.match(/^((?:[A-ZА-ЯЁ][\p{L}'’-]+\s+[A-ZА-ЯЁ][\p{L}'’-]+)(?:,\s*(?:[A-ZА-ЯЁ][\p{L}'’-]+\s+[A-ZА-ЯЁ][\p{L}'’-]+))*)\./u);
  return fullNames?.[1]?.trim() || "";
}

function classifyAuthorFormat(value: string): AuthorFormat {
  if (!value) return "unknown";
  if (/(?:^|,\s*)[A-ZА-ЯЁ](?:\.\s*){1,2}[A-ZА-ЯЁ][\p{L}'’-]+/u.test(value)) return "initials_surname";
  if (/(?:^|,\s*)[A-ZА-ЯЁ][\p{L}'’-]+\s+[A-ZА-ЯЁ]\.(?:\s*[A-ZА-ЯЁ]\.)?/u.test(value)) return "surname_initials";
  if (/(?:^|,\s*)[A-ZА-ЯЁ][\p{L}'’-]+\s+[A-ZА-ЯЁ][\p{L}'’-]+(?:,|$)/u.test(value)) return "full_name";
  return "unknown";
}

function compactAuthorQuote(raw: string, authors: string) { const start = Math.max(0, raw.indexOf(authors)); return contextualQuote(raw, start, authors.length); }
function formatLabel(value: AuthorFormat) { return ({ surname_initials: "фамилия + инициалы", initials_surname: "инициалы + фамилия", full_name: "полные имена", unknown: "нераспознанный формат" })[value]; }
function contextualQuote(text: string, index: number, length: number) { return text.slice(Math.max(0, index - 40), Math.min(text.length, index + length + 100)).replace(/\s+/g, " ").trim(); }
function evidence(entry: Entry, quote: string): Evidence { return { quote: quote.replace(/\s+/g, " ").trim(), blockId: entry.block.id, location: entry.block.location, page: entry.block.page, verified: true }; }
function dedupeEvidence(items: Evidence[]) { const seen = new Set<string>(); return items.filter((item) => { const key = `${item.blockId}|${item.quote.toLocaleLowerCase("ru").replace(/\s+/g, " ")}`; if (seen.has(key)) return false; seen.add(key); return true; }); }
function base(rule: Rule) { return { ruleId: rule.id, severity: rule.severity } as const; }
function pass(rule: Rule, explanation: string): RuleResult { return { ...base(rule), status: "pass", explanation, confidence: 1, evidence: [], checkedBy: "detector", evidenceStatus: "not_required" }; }
function unresolved(rule: Rule, explanation: string): RuleResult { return { ...base(rule), status: "uncertain", explanation, confidence: 0, evidence: [], checkedBy: "detector", evidenceStatus: "not_required" }; }
function violation(rule: Rule, explanation: string, evidenceItems: Evidence[], fix: string, findingIds: string[]): RuleResult { return { ...base(rule), status: "violation", explanation, fix, confidence: 1, evidence: evidenceItems, checkedBy: "detector", evidenceStatus: "verified", findingIds }; }
