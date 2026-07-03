import fs from "node:fs/promises";
import path from "node:path";
import mammoth from "mammoth";
import pdf from "pdf-parse";
import { buildDocument } from "./document/analyze.js";
import type { ExtractedDocument } from "./types.js";

interface PdfItem { str?: string; transform?: number[]; hasEOL?: boolean; width?: number; height?: number; }

export async function extractDocument(filePath: string): Promise<ExtractedDocument> {
  const extension = path.extname(filePath).toLowerCase();
  if (extension === ".docx") {
    const result = await mammoth.extractRawText({ path: filePath });
    const text = normalize(result.value);
    return { ...buildDocument(text, [], ["DOCX не содержит надёжной привязки к страницам. Для финальной проверки вёрстки загрузите также PDF."]), sourceFormat: "docx" };
  }
  if (extension === ".pdf") {
    const buffer = await fs.readFile(filePath);
    const pages: Array<{ number: number; text: string }> = [];
    let pageNumber = 0;
    const parsePdf = pdf as unknown as (data: Buffer, options?: unknown) => Promise<{ text: string }>;
    await parsePdf(buffer, {
      pagerender: async (pageData: { getTextContent: () => Promise<{ items: PdfItem[] }> }) => {
        pageNumber += 1;
        const content = await pageData.getTextContent();
        const text = reconstructPage(content.items);
        pages.push({ number: pageNumber, text });
        return `\n\n<<<PAGE ${pageNumber}>>>\n${text}`;
      }
    });
    const text = pages.map((page) => `<<<PAGE ${page.number}>>>\n${page.text}`).join("\n\n");
    return { ...buildDocument(text, pages, pages.length ? [] : ["Не удалось определить страницы PDF."]), sourceFormat: "pdf" };
  }
  throw new Error("Поддерживаются только PDF и DOCX.");
}

export async function saveExtracted(filePath: string, document: ExtractedDocument) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, JSON.stringify(document), "utf8");
}

export async function readExtracted(filePath: string): Promise<ExtractedDocument> {
  return JSON.parse(await fs.readFile(filePath, "utf8")) as ExtractedDocument;
}

function reconstructPage(items: PdfItem[]) {
  if (!items.some((item) => item.transform?.length)) return normalize(items.map((item) => item.str || "").join(" "));
  const positioned = items.map((item, index) => ({
    text: item.str || "",
    x: item.transform?.[4] ?? index,
    y: item.transform?.[5] ?? 0,
    height: Math.abs(item.transform?.[3] ?? item.height ?? 10),
    hasEOL: item.hasEOL
  })).filter((item) => item.text.trim());
  positioned.sort((a, b) => Math.abs(a.y - b.y) > 2 ? b.y - a.y : a.x - b.x);
  const heights = positioned.map((item) => item.height).filter((value) => value > 0).sort((a, b) => a - b);
  const medianHeight = heights[Math.floor(heights.length / 2)] || 10;
  const lines: Array<{ y: number; height: number; items: typeof positioned }> = [];
  for (const item of positioned) {
    const line = lines.find((candidate) => Math.abs(candidate.y - item.y) <= Math.max(2, medianHeight * 0.28));
    if (line) { line.items.push(item); line.height = Math.max(line.height, item.height); }
    else lines.push({ y: item.y, height: item.height, items: [item] });
  }
  lines.sort((a, b) => b.y - a.y);
  const output: string[] = [];
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    line.items.sort((a, b) => a.x - b.x);
    output.push(joinLine(line.items));
    const next = lines[index + 1];
    if (next && line.y - next.y > Math.max(medianHeight * 1.65, 15)) output.push("");
  }
  return normalize(output.join("\n"));
}

function joinLine(items: Array<{ text: string; x: number; hasEOL?: boolean }>) {
  let result = "";
  for (const item of items) {
    const text = item.text.trim();
    if (!text) continue;
    const needsSpace = result && !/[\s(\[«"'\-–—/]$/u.test(result) && !/^[,.;:!?%)\]»]/u.test(text);
    result += `${needsSpace ? " " : ""}${text}`;
  }
  return result;
}

function normalize(value: string) {
  return value.replace(/\r/g, "").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}
