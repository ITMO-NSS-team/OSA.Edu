import type { DocumentBlock, DocumentElementType } from "../types.js";

export function trimBlocksForElement(type: DocumentElementType, blocks: DocumentBlock[]) {
  if (!blocks.length) return [];
  if (type === "tasks") return trimBetween(blocks, /(?:^|\n|[.!?]\s+)Задачи\s+(?:диссертационной\s+)?работы\s*\.?/iu, [/(?:^|\n|[.!?]\s+)(?:Объект\s+и\s+предмет\s+исследования|Научная\s+новизна|Теоретическая\s+и\s+практическая\s+значимость|Положения,?\s+выносимые\s+на\s+защиту)\s*\.?/iu]);
  if (type === "defense_statements") return trimBetween(blocks, /(?:^|\n|[.!?]\s+)Положения,?\s+выносимые\s+на\s+защиту\s*\.?/iu, [/(?:^|\n|[.!?]\s+)(?:Достоверность\s+научных\s+результатов|Апробация\s+работы|Личный\s+вклад\s+автора|Публикации|Структура\s+и\s+объ[её]м)\s*\.?/iu]);
  if (type === "goal") return trimBetween(blocks, /(?:^|\n|[.!?]\s+)Цель\s+(?:диссертационной\s+)?работы\s*\.?/iu, [/(?:^|\n|[.!?]\s+)Задачи\s+(?:диссертационной\s+)?работы\s*\.?/iu]);
  if (type === "bibliography") return trimBetween(blocks, /(?:^|\n)Список\s+(?:использованных\s+)?(?:источников|литературы)\s*\.?/iu, [/(?:^|\n)Приложение\b/iu]);
  return blocks;
}

function trimBetween(blocks: DocumentBlock[], startPattern: RegExp, endPatterns: RegExp[]) {
  let startBlock = 0;
  let startOffset = 0;
  let foundStart = false;
  for (let index = 0; index < blocks.length; index++) {
    const match = blocks[index].text.match(startPattern);
    if (!match || match.index === undefined) continue;
    startBlock = index;
    startOffset = match.index + leadingDelimiterLength(match[0]);
    foundStart = true;
    break;
  }
  if (!foundStart) return blocks;

  let endBlock = blocks.length - 1;
  let endOffset = blocks[endBlock].text.length;
  outer: for (let index = startBlock; index < blocks.length; index++) {
    const from = index === startBlock ? startOffset + 1 : 0;
    const tail = blocks[index].text.slice(from);
    for (const pattern of endPatterns) {
      const match = tail.match(pattern);
      if (!match || match.index === undefined) continue;
      endBlock = index;
      endOffset = from + match.index + leadingDelimiterLength(match[0]);
      break outer;
    }
  }

  const result: DocumentBlock[] = [];
  for (let index = startBlock; index <= endBlock; index++) {
    let text = blocks[index].text;
    if (index === startBlock) text = text.slice(startOffset);
    if (index === endBlock) text = text.slice(0, index === startBlock ? Math.max(0, endOffset - startOffset) : endOffset);
    text = text.trim();
    if (text) result.push({ ...blocks[index], text });
  }
  return result.length ? result : blocks;
}

function leadingDelimiterLength(value: string) {
  const match = value.match(/^(?:\n|[.!?]\s+)/u);
  return match?.[0].length || 0;
}
