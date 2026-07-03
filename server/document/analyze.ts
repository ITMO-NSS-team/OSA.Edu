import type { DocumentBlock, ExtractedDocument } from "../types.js";
import { extractBestTitle } from "./title.js";

export function buildDocument(text: string, pages: Array<{ number: number; text: string }>, warnings: string[]): ExtractedDocument {
  const blocks = buildBlocks(text, pages);
  const fields = extractFields(blocks);
  return { text, pages, blocks, detectedPages: pages.length || undefined, warnings, fields };
}

function buildBlocks(text: string, pages: Array<{ number: number; text: string }>): DocumentBlock[] {
  if (!pages.length) return paragraphsToBlocks(text, undefined, "Документ");
  return pages.flatMap((page) => paragraphsToBlocks(page.text, page.number, `Страница ${page.number}`));
}

function paragraphsToBlocks(text: string, page: number | undefined, location: string): DocumentBlock[] {
  const chunks = text
    .split(/\n\s*\n/g)
    .flatMap((paragraph) => splitLongParagraph(paragraph.trim()))
    .map((item) => item.replace(/[ \t]+/g, " ").trim())
    .filter((item) => item.length >= 2);
  return chunks.map((value, index) => ({
    id: `${page ? `p${page}` : "doc"}-b${index + 1}`,
    page,
    location,
    type: classifyBlock(value),
    text: value
  }));
}

function splitLongParagraph(value: string) {
  if (value.length <= 2400) return [value];
  const sentences = value.split(/(?<=[.!?])\s+(?=[А-ЯA-ZЁ0-9])/u);
  const result: string[] = [];
  let current = "";
  for (const sentence of sentences) {
    if (current && `${current} ${sentence}`.length > 1800) { result.push(current); current = sentence; }
    else current = current ? `${current} ${sentence}` : sentence;
  }
  if (current) result.push(current);
  return result;
}

function classifyBlock(text: string): DocumentBlock["type"] {
  const compact = text.replace(/\s+/g, " ").trim();
  if (/^(?:рис(?:унок)?|таблица|график)\s*\d*[.\-–—:]?/iu.test(compact)) return "caption";
  if (/^(?:глава\s+\d+|\d+(?:\.\d+){1,3}\.?)\s+\p{L}/iu.test(compact) || isNamedHeading(compact)) return "heading";
  if (/^(?:\d+[.)]|[-–—•])\s+/u.test(compact)) return "list";
  if (/^(?:\[?\d+\]?\.?\s+)[А-ЯA-ZЁ][^\n]{20,}$/u.test(compact) && /(?:doi|isbn|https?:|pp?\.|№|vol\.)/iu.test(compact)) return "bibliography";
  if (/[=≈≤≥∑∫√]|\\(?:frac|sum|begin)/u.test(compact) && compact.length < 700) return "formula";
  return "paragraph";
}

function isNamedHeading(text: string) {
  if (text.length > 190) return false;
  return /^(?:введение|заключение|выводы(?:\s+по\s+главе)?|список\s+(?:использованных\s+)?(?:источников|литературы)|оглавление|содержание|приложение(?:\s+[А-ЯA-Z])?)\.?$/iu.test(text)
    || (text === text.toLocaleUpperCase("ru") && /\p{L}/u.test(text) && text.split(/\s+/).length <= 12);
}

function extractFields(blocks: DocumentBlock[]): ExtractedDocument["fields"] {
  const title = extractTitle(blocks);
  const goal = blocks.find((block) => /(?<![\p{L}\p{N}_])цель(?:ю)?\s+(?:работы|исследования)?\s*(?:является|состоит|заключается|–|-|:)/iu.test(block.text));
  const tasks = extractFollowingList(blocks, /задач(?:и|ами|ей)?\s+(?:работы|исследования)|для достижения.*цели/iu, 12);
  const defenseStatements = extractSection(blocks, /положени[яй],?\s+выносимые\s+на\s+защиту/iu, 18);
  const chapterHeadings = blocks.filter((block) => {
    if (/^глава\s+\d+/iu.test(block.text)) return true;
    if (!/^\d+\.\s+\p{L}/u.test(block.text) || !block.page) return false;
    const pageBlocks = blocks.filter((item) => item.page === block.page);
    return pageBlocks.findIndex((item) => item.id === block.id) <= 2 && block.text.length <= 150;
  });
  const conclusionHeadings = blocks.filter((block) => block.type === "heading" && /^(?:выводы|заключение|итоги)/iu.test(block.text));
  const bibliographyStart = blocks.findIndex((block) => block.type === "heading" && /список.*(?:литератур|источник)/iu.test(block.text));
  const bibliographyBlocks = bibliographyStart >= 0 ? blocks.slice(bibliographyStart + 1).filter((block) => block.type === "bibliography" || /^\s*\d+[.)]\s+/u.test(block.text)) : blocks.filter((block) => block.type === "bibliography");
  return { title, goal, tasks, defenseStatements, chapterHeadings, conclusionHeadings, bibliographyBlocks };
}

function extractTitle(blocks: DocumentBlock[]) {
  const first = blocks.filter((block) => !block.page || block.page === 1).slice(0, 30);
  return extractBestTitle(first, blocks);
}


function extractFollowingList(blocks: DocumentBlock[], headingPattern: RegExp, limit: number) {
  const index = blocks.findIndex((block) => headingPattern.test(block.text));
  if (index < 0) return [];
  const selected: DocumentBlock[] = [];
  for (const block of blocks.slice(index, index + limit)) {
    if (block !== blocks[index] && block.type === "heading") break;
    if (block.type === "list" || /(?<![\p{L}\p{N}_])задач/iu.test(block.text)) selected.push(block);
  }
  return selected;
}

function extractSection(blocks: DocumentBlock[], headingPattern: RegExp, limit: number) {
  const index = blocks.findIndex((block) => headingPattern.test(block.text));
  if (index < 0) return [];
  const selected: DocumentBlock[] = [];
  for (const block of blocks.slice(index + 1, index + 1 + limit)) {
    if (block.type === "heading" && !/положени/iu.test(block.text)) break;
    selected.push(block);
  }
  return selected.filter((block) => block.type === "list" || /(?:метод|алгоритм|модель|технолог|комплекс|система)/iu.test(block.text));
}
