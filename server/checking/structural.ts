import { runBibliographyRule } from "./bibliography.js";
import { runAbbreviationCheck } from "./abbreviations.js";
import type { DocumentBlock, Evidence, ExtractedDocument, Rule, RuleResult } from "../types.js";
import { collectUniqueNumberedItems } from "../document/numbered-items.js";

export function runStructural(rule: Rule, document: ExtractedDocument): RuleResult {
  if (["CORE-4-1", "CORE-4-2", "CORE-4-3", "CORE-12", "SOFT-056", "SOFT-151"].includes(rule.id)) return runAbbreviationCheck(rule, document);
  if (rule.id === "SOFT-055") return combinedAbbreviationRules(rule, document);
  if (["SOFT-077"].includes(rule.id)) return runAbbreviationCheck(rule, document);
  if (rule.id === "CORE-1-6") return positionsAndChapters(rule, document);
  if (rule.id === "SOFT-026") return defenseSection(rule, document);
  if (["CORE-5-3", "SOFT-062"].includes(rule.id)) return chaptersNewPage(rule, document);
  if (["CORE-5-4"].includes(rule.id)) return headingFormat(rule, document);
  if (rule.id === "SOFT-120") return chapterHeadingOrder(rule, document);
  if (["CORE-7-1", "SOFT-065"].includes(rule.id)) return figureReferenceOrder(rule, document);
  if (rule.id === "SOFT-064") return figureReferenceAndNoSee(rule, document);
  if (["CORE-7-2", "SOFT-066"].includes(rule.id)) return captionFormat(rule, document);
  if (["CORE-7-4", "SOFT-070"].includes(rule.id)) return formulaNumbering(rule, document);
  if (["CORE-7-5", "SOFT-072"].includes(rule.id)) return formulaExplanation(rule, document);
  if (["CORE-8-1", "SOFT-160"].includes(rule.id)) return chapterConclusions(rule, document);
  if (rule.id === "CORE-9-1") return runBibliographyRule(rule, document);
  if (rule.id === "CORE-9-4") return bibliographyReferences(rule, document);
  if (rule.id === "CORE-13") return codeExplanation(rule, document);
  if (rule.id === "CORE-18") return runBibliographyRule(rule, document);
  if (rule.id === "CORE-19") return headingAndCaptionPeriods(rule, document);
  if (rule.id === "SOFT-139") return unresolved(rule, "Смешение русских и английских аббревиатур требует смыслового сопоставления терминов; автоматический категорический вердикт не формируется.");
  const text = `${rule.category} ${rule.requirement}`.toLocaleLowerCase("ru");
  if (/аббревиатур|сокращени/.test(text)) return runAbbreviationCheck(rule, document);
  if (/вывод.*глав/.test(text)) return chapterConclusions(rule, document);
  if (/ссылк.*источник|все источники/.test(text)) return bibliographyReferences(rule, document);
  return unresolved(rule, "Для этого структурного правила нет достаточно надёжного алгоритма; автоматический вердикт не формируется.");
}

function abbreviations(rule: Rule, document: ExtractedDocument): RuleResult {
  const acronym = /(?<![\p{L}\p{N}_])(?:[A-ZА-ЯЁ]{2,10}|[A-ZА-ЯЁ][A-ZА-ЯЁ0-9-]{1,11})(?![\p{L}\p{N}_])/gu;
  const ignore = new Set(["РФ", "СССР", "ИИ", "ГОСТ", "ВКР", "ГЛАВА", "ВВЕДЕНИЕ", "ЗАКЛЮЧЕНИЕ", "СПИСОК", "РЕФЕРАТ", "SYNOPSIS"]);
  const problems = new Map<string, Evidence>();
  if (rule.id === "CORE-4-2") {
    for (const candidate of headingCandidates(document)) for (const match of candidate.text.matchAll(acronym)) {
      const token = match[0];
      if (!isAcronymToken(token, ignore)) continue;
      problems.set(`${token}|${candidate.block.id}`, toEvidence(candidate.block, candidate.text));
    }
    const evidence = dedupeEvidence([...problems.values()]);
    return evidence.length ? violation(rule, "В названии, оглавлении или заголовке обнаружены аббревиатуры.", evidence.slice(0, 15), "Раскрыть термин в заголовке или заменить аббревиатуру полным названием.") : pass(rule, "В распознанных названиях, оглавлении и заголовках аббревиатуры не обнаружены.");
  }

  const seen = new Set<string>();
  for (const block of narrativeBlocks(document)) for (const match of block.text.matchAll(acronym)) {
    const token = match[0];
    if (!isAcronymToken(token, ignore) || seen.has(token)) continue;
    if (rule.id === "CORE-4-3" && /[А-ЯЁ]/u.test(token)) continue;
    seen.add(token);
    const index = match.index || 0;
    const expansion = expansionAt(block.text, index, token.length);
    const ok = rule.id === "CORE-4-3" ? expansion.translated : expansion.expanded;
    if (!ok) problems.set(token, toEvidence(block, contextualQuote(block.text, index, token.length)));
  }
  if (rule.id === "CORE-12") {
    const headingResult = abbreviations({ ...rule, id: "CORE-4-2" }, document);
    for (const item of headingResult.evidence) problems.set(`heading|${item.blockId}|${item.quote}`, item);
  }
  const evidence = dedupeEvidence([...problems.values()]);
  if (!evidence.length) return pass(rule, rule.id === "CORE-4-3" ? "Первые употребления распознанных иностранных аббревиатур сопровождаются русской расшифровкой." : "Первые употребления распознанных аббревиатур сопровождаются полным термином.");
  const tokens = [...problems.keys()].filter((key) => !key.startsWith("heading|") && !key.includes("|")).slice(0, 12);
  const detail = tokens.length ? ` Проблемные обозначения: ${tokens.join(", ")}.` : "";
  return violation(rule, `${rule.id === "CORE-4-3" ? "Иностранная аббревиатура впервые используется без русской расшифровки." : "Аббревиатура впервые используется без конструкции «полный термин (АББРЕВИАТУРА)»."}${detail}`, evidence.slice(0, 15), "При первом употреблении дать русский полный термин и аббревиатуру в скобках; из заголовков аббревиатуры убрать.");
}

function isAcronymToken(token: string, ignore: Set<string>) {
  if (ignore.has(token) || /^(?:I|II|III|IV|V|VI|VII|VIII|IX|X)$/.test(token)) return false;
  const letters = token.replace(/[^A-ZА-ЯЁ]/gu, "");
  if (letters.length < 2) return false;
  if (/^[А-ЯЁ]+$/u.test(token) && token.length > 4) return false;
  if (/^[A-Z]+$/u.test(token) && token.length > 8) return false;
  return true;
}

function expansionAt(text: string, index: number, tokenLength: number) {
  const before = text.slice(Math.max(0, index - 360), index);
  const after = text.slice(index + tokenLength, index + tokenLength + 40);
  const direct = before.match(/([^.!?;:\n()]{2,220})\(\s*$/u);
  const comma = before.match(/\(\s*([^()]{2,220}),\s*$/u);
  const closesParenthesis = /^\s*\)/u.test(after);
  const phrase = (comma?.[1] || direct?.[1] || "").trim();
  const expanded = closesParenthesis && phrase.split(/[\s–—-]+/u).filter(Boolean).length >= 2;
  const sentenceContext = before.replace(/.*(?:[.!?;:]|\n)/su, "");
  const translated = expanded && /[а-яё]/iu.test(`${sentenceContext} ${phrase}`);
  return { expanded, translated };
}
function headingAbbreviations(rule: Rule, document: ExtractedDocument): RuleResult {
  const proxy = abbreviations({ ...rule, id: "CORE-4-2" }, document);
  return { ...proxy, ruleId: rule.id, severity: rule.severity };
}
function combinedAbbreviationRules(rule: Rule, document: ExtractedDocument): RuleResult {
  const firstUse = runAbbreviationCheck({ ...rule, id: "CORE-4-1" }, document);
  const headings = runAbbreviationCheck({ ...rule, id: "CORE-4-2" }, document);
  const evidence = dedupeEvidence([...firstUse.evidence, ...headings.evidence]);
  if (evidence.length) return violation(rule, "Обнаружена аббревиатура без корректной первой расшифровки либо аббревиатура в заголовке/оглавлении.", evidence.slice(0, 15), "При первом употреблении дать полный термин; из заголовков и оглавления аббревиатуры убрать.");
  return pass(rule, "Первые употребления распознанных аббревиатур раскрыты; в распознанных заголовках аббревиатуры не обнаружены.");
}
function defenseSection(rule: Rule, document: ExtractedDocument): RuleResult {
  const element = document.map?.elements.find((item) => item.type === "defense_statements");
  if (!element || !document.fields.defenseStatements.length) return violation(rule, "Отдельный раздел с положениями, выносимыми на защиту, не распознан.", [], "Добавить отдельный раздел с положениями на защиту.");
  return unresolved(rule, "Отдельный раздел с положениями распознан, но указание научной новизны или практической значимости внутри каждого положения требует смысловой проверки.");
}
function chapterHeadingOrder(rule: Rule, document: ExtractedDocument): RuleResult {
  const evidence: Evidence[] = [];
  for (const candidate of headingCandidates(document)) if (/^\s*\d+\s+глава\b/iu.test(candidate.text)) evidence.push(toEvidence(candidate.block, candidate.text));
  return evidence.length ? violation(rule, "Обнаружен заголовок вида «1 Глава».", evidence, "Использовать порядок «Глава 1».") : pass(rule, "Заголовки вида «1 Глава» не обнаружены.");
}
function figureReferenceAndNoSee(rule: Rule, document: ExtractedDocument): RuleResult {
  const exact: Evidence[] = [];
  for (const block of document.blocks) for (const match of block.text.matchAll(/\bсм\.\s*(?:рис|рисунок)\.?\s*\d+/giu)) exact.push(toEvidence(block, contextualQuote(block.text, match.index || 0, match[0].length)));
  const order = figureReferenceOrder({ ...rule, id: "CORE-7-1" }, document);
  const evidence = dedupeEvidence([...exact, ...(order.status === "violation" ? order.evidence : [])]);
  if (evidence.length) return violation(rule, "Обнаружена ссылка «см. рис.» либо рисунок без предшествующей ссылки.", evidence.slice(0, 15), "Сослаться на рисунок до его размещения и убрать «см.».");
  return unresolved(rule, "Явные конструкции «см. рис.» и нарушения порядка в распознанном текстовом слое не обнаружены; визуальное положение рисунка требует просмотра PDF.");
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
function looksLikeContents(value: string) { const compact = value.replace(/\s+/g, " "); return /оглавление|содержание/iu.test(compact) || (/(?:\.\s*){5,}/u.test(compact) && /\b(?:глава|введение|заключение|раздел)\b/iu.test(compact)); }

function positionsAndChapters(rule: Rule, document: ExtractedDocument): RuleResult {
  const positions = collectUniqueNumberedItems(document.fields.defenseStatements).length;
  const chapters = document.fields.chapterHeadings.length;
  if (!positions) return unresolved(rule, "Не удалось определить число положений, выносимых на защиту.");
  if (chapters < positions) return violation(rule, `Распознано положений: ${positions}; глав: ${chapters}. Отдельная содержательная глава для каждого положения по количеству невозможна.`, document.fields.chapterHeadings.map((b) => toEvidence(b, b.text)), "Сопоставить каждое положение с отдельной содержательной главой.");
  return pass(rule, `В тексте распознано ${positions} положений и ${chapters} глав: соответствие по количеству возможно. Часть правила о презентации требует отдельного файла.`);
}

function chaptersNewPage(rule: Rule, document: ExtractedDocument): RuleResult {
  if (!document.pages.length) return unresolved(rule, "Для DOCX без постраничной разметки начало глав с новой страницы проверить нельзя.");
  const evidence: Evidence[] = [];
  for (const heading of document.fields.chapterHeadings) { if (!heading.page) continue; const pageBlocks = document.blocks.filter((b) => b.page === heading.page); if (pageBlocks.findIndex((b) => b.id === heading.id) > 1) evidence.push(toEvidence(heading, heading.text)); }
  return evidence.length ? violation(rule, "Заголовок главы расположен не в начале страницы.", evidence, "Перенести начало главы на новую страницу.") : pass(rule, "Распознанные заголовки глав находятся в начале страниц.");
}
function headingFormat(rule: Rule, document: ExtractedDocument): RuleResult {
  const evidence: Evidence[] = [];
  for (const block of document.blocks.filter((item) => item.type === "heading" || isActualCaption(item))) { const text = block.text.trim(); if (/^глава\s+\d+\s+(?![.–—-])/iu.test(text) || /^\d+(?:\.\d+)+\s+(?=[А-ЯЁ])/u.test(text) || /\.$/u.test(text)) evidence.push(toEvidence(block, text)); }
  return evidence.length ? violation(rule, "Обнаружен заголовок или подпись с неверной точкой после номера либо лишней точкой в конце.", evidence.slice(0, 15), "Исправить нумерацию и убрать точку в конце названия.") : pass(rule, "Явных нарушений формата распознанных заголовков и подписей не обнаружено.");
}
function figureReferenceOrder(rule: Rule, document: ExtractedDocument): RuleResult {
  const captions = document.blocks.map((block, index) => ({ block, index, key: objectKey(block.text) })).filter((item) => item.key && isActualCaption(item.block));
  if (!captions.length) return unresolved(rule, "Подписи рисунков или таблиц не удалось распознать.");
  const evidence = captions.filter((caption) => {
    const sectionStart = nearestSectionStart(document.blocks, caption.index);
    const precedingText = document.blocks.slice(sectionStart, caption.index).map((block) => block.text).join("\n");
    return !referenceKeys(precedingText).includes(caption.key!);
  }).map((item) => toEvidence(item.block, item.block.text));
  return evidence.length ? violation(rule, "Для объекта не найдена предшествующая ссылка в пределах текущего раздела документа.", evidence.slice(0, 15), "Добавить ссылку на рисунок или таблицу до объекта.") : pass(rule, "Для распознанных подписей найдены предшествующие ссылки в соответствующих разделах.");
}

function captionFormat(rule: Rule, document: ExtractedDocument): RuleResult {
  const captions = document.blocks.filter(isActualCaption); if (!captions.length) return unresolved(rule, "Подписи объектов не распознаны.");
  const evidence = captions.filter((b) => /\.$/u.test(b.text.trim())).map((b) => toEvidence(b, b.text));
  return evidence.length ? violation(rule, "Подпись рисунка или название таблицы заканчивается точкой.", evidence.slice(0, 15), "Убрать точку в конце подписи или названия.") : unresolved(rule, "Точки в конце подписей не обнаружены, но положение подписи относительно объекта по текстовому слою не проверяется.");
}
function formulaNumbering(rule: Rule, document: ExtractedDocument): RuleResult {
  const formulas = document.blocks.filter((b) => b.type === "formula"); if (!formulas.length) return notApplicable(rule, "Формулы в извлечённом тексте не распознаны.");
  const bad: Evidence[] = []; let numbered = 0;
  for (const formula of formulas) { const context = formula.text; if (/\(\d+(?:\.\d+)*\)/u.test(context)) numbered++; if (/\(\d+(?:\.\d+)*\)\s*[.,;:]/u.test(context)) bad.push(toEvidence(formula, context.slice(0, 450))); }
  return bad.length ? violation(rule, "В том же извлечённом блоке знак препинания расположен после номера формулы.", bad, "Перенести знак перед номером формулы.") : unresolved(rule, `Распознано формул: ${formulas.length}; в тех же блоках найдено номеров: ${numbered}. Отсутствие или расположение остальных номеров требует просмотра PDF.`);
}
function formulaExplanation(rule: Rule, document: ExtractedDocument): RuleResult { const formulas = document.blocks.filter((b) => b.type === "formula"); if (!formulas.length) return notApplicable(rule, "Формулы в извлечённом тексте не распознаны."); return unresolved(rule, "Наличие слова «где», перенос строки и регистр после формул необходимо подтвердить визуально."); }
function chapterConclusions(rule: Rule, document: ExtractedDocument): RuleResult {
  const chapters = document.fields.chapterHeadings;
  if (!chapters.length) return unresolved(rule, "Заголовки глав не распознаны.");
  const evidence: Evidence[] = [];
  for (let i = 0; i < chapters.length; i++) {
    const start = document.blocks.findIndex((b) => b.id === chapters[i].id);
    const end = i + 1 < chapters.length ? document.blocks.findIndex((b) => b.id === chapters[i + 1].id) : document.blocks.length;
    const segment = document.blocks.slice(start + 1, end);
    const ci = segment.findIndex((b) => /выводы(?:\s+по\s+главе)?/iu.test(b.text));
    if (ci < 0) { evidence.push(toEvidence(chapters[i], chapters[i].text)); continue; }
    const conclusionText = segment.slice(ci + 1, ci + 8).map((b) => b.text).join(" ").replace(/\s+/g, " ").trim();
    if (!/(?:^|\s)1\.\s+(?=[А-ЯЁA-Z])/u.test(conclusionText)) {
      const quote = `${segment[ci].text} ${conclusionText.slice(0, 500)}`.trim();
      evidence.push(toEvidence(segment[ci], quote));
    }
  }
  return evidence.length ? violation(rule, "Для главы не найдены выводы, оформленные нумерованным списком.", evidence.slice(0, 15), "Представить выводы по каждой главе нумерованным списком.") : pass(rule, "Для распознанных глав найдены выводы с нумерованными пунктами.");
}

function bibliographyReferences(rule: Rule, document: ExtractedDocument): RuleResult {
  const entries = document.fields.bibliographyBlocks;
  if (!entries.length) return unresolved(rule, "Список литературы не удалось распознать.");
  const nums = new Set<number>();
  for (const b of entries) for (const m of b.text.matchAll(/(?:^|\n|\s)(\d{1,3})[.)]\s+(?=[А-ЯЁA-Z])/gmu)) nums.add(Number(m[1]));
  if (!nums.size) return unresolved(rule, "Нумерацию библиографии не удалось распознать.");
  const bibIds = new Set(entries.map((b) => b.id));
  const cited = new Set<number>();
  for (const b of document.blocks) if (!bibIds.has(b.id)) for (const br of b.text.matchAll(/\[([^\]]+)\]/gu)) for (const n of citationNumbers(br[1])) cited.add(n);
  const missing = [...nums].filter((n) => !cited.has(n));
  if (!missing.length) return pass(rule, "Для всех распознанных источников найдены ссылки в основном тексте, включая номера внутри диапазонов.");
  const evidence = entries.filter((b) => missing.some((n) => new RegExp(`(?:^|\\s)${n}[.)]\\s+`, "mu").test(b.text))).slice(0, 15).map((b) => toEvidence(b, b.text.slice(0, 450)));
  return violation(rule, `Не найдены ссылки на источники: ${missing.slice(0, 25).join(", ")}.`, evidence, "Добавить ссылки либо удалить неиспользованные записи.");
}

function codeExplanation(rule: Rule, document: ExtractedDocument): RuleResult {
  const code = document.blocks.filter((b) => isCodeBlock(b.text));
  if (!code.length) return notApplicable(rule, "Фрагменты программного кода не обнаружены.");
  const evidence = code.filter((b) => {
    const i = document.blocks.findIndex((x) => x.id === b.id);
    return !/(?:фрагмент|код|листинг|реализ|выполня|алгоритм)/iu.test(document.blocks.slice(Math.max(0, i - 3), i).map((x) => x.text).join(" "));
  }).map((b) => toEvidence(b, b.text.slice(0, 400)));
  return evidence.length ? violation(rule, "Для фрагмента кода не найдено предшествующее описание.", evidence, "Перед кодом объяснить его назначение.") : pass(rule, "Для распознанных фрагментов кода найдено описание.");
}

function initialsOrder(rule: Rule, document: ExtractedDocument): RuleResult { const evidence: Evidence[] = []; for (const b of document.fields.bibliographyBlocks) for (const m of b.text.matchAll(/(?<![\p{L}\p{N}_])[А-ЯЁA-Z]\.\s*[А-ЯЁA-Z]\.\s+[А-ЯЁA-Z][\p{L}-]+/gu)) evidence.push(toEvidence(b, contextualQuote(b.text, m.index || 0, m[0].length))); return evidence.length ? violation(rule, "В библиографии обнаружены инициалы перед фамилией.", evidence, "Переставить инициалы после фамилии.") : pass(rule, "Инициалы перед фамилиями не обнаружены."); }
function headingAndCaptionPeriods(rule: Rule, document: ExtractedDocument): RuleResult { const evidence = document.blocks.filter((b) => (b.type === "heading" || isActualCaption(b)) && /\.$/u.test(b.text.trim())).map((b) => toEvidence(b, b.text)); return evidence.length ? violation(rule, "В конце заголовка или подписи стоит точка.", evidence.slice(0, 15), "Убрать точку.") : pass(rule, "В распознанных заголовках и подписях лишние точки не обнаружены."); }
function dedupeEvidence(items: Evidence[]) { const seen = new Set<string>(); return items.filter((item) => { const key = `${item.blockId}|${item.quote.toLocaleLowerCase("ru").replace(/\s+/g, " ").trim()}`; if (seen.has(key)) return false; seen.add(key); return true; }); }
function uniqueBlocks(items: DocumentBlock[]) { const seen = new Set<string>(); return items.filter((item) => !seen.has(item.id) && Boolean(seen.add(item.id))); }
function objectKey(text: string) { const m = text.trim().match(/^(рис(?:унок)?|табл(?:ица)?)\.?\s*(\d+(?:\.\d+)?)/iu); return m ? `${m[1].toLocaleLowerCase("ru").startsWith("рис") ? "рис" : "табл"}:${m[2]}` : undefined; }
function referenceKeys(text: string) {
  const normalized = text.replace(/([А-ЯЁа-яё])(?:-|\u00ad)\s*\n\s*([А-ЯЁа-яё])/gu, "$1$2");
  const keys = [...normalized.matchAll(/(?<![\p{L}\p{N}_])(?:рис\.?|рисунок|рисунк(?:е|а|у|ом)|табл\.?|таблиц(?:а|е|ы|у|ей)|figure|fig\.?|table)\s*(\d+(?:\.\d+)?)/giu)]
    .map((match) => `${/(?:рис|fig|figure)/iu.test(match[0]) ? "рис" : "табл"}:${match[1]}`);
  for (const match of normalized.matchAll(/(?<![\p{L}\p{N}_])(рисунках|таблицах|figures|tables)\s+(\d+(?:\.\d+)?)(?:\s*(?:,|и|and|–|-)\s*(\d+(?:\.\d+)?))?/giu)) {
    const prefix = /(?:рис|figure)/iu.test(match[1]) ? "рис" : "табл";
    keys.push(`${prefix}:${match[2]}`);
    if (match[3]) keys.push(`${prefix}:${match[3]}`);
  }
  return [...new Set(keys)];
}
function nearestSectionStart(blocks: DocumentBlock[], before: number) {
  for (let index = before - 1; index >= 0; index--) {
    const text = blocks[index].text.replace(/^\s*\d{1,3}\s*\n/u, "").trim();
    if (/^(?:Реферат|Synopsis|Введение|ГЛАВА\s+\d+|Заключение|Приложение\b)/iu.test(text)) return index;
  }
  return 0;
}
function citationNumbers(value: string) {
  const result = new Set<number>();
  const normalized = value.replace(/[−—]/g, "–");
  for (const match of normalized.matchAll(/(\d{1,3})\s*[–-]\s*(\d{1,3})/gu)) {
    const from = Number(match[1]), to = Number(match[2]);
    if (to >= from && to - from <= 200) for (let n = from; n <= to; n++) result.add(n);
  }
  const withoutRanges = normalized.replace(/\d{1,3}\s*[–-]\s*\d{1,3}/gu, " ");
  for (const token of withoutRanges.match(/\d{1,3}/g) || []) result.add(Number(token));
  return [...result];
}
function isCodeBlock(value: string) {
  if (/```/u.test(value)) return true;
  const lines = value.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const statementLines = lines.filter((line) => /^(?:def\s+\w+\s*\(|class\s+\w+|import\s+\w+|from\s+\w+\s+import|function\s+\w+\s*\(|(?:SELECT|INSERT|UPDATE|DELETE)\b)/u.test(line)).length;
  const xmlLines = lines.filter((line) => /^<\/?[A-Za-z][^>]*>/.test(line)).length;
  const syntax = value.match(/[{};]|=>|:=|==|\b(?:const|let|var)\s+\w+/gu)?.length || 0;
  return statementLines >= 2 || xmlLines >= 3 || (statementLines >= 1 && syntax >= 3);
}

function isActualCaption(block: DocumentBlock) { return block.type === "caption" && /^(?:рис(?:унок)?|таблица|график)\s*\d+(?:\.\d+)?\s*(?:[.–—-]{1,2}|:)/iu.test(block.text.trim()); }
function contentsPageRange(document: ExtractedDocument) { const start = document.blocks.find((block) => /(?:^|\n)\s*(?:\d+\s*)?(?:оглавление|содержание)/iu.test(block.text))?.page; if (start === undefined) return new Set<number>(); const end = document.blocks.find((block) => block.page !== undefined && block.page > start && /(?:^|\n)\s*(?:\d+\s*)?(?:реферат|synopsis|введение)/iu.test(block.text))?.page; const pages = new Set<number>(); for (let page = start; page < (end ?? start + 1); page++) pages.add(page); return pages; }
function isLikelyPublicationEntry(value: string) {
  return /(?:\bDOI\s*:|\/\/|\bet\s+al\.|Подано\s+на\s+(?:конференцию|рецензирование)|Submitted\s+to|\b(?:AAAI|ACM|EMNLP|GECCO|ICML|NeurIPS)\b[^.!?\n]{0,80}(?:Conference|Workshop|Companion|20\d{2}))/iu.test(value);
}
function isLikelyCodeOrPrompt(value: string) { return /(?:Requirements:|Rules:|Generate ONLY|Return ONLY|No commentary|\{[a-z_][^}]*\}|```|^\s*(?:def|class|import|from|SELECT|INSERT|UPDATE)\b)/imu.test(value); }
function isAllCapsHeading(value: string) { const compact = value.replace(/[^А-ЯЁа-яёA-Za-z ]/gu, "").trim(); return compact.length >= 4 && compact.length <= 120 && compact === compact.toLocaleUpperCase("ru") && !/[а-яё]/u.test(compact); }
function contextualQuote(text: string, index: number, length: number) { return text.slice(Math.max(0, index - 80), Math.min(text.length, index + length + 140)).replace(/\s+/g, " ").trim(); }
function toEvidence(block: DocumentBlock, quote: string): Evidence { return { quote: quote.replace(/\s+/g, " ").trim(), blockId: block.id, location: block.location, page: block.page, verified: true }; }
function base(rule: Rule) { return { ruleId: rule.id, severity: rule.severity } as const; }
function pass(rule: Rule, explanation: string): RuleResult { return { ...base(rule), status: "pass", explanation, confidence: 1, evidence: [], checkedBy: "detector", evidenceStatus: "not_required" }; }
function violation(rule: Rule, explanation: string, evidence: Evidence[], fix: string): RuleResult { return { ...base(rule), status: "violation", explanation, fix, confidence: 1, evidence, checkedBy: "detector", evidenceStatus: "verified" }; }
function unresolved(rule: Rule, explanation: string): RuleResult { return { ...base(rule), status: "uncertain", explanation, confidence: 0, evidence: [], checkedBy: "detector", evidenceStatus: "not_required" }; }
function notApplicable(rule: Rule, explanation: string): RuleResult { return { ...base(rule), status: "not_applicable", explanation, confidence: 0, evidence: [], checkedBy: "detector", evidenceStatus: "not_required" }; }
