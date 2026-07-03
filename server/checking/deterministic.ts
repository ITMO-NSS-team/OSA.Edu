import { runBibliographyRule } from "./bibliography.js";
import type { DocumentBlock, Evidence, ExtractedDocument, Rule, RuleResult } from "../types.js";
import { collectUniqueNumberedItems } from "../document/numbered-items.js";

interface MatchSpec { regex: RegExp; fix?: string; reason?: string; scope?: "title" | "goal" | "bibliography" | "headings" | "all"; }

export function runDeterministic(rule: Rule, document: ExtractedDocument): RuleResult {
  const detector = rule.detectorId || DETECTOR_BY_RULE_ID[rule.id];
  if (!detector) return result(rule, "not_checked", "Для правила не назначен детерминированный детектор.", [], 0, "detector");

  if (detector === "title-length") return titleLength(rule, document);
  if (detector === "personal-pronouns") return personalPronouns(rule, document);
  if (detector === "praise-claims") return praiseClaims(rule, document);
  if (detector === "yo-letter") return yoLetter(rule, document);
  if (detector === "quote-consistency") return quoteConsistency(rule, document);
  if (detector === "numbered-list-ending") return listEnding(rule, document, true);
  if (detector === "bullet-list-ending") return listEnding(rule, document, false);
  if (detector === "numbered-list-capitalization") return listCapitalization(rule, document);
  if (detector === "small-numerals") return smallNumerals(rule, document);
  if (detector === "defense-punctuation") return defensePunctuation(rule, document);
  if (detector === "defense-symbols") return defenseSymbols(rule, document);
  if (detector === "spacing") return spacing(rule, document);
  if (detector === "dash-spacing") return dashSpacing(rule, document);
  if (["CORE-9-2", "CORE-9-3"].includes(rule.id)) return runBibliographyRule(rule, document);

  const specs = SPECS[detector];
  if (!specs) return result(rule, "not_checked", `Детектор ${detector} ещё не реализован.`, [], 0, "detector");
  return multiplePatterns(rule, document, specs);
}

function multiplePatterns(rule: Rule, document: ExtractedDocument, specs: MatchSpec[]): RuleResult {
  const evidence: Evidence[] = [];
  let chosenFix = "Исправить найденные фрагменты в соответствии с требованием.";
  let chosenReason = rule.requirement;
  for (const spec of specs) {
    const blocks = blocksForScope(document, spec.scope || scopeFromRule(rule));
    for (const block of blocks) {
      for (const match of block.text.matchAll(cloneGlobal(spec.regex))) {
        const quote = contextualQuote(block.text, match.index || 0, match[0].length);
        if (isNoisyMatch(rule.id, quote)) continue;
        evidence.push(toEvidence(block, quote));
        chosenFix = spec.fix || chosenFix;
        chosenReason = spec.reason || chosenReason;
        if (evidence.length >= 12) break;
      }
      if (evidence.length >= 12) break;
    }
    if (evidence.length >= 12) break;
  }
  return evidence.length
    ? result(rule, "violation", chosenReason, dedupeEvidence(evidence), confidenceFor(evidence.length), "detector", chosenFix)
    : result(rule, "pass", "Детерминированный поиск не обнаружил высокоуверенных совпадений с запрещённым шаблоном.", [], 0.98, "detector");
}

function titleLength(rule: Rule, document: ExtractedDocument): RuleResult {
  const title = document.fields.title;
  if (!title) return result(rule, "uncertain", "Название работы не удалось надёжно извлечь.", [], 0.2, "detector");
  const words = title.text.match(/[\p{L}\p{N}]+(?:-[\p{L}\p{N}]+)*/gu) || [];
  if (words.length <= 13) return result(rule, "pass", `В названии ${words.length} слов — не более 13.`, [], 0.98, "detector");
  return result(rule, "violation", `В названии ${words.length} слов, что превышает рекомендуемый предел 13.`, [toEvidence(title, title.text)], 0.99, "detector", "Сократить название, сохранив объект и результат работы.");
}

function personalPronouns(rule: Rule, document: ExtractedDocument): RuleResult {
  const evidence: Evidence[] = [];
  const pattern = /(?<![\p{L}\p{N}_])(?:я|мы|наш(?:а|е|и|его|ему|им|ими)?|нами|мною|мой|моя|моё|мои)(?![\p{L}\p{N}_])/giu;
  for (const block of russianNarrativeBlocks(document)) {
    if (/\bАлгоритм\s*:/iu.test(block.text) || formulaLikeBlock(block.text)) continue;
    for (const match of block.text.matchAll(pattern)) {
      const token = match[0].toLocaleLowerCase("ru");
      const prefix = block.text.slice(Math.max(0, (match.index || 0) - 24), match.index || 0);
      if ((token === "нами" || token === "мною") && /[\p{L}]\u00ad?\s*$/u.test(prefix)) continue;
      const quote = contextualQuote(block.text, match.index || 0, match[0].length);
      if (isLikelyTableContext(quote)) continue;
      evidence.push(toEvidence(block, quote));
    }
  }
  return evidence.length
    ? result(rule, "violation", rule.requirement, dedupeEvidence(evidence).slice(0, 12), confidenceFor(evidence.length), "detector", "Переформулировать безлично: «в работе предложено», «автором разработано» — если это не искажает смысл.")
    : result(rule, "pass", "В русскоязычном основном тексте не обнаружены отдельные формы «я», «мы», «наш». Составные слова и переносы PDF исключены.", [], 0.99, "detector");
}

function praiseClaims(rule: Rule, document: ExtractedDocument): RuleResult {
  const evidence: Evidence[] = [];
  const pattern = /\b(?:уникальн\p{L}*|высокоэффективн\p{L}*|совершенно\s+бесспорн\p{L}*|значительн\p{L}*\s+вклад|наглядно\s+демонстрир\p{L}*)\b/giu;
  for (const block of russianNarrativeBlocks(document)) for (const match of block.text.matchAll(cloneGlobal(pattern))) {
    const quote = contextualQuote(block.text, match.index || 0, match[0].length);
    if (/числ[оа]\s+уникальн|уникальн\p{L}*\s+(?:текстов\p{L}*\s+)?(?:запрос|значени|идентификатор|объект|элемент|запис|класс)/iu.test(quote) || isLikelyTableContext(quote)) continue;
    evidence.push(toEvidence(block, quote));
  }
  return evidence.length
    ? result(rule, "violation", rule.requirement, dedupeEvidence(evidence).slice(0, 12), confidenceFor(evidence.length), "detector", "Заменить оценку измеримым сравнением с прототипом или нейтральным утверждением.")
    : result(rule, "pass", "Необоснованные восторженные оценки не обнаружены; статистические сочетания вроде «число уникальных запросов» исключены.", [], 0.99, "detector");
}

function yoLetter(rule: Rule, document: ExtractedDocument): RuleResult {
  const candidates: Array<{ regex: RegExp; replacement: string }> = [
    { regex: /\bза\s+счет\b/giu, replacement: "за счёт" },
    { regex: /\bвсе\s+еще\b/giu, replacement: "всё ещё" },
    { regex: /\bеще\b/giu, replacement: "ещё" },
    { regex: /\bучет(?:а|е|ом)?\b/giu, replacement: "учёт…" },
    { regex: /\bобъем(?:а|е|ом|ы|ов)?\b/giu, replacement: "объём…" },
    { regex: /\bприем(?:а|е|ом|ы|ов)?\b/giu, replacement: "приём…" },
    { regex: /\bнадежн\p{L}*\b/giu, replacement: "надёжн…" },
    { regex: /\bпроведен(?:ный|ная|ное|ные|ного|ной|ному|ным|ными|ных)?\b/giu, replacement: "проведён…" },
    { regex: /\bподтвержден(?:ный|ная|ное|ные|ного|ной|ному|ным|ными|ных)?\b/giu, replacement: "подтверждён…" }
  ];
  const evidence: Evidence[] = [];
  for (const block of russianNarrativeBlocks(document)) for (const candidate of candidates) for (const match of block.text.matchAll(cloneGlobal(candidate.regex))) {
    const found = match[0];
    evidence.push(toEvidence(block, `${contextualQuote(block.text, match.index || 0, found.length)} [найдено: «${found}»; ожидается: «${candidate.replacement}»]`));
  }
  return evidence.length
    ? result(rule, "violation", "Найдены конкретные слова, в которых нормативно требуется буква «ё».", dedupeEvidence(evidence).slice(0, 12), 0.99, "detector", "Заменить указанную форму на вариант с буквой «ё».")
    : result(rule, "pass", "Конкретные словарные формы с пропущенной буквой «ё» не обнаружены.", [], 0.99, "detector");
}

function quoteConsistency(rule: Rule, document: ExtractedDocument): RuleResult {
  const variants = new Map<string, Evidence>();
  for (const block of russianNarrativeBlocks(document)) {
    const checks: Array<[string, RegExp]> = [
      ["ёлочки", /«[^»\n]{2,}»/u],
      ["английские двойные", /“[^”\n]{2,}”/u],
      ["лапки", /„[^“\n]{2,}“/u],
      ["прямые двойные", /"[^"\n]{2,}"/u]
    ];
    for (const [name, regex] of checks) {
      const match = block.text.match(regex);
      if (!match || variants.has(name)) continue;
      const index = block.text.indexOf(match[0]);
      const quote = contextualQuote(block.text, index, match[0].length);
      if (name === "прямые двойные" && isLikelyCodeOrPrompt(quote)) continue;
      variants.set(name, toEvidence(block, quote));
    }
  }
  if (variants.size <= 1) return result(rule, "pass", "В русскоязычном основном тексте не обнаружено смешения нескольких типов кавычек.", [], 0.98, "detector");
  return result(rule, "violation", `В русскоязычном основном тексте обнаружено несколько типов кавычек: ${[...variants.keys()].join(", ")}.`, [...variants.values()], 0.99, "detector", "Выбрать один тип кавычек и применить его к русскоязычному основному тексту.");
}

function listCapitalization(rule: Rule, document: ExtractedDocument): RuleResult {
  const evidence: Evidence[] = [];
  const pattern = /(?:^|\n)\s*\d{1,3}\.\s+([а-яё])/gmu;
  for (const block of russianNarrativeBlocks(document)) {
    if (/\bАлгоритм\s*:/iu.test(block.text) || formulaLikeBlock(block.text)) continue;
    for (const match of block.text.matchAll(pattern)) {
      const quote = contextualQuote(block.text, match.index || 0, match[0].length + 80);
      if (isLikelyTableContext(quote)) continue;
      evidence.push(toEvidence(block, quote));
    }
  }
  return evidence.length ? result(rule, "violation", "Нумерованный пункт начинается со строчной буквы.", dedupeEvidence(evidence).slice(0, 12), 1, "detector", "Начать пункт с прописной буквы.") : result(rule, "pass", "Высокоуверенные случаи начала нумерованного пункта со строчной буквы не обнаружены.", [], 1, "detector");
}
function listEnding(rule: Rule, document: ExtractedDocument, numbered: boolean): RuleResult {
  const evidence: Evidence[] = [];
  for (const block of russianNarrativeBlocks(document)) {
    if (/\bАлгоритм\s*:/iu.test(block.text) || formulaLikeBlock(block.text)) continue;
    if (numbered) {
      for (const item of extractNumberedItems(block.text)) if (/;$/.test(item.body)) evidence.push(toEvidence(block, item.full));
      continue;
    }
    for (const line of block.text.split(/\n+/).map((x) => x.trim()).filter(Boolean)) if (/^(?:[-–—•]|\d+\))\s+/u.test(line) && !/[.;]$/u.test(line)) evidence.push(toEvidence(block, line));
  }
  return evidence.length ? result(rule, "violation", numbered ? "Пункт нумерованного списка заканчивается точкой с запятой." : "Пункт списка не имеет требуемого завершающего знака.", dedupeEvidence(evidence).slice(0, 12), 1, "detector", "Исправить окончания пунктов.") : result(rule, "pass", "Явных нарушений окончания пунктов не обнаружено.", [], 1, "detector");
}

function smallNumerals(rule: Rule, document: ExtractedDocument): RuleResult {
  const evidence: Evidence[] = [];
  const highPrecision = [
    /(?<![\d.,])(?<![\p{L}\p{N}_])[0-9]\s+(?:рабо(?:-\s*)?т\p{L}*|публикаци\p{L}*|модел\p{L}*|метод\p{L}*|вопрос\p{L}*|конфигураци\p{L}*|бенчмарк\p{L}*|задач\p{L}*|положени\p{L}*|этап\p{L}*|принцип\p{L}*|вариант\p{L}*|категори\p{L}*|глав\p{L}*)(?![\p{L}\p{N}_])/giu,
    /(?:из\s+них|из\s+которых|а|и)\s+[0-9]\s+(?:опубликован\p{L}*|подан\p{L}*|принят\p{L}*|представлен\p{L}*|использован\p{L}*)(?![\p{L}\p{N}_])/giu
  ];
  for (const block of russianNarrativeBlocks(document)) {
    if (block.type === "formula" || block.type === "caption" || looksLikeContents(block.text)) continue;
    for (const regex of highPrecision) for (const match of block.text.matchAll(regex)) {
      const index = match.index || 0;
      const quote = contextualQuote(block.text, index, match[0].length);
      const prefix = block.text.slice(Math.max(0, index - 24), index);
      if (/(?:глав(?:а|е|ы)|раздел(?:е|а)|рисунк(?:е|а)|таблиц(?:е|ы))\s*$/iu.test(prefix) || isLikelyTableContext(quote) || /\bpass\s*\d|\d\s*pass\b/iu.test(quote)) continue;
      evidence.push(toEvidence(block, quote));
      if (evidence.length >= 12) break;
    }
    if (evidence.length >= 12) break;
  }
  return evidence.length
    ? result(rule, "violation", "В связном тексте обнаружено числительное от нуля до девяти, записанное цифрой в высокоуверенном языковом контексте.", dedupeEvidence(evidence), 1, "detector", "Записать числительное словом либо подтвердить, что это специальное обозначение.")
    : result(rule, "pass", "Высокоуверенные случаи записи числительных от нуля до девяти цифрами не обнаружены.", [], 1, "detector");
}

function defensePunctuation(rule: Rule, document: ExtractedDocument): RuleResult {
  const blocks = document.fields.defenseStatements;
  const items = collectUniqueNumberedItems(blocks);
  if (!items.length) return result(rule, "uncertain", "Положения на защиту не удалось выделить как целые нумерованные пункты.", [], 0, "detector");
  const evidence = items.filter((item) => !/^\p{Lu}/u.test(item.text) || !/\.$/u.test(item.text) || /;$/.test(item.text)).map((item) => toEvidence(item.source, `${item.number}. ${item.text}`));
  return evidence.length ? result(rule, "violation", "Найдено положение с неверной прописной буквой или завершающим знаком.", dedupeEvidence(evidence).slice(0, 12), 1, "detector", "Начать положение с прописной буквы и завершить точкой.") : result(rule, "pass", `Все ${items.length} распознанных положений начинаются с прописной буквы и заканчиваются точкой.`, [], 1, "detector");
}
function defenseSymbols(rule: Rule, document: ExtractedDocument): RuleResult {
  const items = collectUniqueNumberedItems(document.fields.defenseStatements);
  if (!items.length) return result(rule, "uncertain", "Положения на защиту не удалось выделить как целые нумерованные пункты.", [], 0, "detector");
  const evidence: Evidence[] = [];
  const pattern = /(?<![\p{L}\p{N}_])(?:[A-ZА-ЯЁ]{2,8}|Recall@\d+|NDCG@\d+|F1@?\d*|κ|τ|γ|≈|≤|≥|\b\d+[,.]\d+\b)(?![\p{L}\p{N}_])/gu;
  for (const item of items) if (pattern.test(item.text)) { evidence.push(toEvidence(item.source, `${item.number}. ${item.text}`)); pattern.lastIndex = 0; }
  return evidence.length ? result(rule, "violation", "В положениях обнаружены аббревиатуры, метрики или математические обозначения.", dedupeEvidence(evidence).slice(0, 12), 1, "detector", "Раскрыть обозначения словами.") : result(rule, "pass", "В распознанных положениях аббревиатуры и математические обозначения не обнаружены.", [], 1, "detector");
}

function extractNumberedItems(value: string) {
  const matches = [...value.matchAll(/(?:^|\s)(\d+)\.\s+(?=[А-ЯЁA-Z])/gu)]; if (!matches.length) return [];
  return matches.map((match, index) => { const start = (match.index || 0) + match[0].search(/\d/); const end = index + 1 < matches.length ? (matches[index + 1].index || value.length) : value.length; const full = value.slice(start, end).trim(); return { full, body: full.replace(/^\d+\.\s+/, "").trim() }; });
}

function looksLikeContents(value: string) { const compact = value.replace(/\s+/g, " "); return /оглавление|содержание/iu.test(compact) || (/(?:\.{3,}|(?:\.\s*){5,})/u.test(compact) && /\b(?:глава|введение|заключение|раздел)\b/iu.test(compact)); }

function blocksForScope(document: ExtractedDocument, scope: MatchSpec["scope"]) {
  if (scope === "title") return document.fields.title ? [document.fields.title] : [];
  if (scope === "goal") return document.fields.goal ? [document.fields.goal] : [];
  if (scope === "bibliography") return document.fields.bibliographyBlocks;
  if (scope === "headings") return document.blocks.filter((block) => block.type === "heading" || isActualCaption(block));
  return russianNarrativeBlocks(document);
}

function scopeFromRule(rule: Rule): MatchSpec["scope"] {
  if (rule.scope === "title") return "title";
  if (rule.scope === "goal") return "goal";
  if (rule.scope === "bibliography") return "bibliography";
  return "all";
}

function result(rule: Rule, status: RuleResult["status"], explanation: string, evidence: Evidence[], confidence: number, checkedBy: RuleResult["checkedBy"], fix?: string): RuleResult {
  return { ruleId: rule.id, status, severity: rule.severity, explanation, fix, confidence, evidence, checkedBy, evidenceStatus: evidence.length ? "verified" : "not_required" };
}

function toEvidence(block: DocumentBlock, quote: string): Evidence {
  return { quote, blockId: block.id, location: block.location, page: block.page, verified: true };
}

function contextualQuote(text: string, index: number, length: number) {
  const start = Math.max(0, index - 70);
  const end = Math.min(text.length, index + length + 110);
  return text.slice(start, end).replace(/\s+/g, " ").trim();
}

function cloneGlobal(regex: RegExp) {
  const unicodeBoundary = "(?:(?<=[\\p{L}\\p{N}_])(?![\\p{L}\\p{N}_])|(?<![\\p{L}\\p{N}_])(?=[\\p{L}\\p{N}_]))";
  const source = regex.source.replace(/\\b/g, unicodeBoundary);
  const flags = regex.flags.includes("u") ? regex.flags : `${regex.flags}u`;
  return new RegExp(source, flags.includes("g") ? flags : `${flags}g`);
}

function dedupeEvidence(items: Evidence[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = `${item.blockId}|${item.quote.toLocaleLowerCase("ru").replace(/\s+/g, " ")}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function confidenceFor(count: number) { return count > 1 ? 0.99 : 0.95; }
function firstQuoteIndex(value: string) { const indexes = ["«", "“", "„", '"'].map((char) => value.indexOf(char)).filter((index) => index >= 0); return indexes.length ? Math.min(...indexes) : 0; }

function dashSpacing(rule: Rule, document: ExtractedDocument): RuleResult {
  const candidates: Evidence[] = [];
  for (const block of russianNarrativeBlocks(document)) {
    const text = block.text;
    const patterns = [
      /(?<=\p{L})\s-\s(?=\p{L})/gu,
      /(?<=\p{L})[—–](?=\p{L})/gu,
      /(?<=\p{L})\s+[—–](?=\p{L})/gu,
      /(?<=\p{L})[—–]\s+(?=\p{L})/gu,
      /(?<=\p{L})--(?=\p{L})/gu
    ];
    for (const pattern of patterns) for (const match of text.matchAll(pattern)) {
      const index = match.index || 0;
      const quote = contextualQuote(text, index, match[0].length);
      if (isLikelyHyphenatedName(quote) || isPdfLineWrap(text, index)) continue;
      candidates.push(toEvidence(block, quote));
    }
  }
  const evidence = dedupeEvidence(candidates).slice(0, 12);
  if (!evidence.length) return result(rule, "pass", "Высокоуверенные нарушения различия тире и дефиса не обнаружены.", [], 0.98, "detector");
  if (document.sourceFormat === "pdf" || document.pages.length > 0) return result(rule, "uncertain", "В текстовом слое PDF найдены возможные нарушения оформления тире, но PDF может терять пробелы и переносить слова. Требуется визуальная проверка или исходный DOCX.", evidence, 0, "detector");
  return result(rule, "violation", "В DOCX обнаружено тире без требуемых пробелов либо дефис, использованный вместо тире.", evidence, 1, "detector", "Использовать среднее тире «–» с пробелами; дефис оставлять только внутри составных слов.");
}

function isPdfLineWrap(text: string, index: number) {
  const around = text.slice(Math.max(0, index - 35), Math.min(text.length, index + 35));
  return /[А-ЯЁа-яё]-\s*\n\s*[А-ЯЁа-яё]/u.test(around);
}
function isLikelyHyphenatedName(value: string) {
  return /(?:end-to-end|out-of-domain|ToolRet-Web|Auto-GPT|API-Bank|Sentence-BERT|Qwen3-Embedding|Post-Selection|Retrieval–Plan–Select|TF–IDF|[A-Za-z]+-[A-Za-z0-9]+|«[^»]{1,80}(?:[А-ЯЁа-яё]+–){2,}[А-ЯЁа-яё]+[^»]{0,80}»)/u.test(value);
}

function spacing(rule: Rule, document: ExtractedDocument): RuleResult {
  const initials: Evidence[] = [];
  const percents: Evidence[] = [];
  for (const block of russianNarrativeBlocks(document)) {
    for (const match of block.text.matchAll(/(?<![\p{L}\p{N}_])[А-ЯЁA-Z]\.\s?[А-ЯЁA-Z]\.(?=[А-ЯЁA-Z][а-яёa-z-])/gu)) initials.push(toEvidence(block, contextualQuote(block.text, match.index || 0, match[0].length)));
    for (const match of block.text.matchAll(/\d+(?:[,.]\d+)?%(?!\p{L})/gu)) {
      const quote = contextualQuote(block.text, match.index || 0, match[0].length);
      if (!isLikelyTableContext(quote)) percents.push(toEvidence(block, quote));
    }
  }
  if (initials.length) return result(rule, "violation", "Обнаружено написание инициалов без пробела перед фамилией.", dedupeEvidence(initials).slice(0, 12), 1, "detector", "Добавить пробел между инициалами и фамилией.");
  if (percents.length && document.pages.length) return result(rule, "uncertain", "В текстовом слое PDF знак процента местами прилегает к числу, но извлечение PDF может терять пробелы. Требуется визуальная проверка или DOCX.", dedupeEvidence(percents).slice(0, 8), 0, "detector");
  if (percents.length) return result(rule, "violation", "Обнаружено число без пробела перед знаком процента.", dedupeEvidence(percents).slice(0, 12), 1, "detector", "Добавить неразрывный пробел перед знаком процента.");
  return result(rule, "pass", "Высокоуверенные нарушения пробелов после инициалов и перед знаком процента не обнаружены.", [], 1, "detector");
}

function russianNarrativeBlocks(document: ExtractedDocument) {
  const excluded = new Set<string>();
  if (document.map) {
    const index = new Map(document.blocks.map((block, position) => [block.id, position]));
    for (const element of document.map.elements.filter((item) => item.type === "bibliography" || item.type === "appendices")) {
      const start = index.get(element.startBlockId), end = index.get(element.endBlockId);
      if (start === undefined || end === undefined || start > end) continue;
      for (const block of document.blocks.slice(start, end + 1)) excluded.add(block.id);
    }
  }
  const bibliography = new Set(document.fields.bibliographyBlocks.map((block) => block.id));
  const contentsPages = contentsPageRange(document);
  return document.blocks.filter((block) => {
    if (excluded.has(block.id) || bibliography.has(block.id) || block.type === "bibliography" || looksLikeContents(block.text) || (block.page !== undefined && contentsPages.has(block.page))) return false;
    if (isLikelyCodeOrPrompt(block.text)) return false;
    const letters = block.text.match(/\p{L}/gu) || [];
    if (!letters.length) return false;
    const cyrillic = block.text.match(/[А-ЯЁа-яё]/gu) || [];
    return cyrillic.length / letters.length >= 0.28;
  });
}

function contentsPageRange(document: ExtractedDocument) {
  const start = document.blocks.find((block) => /(?:^|\n)\s*(?:\d+\s*)?(?:оглавление|содержание)/iu.test(block.text))?.page;
  if (start === undefined) return new Set<number>();
  const end = document.blocks.find((block) => block.page !== undefined && block.page > start && /(?:^|\n)\s*(?:\d+\s*)?(?:реферат|synopsis|введение)/iu.test(block.text))?.page;
  const pages = new Set<number>();
  for (let page = start; page < (end ?? start + 1); page++) pages.add(page);
  return pages;
}
function isLikelyCodeOrPrompt(value: string) {
  return /(?:Requirements:|Rules:|Generate ONLY|Return ONLY|No commentary|```|^\s*(?:def|class|import|from\s+\w+\s+import|SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET)\b)/imu.test(value);
}
function formulaLikeBlock(value: string) {
  const symbols = value.match(/[←→∈∪{}=τ퐴-힣]/gu)?.length || 0;
  return symbols >= 4 || /(?:^|\n)\s*Алгоритм\s*:/iu.test(value);
}
function isLikelyTableContext(value: string) {
  const compact = value.replace(/\s+/g, " ");
  const numbers = compact.match(/(?<!\p{L})\d+(?:[,.]\d+)?/gu)?.length || 0;
  return /(?:Модель\s+Обслуживание|Precision\s+Recall)/iu.test(compact) || (/(?:Датасет|Конфигурация|Кодировщик|Шум\s+Поиск|Судья\s+Необходим)/iu.test(compact) && numbers >= 2);
}
function isNoisyMatch(ruleId: string, quote: string) {
  if (isLikelyCodeOrPrompt(quote)) return true;
  if (ruleId === "CORE-3-1" && isLikelyTableContext(quote)) return true;
  return false;
}
function isActualCaption(block: DocumentBlock) {
  return block.type === "caption" && /^(?:рис(?:унок)?|таблица|график)\s*\d+(?:\.\d+)?\s*(?:[.–—-]{1,2}|:)/iu.test(block.text.trim());
}

const DETECTOR_BY_RULE_ID: Record<string, string> = {
  "CORE-1-4": "defense-punctuation",
  "CORE-1-5": "defense-symbols",
  "SOFT-023": "defense-punctuation",
  "SOFT-024": "defense-symbols"
};

const SPECS: Record<string, MatchSpec[]> = {
  "personal-pronouns": [
    { regex: /\b(?:я|мы|наш(?:а|е|и|его|ему|им|ими)?|нами|мною|мой|моя|моё|мои)\b/giu, fix: "Переформулировать безлично: «в работе предложено», «автором разработано» — если это не искажает смысл." }
  ],
  "lexical-replacements": [
    { regex: /\bнужно\b/giu, fix: "Заменить «нужно» на «необходимо»." },
    { regex: /\bзначит\b/giu, fix: "Заменить «значит» на «следовательно» или перестроить предложение." },
    { regex: /\bзаключается\s+в\b/giu, fix: "Проверить замену на «состоит в»." }
  ],
  "obvious-claims": [{ regex: /\b(?:очевидно|несомненно|легко\s+видеть|хорошо\s+известно|довольно\s+очевидно)\b/giu, fix: "Убрать оценочное слово и привести обоснование или ссылку." }],
  "praise-claims": [{ regex: /\b(?:уникальн\p{L}*|высокоэффективн\p{L}*|совершенно\s+бесспорн\p{L}*|значительн\p{L}*\s+вклад|наглядно\s+демонстрир\p{L}*)\b/giu, fix: "Заменить оценку измеримым сравнением с прототипом или нейтральным утверждением." }],
  "dash-spacing": [
    { regex: /\s-\s/gu, fix: "Заменить дефис между частями предложения на среднее тире «–»." },
    { regex: /\p{L}\s*[—]\s*\p{L}/gu, fix: "Использовать единообразное среднее тире «–», отбивая его пробелами." }
  ],
  "title-process": [{ regex: /^(?:разработка|исследование|создание|проектирование|реализация|разработка\s+и\s+исследование)\b/iu, scope: "title", fix: "Начать название с существительного, обозначающего результат: «Метод…», «Алгоритм…», «Система…»." }],
  "title-vague-efficiency": [{ regex: /\bповышени[ея]\s+(?:эффективности|качества)\b/giu, scope: "title", fix: "Указать конкретный измеримый результат вместо общей формулы «повышение эффективности/качества»." }],
  "bibliography-junk": [
    { regex: /\bISBN\b/giu, scope: "bibliography" },
    { regex: /\bed\.\s+by\b/giu, scope: "bibliography" },
    { regex: /\b[A-ZА-ЯЁ][\p{L}-]+,\s+[A-ZА-ЯЁ]\./gu, scope: "bibliography", fix: "Убрать запятую между фамилией и инициалами и унифицировать формат." },
    { regex: /\s&\s/gu, scope: "bibliography", fix: "Убрать символ & и оформить авторов единообразно." }
  ],
  "bibliography-pages": [{ regex: /\bp\.\s*\d+\s*[-–—]\s*\d+\b/giu, scope: "bibliography", fix: "Для диапазона страниц англоязычной статьи использовать «pp. 12-25»." }],
  "yo-letter": [{ regex: /\b(?:за\s+счет|все\s+еще|еще|решетк(?:а|и|ой|у)|учет(?:а|е|ом)?|объем(?:а|е|ом|ы|ов)?|проведен(?:о|ы|ный|ного|ному|ным|ные|ных)?|прием(?:а|е|ом|ы|ов)?|надежн(?:ый|ая|ое|ые|ого|ых|ость|ости)|подтвержденн(?:ый|ая|ое|ые|ого|ых|ой|ыми))\b/giu, fix: "Поставить букву «ё» в конкретном найденном слове." }],
  "forbidden-sentence-start": [{ regex: /(?:^|[.!?]\s+)(?:А|Но|Так\s+как|То\s+есть|Т\.?\s*к\.|Т\.?\s*е\.)\s+/gmu, fix: "Перестроить начало предложения: например, использовать «Поскольку» или «Следовательно»." }],
  "forbidden-abbreviations": [{ regex: /\b(?:т\.?\s*е\.|т\.?\s*к\.|т\.?\s*ч\.)/giu, fix: "Раскрыть сокращение или перестроить фразу. Допустимые формы «т. д.» и «т. п.» пишутся с пробелами." }, { regex: /\bт\.[дп]\.(?!\s)/giu, fix: "Добавить пробел: «т. д.», «т. п.»." }],
  "colon-a-imenno": [{ regex: /:\s*а\s+именно\b/giu, fix: "Убрать «а именно»: двоеточие уже выполняет эту функцию." }],
  "to-est": [{ regex: /\bто\s+есть\b/giu, fix: "В пояснении заменить «то есть» на тире; в выводе — на «таким образом», если это соответствует смыслу." }],
  "colon-and-to-est": [{ regex: /:\s*а\s+именно\b/giu }, { regex: /\bто\s+есть\b/giu }],
  "diminutives": [{ regex: /\b(?:лампочка|программка|строчка|стрелочка|кнопочка|табличка)\b/giu, fix: "Использовать нейтральную форму без уменьшительного суффикса." }],
  "see-figure": [{ regex: /\bсм\.\s*(?:рис|рисунок|табл|таблицу)\.?\s*\d+/giu, fix: "Убрать «см.» и сослаться непосредственно: «на рис. 5» или «рис. 5»." }],
  "first-new": [{ regex: /\b(?:впервые|нов(?:ый|ая|ое|ые)\s+(?:метод|алгоритм|модель|подход))\b/giu, fix: "Убрать «впервые»/«новый» либо обосновать действительно пионерский характер результата." }],
  "heading-final-period": [{ regex: /\.$/gmu, scope: "headings", fix: "Убрать точку в конце названия главы, раздела, таблицы или подрисуночной подписи." }],
  "above-written": [{ regex: /\b(?:вышеизложенн\p{L}*|вышеперечисленн\p{L}*)\b/giu, fix: "Заменить на «изложенное выше» или «перечисленное выше»." }],
  "lexical-cliches": [
    { regex: /\bвидится\b/giu, fix: "Заменить на точный глагол, например «является»." },
    { regex: /\bпредставляет\s+(?:важное|практическое|теоретическое)\s+значение\b/giu, fix: "Использовать «имеет ... значение»." },
    { regex: /\bвыглядит\s+как\b/giu, fix: "Использовать «представляет собой», если это соответствует смыслу." }
  ],
  "jargon": [{ regex: /\b(?:мапать|маппинг|буст|спринт|бэклог|стейкхолдер|бейзлайн|даунтайм|дашборд|эпик(?:и|ов)?)\b/giu, fix: "Заменить профессиональный жаргон русским термином или дать пояснение при первом употреблении." }],
  "goal-infinitive": [{ regex: /\bцель(?:ю)?\s+(?:работы|исследования)?\s*(?:является|состоит\s+в|–|-|:)\s*(?:разработать|исследовать|создать|реализовать|построить|автоматизировать|повысить|улучшить)\b/giu, scope: "goal", fix: "Сформулировать цель как внешний результат существительным, а не как перечень действий." }],
  "python-capitalization": [{ regex: /\bпитон(?:а|е|ом|ы|ов)?\b/giu, fix: "Название языка программирования писать как Python." }],
  "initials-order": [{ regex: /\b[А-ЯЁA-Z]\.\s?[А-ЯЁA-Z]\.\s+[А-ЯЁA-Z][\p{L}-]+/gu, scope: "bibliography", fix: "В библиографии ставить инициалы после фамилии: «Шалыто А.А.»." }],
  "decimal-comma": [{ regex: /(?<!\d\.)(?<!\b(?:рис|табл|гл))\b\d+\.\d+\b/giu, fix: "В русскоязычном тексте использовать запятую как десятичный разделитель, если это не версия ПО, IP-адрес или код." }],
  "thousands-spacing": [{ regex: /\b[1-9]\d{3,}\b/gu, fix: "Разбить многозначное число пробелами, если это не год, номер документа или идентификатор." }],
  "percent-spacing": [{ regex: /\d+(?:[,.]\d+)?%(?!\p{L})/gu, fix: "Поставить неразрывный пробел перед знаком процента." }],
  "specialty-dot": [{ regex: /\b\d+\.\d+\.\d+\s+[А-ЯЁ]/gu, fix: "После номера научной специальности поставить точку." }],
  "method-tuning": [{ regex: /\bнастройк\p{L}*\s+метод\p{L}*\b/giu, fix: "Указать, что настраиваются параметры метода или программный модуль." }],
  "method-behavior": [{ regex: /\bповедени\p{L}*\s+метод\p{L}*\b/giu, fix: "Переформулировать через свойства, результаты или работу метода." }],
  "performance-verb": [{ regex: /\bулучшени\p{L}*\s+производительности\b/giu, fix: "Использовать «повышение производительности»." }],
  "model-shows": [{ regex: /\bмодель\s+показыва\p{L}*\b/giu, fix: "Использовать точный глагол: «обеспечивает», «даёт результат», «демонстрирует на графике»." }],
  "tasks-solved": [{ regex: /\b(?:выполнени\p{L}*|выполнить)\s+(?:этой\s+|данной\s+)?задач\p{L}*\b/giu, fix: "Задачу решают, а не выполняют." }],
  "analogovye": [{ regex: /\bаналогов(?:ое|ые|ая|ого|ых)\s+решени\p{L}*\b/giu, fix: "Заменить «аналоговые решения» на «аналогичные решения», если речь не об аналоговой технике." }],
  "roman-ending": [{ regex: /\b[IVXLCDM]+-(?:ую|ой|го|я|е|й)\b/giu, fix: "Не присоединять русское окончание к римской цифре через дефис." }],
  "implemented-in-company": [{ regex: /\bвнедр(?:ен|ена|ено|ены|ил|или)\p{L}*\s+в\s+компани(?:ю|и)\b/giu, fix: "Проверить предлог: обычно результаты внедрены «в компании», а не «в компанию»." }],
  "formula-wording": [{ regex: /\bв\s+соответствии\s+со\s+следующей\s+формулой\b/giu, fix: "Сократить до «по формуле»." }],
  "next-respectively": [{ regex: /\bследующ\p{L}*\b(?:(?![.!?]).){0,55}\bследующ\p{L}*\b/giu, fix: "Убрать повтор слова «следующий» и назвать объект прямо." }],
  "receiver-successor": [{ regex: /\bприемник(?:а|ом|у)?\s+(?:президент|руководител|директор)/giu, fix: "Использовать «преемник», если речь о продолжателе должности." }],
  "colloquial-errors": [{ regex: /\b(?:ложить|ложит|ложат|ихний|ихняя|слазить|слазя|влазить|залазить)\b/giu, fix: "Заменить просторечную или неправильную форму нормативной." }],
  "present-work": [{ regex: /\bнастоящая\s+работа\b/giu, fix: "Сократить до «работа»." }],
  "format-parent-word": [{ regex: /(?<!формат(?:ы|ов)?\s)\b(?:SVG\s+и\s+PNG|PNG\s+и\s+SVG)\b/giu, fix: "Добавить родовое слово: «форматы SVG и PNG»." }],
  "document-garbage": [{ regex: /(?:Error!|\b\d{2,}[a-z]{3,}\d{4}[a-z]+\b|####+|<undefined>|\[object Object\])/giu, fix: "Удалить технический мусор, ошибки полей и сломанные идентификаторы перед отправкой работы." }],
  "transition-to-conclusions": [{ regex: /\bперейд[её]м\s+к\s+изложению\s+выводов\b/giu, fix: "Убрать фразу-переход и сразу изложить выводы." }],
  "results-word": [{ regex: /\bитоги\s+(?:главы|раздела|работы)\b/giu, fix: "Использовать «выводы»." }]
};
