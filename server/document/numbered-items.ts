import type { DocumentBlock } from "../types.js";

export interface NumberedItem {
  number: number;
  text: string;
  source: DocumentBlock;
}

const DEFENSE_HEADING = /(?:основные\s+)?положения,?\s+выносимые\s+на\s+защиту\.?/giu;

export function collectNumberedItems(blocks: DocumentBlock[]): NumberedItem[] {
  if (!blocks.length) return [];
  const hasDefenseHeading = blocks.some((block) => {
    DEFENSE_HEADING.lastIndex = 0;
    return DEFENSE_HEADING.test(block.text.replace(/\u00ad/g, ""));
  });
  let insideDefenseSection = !hasDefenseHeading;
  const pieces: Array<{ text: string; block: DocumentBlock }> = [];
  for (const block of blocks) {
    let text = block.text.replace(/\u00ad/g, "");
    DEFENSE_HEADING.lastIndex = 0;
    const headings = [...text.matchAll(DEFENSE_HEADING)];
    if (headings.length) {
      const last = headings[headings.length - 1];
      text = text.slice((last.index || 0) + last[0].length);
      insideDefenseSection = true;
    } else if (!insideDefenseSection) continue;
    text = text.replace(/^\s*\d{1,3}\s*\n/u, "");
    text = text.replace(/([А-ЯЁа-яё])-\s*\n\s*([А-ЯЁа-яё])/gu, "$1$2");
    pieces.push({ text, block });
  }
  const joined = pieces.map((piece) => piece.text).join("\n");
  const starts = [...joined.matchAll(/(?:^|\n)\s*(\d{1,3})\.\s+(?=[А-ЯЁA-Z])/gmu)];
  if (!starts.length) return [];
  const offsets: Array<{ start: number; end: number; block: DocumentBlock }> = [];
  let cursor = 0;
  for (const piece of pieces) {
    offsets.push({ start: cursor, end: cursor + piece.text.length, block: piece.block });
    cursor += piece.text.length + 1;
  }
  return starts.map((match, index) => {
    const start = (match.index || 0) + match[0].search(/\d/);
    const end = index + 1 < starts.length ? (starts[index + 1].index || joined.length) : joined.length;
    const raw = joined.slice(start, end).trim();
    const source = offsets.find((item) => start >= item.start && start <= item.end)?.block || blocks[0];
    return { number: Number(match[1]), text: raw.replace(/^\d+\.\s+/, "").replace(/\s+/g, " ").trim(), source };
  });
}

export function collectUniqueNumberedItems(blocks: DocumentBlock[]): NumberedItem[] {
  const byNumber = new Map<number, NumberedItem>();
  for (const item of collectNumberedItems(blocks)) {
    const current = byNumber.get(item.number);
    if (!current || itemQuality(item.text) > itemQuality(current.text)) byNumber.set(item.number, item);
  }
  return [...byNumber.values()].sort((a, b) => a.number - b.number);
}

function itemQuality(value: string) {
  const words = value.match(/[А-ЯЁа-яёA-Za-z]{2,}/gu) || [];
  const gluedPenalty = (value.match(/[а-яё]{18,}/giu) || []).length * 12;
  return words.length * 4 + Math.min(value.length, 1200) / 20 - gluedPenalty;
}
