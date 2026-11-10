// Evidence grouping only affects presentation; report JSON remains untouched.
export const fixSuggestions: Record<string, string> = {
  "ITMO-4-2-a4": "Установите формат страниц А4 (210 × 297 мм) и повторно экспортируйте PDF.",
  "ITMO-4-2-color": "Установите чёрный цвет обнаруженных цветных текстовых фрагментов.",
  "ITMO-4-2-font": "Используйте Times New Roman не менее 12 пт для основного текста.",
  "ITMO-4-3-1": "Проверьте сквозную нумерацию, положение и гарнитуру номеров страниц.",
  "ITMO-4-4-3": "Исправьте нумерацию глав и подразделов, исключив точки после номеров.",
  "ITMO-4-4-4": "Исправьте отступы и оформление заголовков по нормативному пункту.",
  "ITMO-4-5-3": "Проверьте нумерацию, центрирование и пунктуацию подписей рисунков.",
  "ITMO-4-6-2-number": "Используйте уникальные номера таблиц при сквозной или поглавной нумерации.",
  "ITMO-4-6-3": "Разместите названия над таблицами слева; исправьте разделители и конечные точки.",
  "ITMO-4-10-1": "Добавьте отсутствующие записи в библиографию либо исправьте ссылки на них.",
  "ITMO-4-10-3-order": "Упорядочьте и перенумеруйте источники по первому упоминанию.",
  "ITMO-4-10-3-format": "Исправьте нумерацию и абзацные отступы библиографических записей.",
  "ITMO-A1-links": "Удалите активные гиперссылки в списке литературы, сохранив текст URL.",
};

type EvidenceLike = { page?: number | null; quote: string; reason?: string; blockId?: string; start?: number; end?: number; sourceKind?: string };
export function compactEvidence<T extends EvidenceLike>(items: T[]) {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const reason = item.reason || "";
    const quote = (item.quote || "").trim();
    const key = /размер страницы|page size/i.test(quote) ? `size:${quote}`
      : /Гарнитура номера|Гарнитура.*кегль/.test(reason) ? `font:${reason}`
      : item.sourceKind === "pdf_annotation" ? `annotation:${reason}`
      : /Цвет фрагмента/.test(reason) ? `color:${reason}`
      : `item:${item.blockId}:${item.start}:${item.end}:${quote}:${reason}`;
    groups.set(key, [...(groups.get(key) || []), item]);
  }
  return [...groups.values()].map(members => ({ count: members.length, samples: members.slice(0, 2), pages: [...new Set(members.map(v => v.page).filter((p): p is number => typeof p === "number"))].sort((a, b) => a - b) }));
}
export function formatPages(pages: number[]): string {
  if (!pages.length) return "—";
  const parts: string[] = []; let first = pages[0], last = first;
  for (const p of pages.slice(1)) { if (p === last + 1) { last = p; continue; } parts.push(first === last ? `${first}` : `${first}–${last}`); first = last = p; }
  parts.push(first === last ? `${first}` : `${first}–${last}`);
  return parts.join(", ");
}
