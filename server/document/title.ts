import type { DocumentBlock } from "../types.js";

const TITLE_HINT = /(?:^|[^\p{L}])(?:модел(?:ь|и|ей)|алгоритм(?:ы|ов)?|метод(?:ы|ов)?|систем(?:а|ы)|технолог(?:ия|ии)|анализ|синтез|управлен(?:ие|ия)|генераци(?:я|и)|сегментаци(?:я|и)|разработк(?:а|и))(?!\p{L})/iu;
const EXCLUDED = /(?:министерств|университет|институт|факультет|кафедр|на\s+правах\s+рукописи|диссертац(?:ия|ии)|на\s+соискание|научн(?:ый|ая)\s+руководител|специальност|санкт-петербург|москва|\b20\d{2}\b)/iu;
const COMMON = new Set(["и", "в", "на", "с", "по", "для", "из", "к", "о", "об", "от", "до", "при", "без", "под", "над", "между", "основе"]);

export function extractBestTitle(range: DocumentBlock[], allBlocks: DocumentBlock[] = range): DocumentBlock | undefined {
  const lexicon = buildLexicon(allBlocks);
  const candidates: Array<{ block: DocumentBlock; text: string; score: number }> = [];
  for (const block of range) {
    const lines = block.text.split(/\n+/).map((line) => restoreJoinedWords(cleanLine(line), lexicon)).filter(Boolean);
    for (let start = 0; start < lines.length; start++) {
      if (!/[А-ЯЁа-яё]/u.test(lines[start]) || /[A-Za-z]/u.test(lines[start])) continue;
      let combined = "";
      for (let end = start; end < Math.min(lines.length, start + 3); end++) {
        if (!/[А-ЯЁа-яё]/u.test(lines[end]) || /[A-Za-z]/u.test(lines[end])) break;
        combined = `${combined} ${lines[end]}`.replace(/\s+/g, " ").trim();
        const score = titleScore(combined);
        if (score > 0) candidates.push({ block, text: combined, score });
      }
    }
    const whole = restoreJoinedWords(cleanLine(block.text.replace(/\n+/g, " ")), lexicon);
    if (!/[A-Za-z]/u.test(whole)) {
      const score = titleScore(whole);
      if (score > 0) candidates.push({ block, text: whole, score });
    }
  }
  const best = candidates.sort((a, b) => b.score - a.score || b.text.length - a.text.length)[0];
  return best ? { ...best.block, text: best.text } : undefined;
}

function cleanLine(value: string) {
  return value.replace(/\u00ad/g, "").replace(/\s+/g, " ").trim();
}

function titleScore(value: string) {
  const text = value.replace(/^[«"]|[»"]$/g, "").trim();
  if (text.length < 25 || text.length > 240 || EXCLUDED.test(text) || !TITLE_HINT.test(text)) return -1;
  const words = text.match(/[\p{L}\p{N}]+(?:-[\p{L}\p{N}]+)*/gu) || [];
  if (words.length < 4 || words.length > 24) return -1;
  let score = Math.min(text.length, 150) + words.length * 4;
  if (/^(?:модел|алгоритм|метод|систем|технолог|анализ|синтез|управлен|генераци|сегментаци|разработк)/iu.test(text)) score += 55;
  if (/(?:^|[^\p{L}])(?:больших\s+языковых\s+моделей|искусственного\s+интеллекта|машинного\s+обучения)(?!\p{L})/iu.test(text)) score += 15;
  if (text.endsWith(".")) score -= 20;
  return score;
}

function buildLexicon(blocks: DocumentBlock[]) {
  const counts = new Map<string, number>();
  for (const block of blocks) {
    const normalized = block.text.replace(/([А-ЯЁа-яё])\u00ad?\s*\n\s*([А-ЯЁа-яё])/gu, "$1$2");
    for (const token of normalized.match(/[А-ЯЁа-яё]{2,30}/gu) || []) {
      const lower = token.toLocaleLowerCase("ru");
      counts.set(lower, (counts.get(lower) || 0) + 1);
    }
  }
  for (const word of COMMON) counts.set(word, Math.max(20, counts.get(word) || 0));
  return counts;
}

function restoreJoinedWords(value: string, lexicon: Map<string, number>) {
  return value.split(/(\s+)/).map((part) => {
    if (/^\s+$/u.test(part) || !/^[А-ЯЁа-яё]{12,}$/u.test(part)) return part;
    return segmentToken(part, lexicon) || part;
  }).join("").replace(/\s+/g, " ").trim();
}

function segmentToken(token: string, lexicon: Map<string, number>) {
  const lower = token.toLocaleLowerCase("ru");
  const n = lower.length;
  const dp: Array<{ score: number; words: string[] } | undefined> = Array(n + 1).fill(undefined);
  dp[0] = { score: 0, words: [] };
  for (let i = 0; i < n; i++) {
    const state = dp[i];
    if (!state) continue;
    for (let j = i + 1; j <= Math.min(n, i + 30); j++) {
      const word = lower.slice(i, j);
      const count = lexicon.get(word) || 0;
      if (!count || (word.length < 4 && !COMMON.has(word))) continue;
      const lengthBonus = Math.min(word.length, 14) * 0.72;
      const score = state.score + Math.log2(count + 1) * 1.2 + lengthBonus - 5;
      if (!dp[j] || score > dp[j]!.score) dp[j] = { score, words: [...state.words, word] };
    }
  }
  const best = dp[n];
  if (!best || best.words.length < 2 || best.words.some((word) => word.length === 1 && !COMMON.has(word))) return undefined;
  const joined = best.words.join(" ");
  return /^[А-ЯЁ]/u.test(token) ? joined[0].toLocaleUpperCase("ru") + joined.slice(1) : joined;
}
