import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import mammoth from "mammoth";
import { buildDocument } from "./document/analyze.js";
import type { ExtractedDocument } from "./types.js";

const execFileAsync = promisify(execFile);

interface PyMuPdfResult {
  engine: "pymupdf";
  engineVersion: string;
  pageCount: number;
  pages: Array<{ number: number; text: string }>;
  warnings: string[];
}

export async function extractDocument(filePath: string): Promise<ExtractedDocument> {
  const extension = path.extname(filePath).toLowerCase();

  if (extension === ".docx") {
    const result = await mammoth.extractRawText({ path: filePath });
    const text = normalize(result.value);
    return {
      ...buildDocument(text, [], [
        "DOCX не содержит надёжной привязки к страницам. Для финальной проверки вёрстки загрузите также PDF."
      ]),
      sourceFormat: "docx"
    };
  }

  if (extension === ".pdf") {
    const extracted = await extractPdfWithPyMuPdf(filePath);
    const pages = extracted.pages.map((page) => ({
      number: page.number,
      text: normalize(page.text)
    }));

    const text = pages
      .map((page) => `<<<PAGE ${page.number}>>>\n${page.text}`)
      .join("\n\n");

    console.log(
      `[PDF_EXTRACTOR] engine=${extracted.engine} version=${extracted.engineVersion} pages=${extracted.pageCount}`
    );

    return {
      ...buildDocument(
        text,
        pages,
        pages.length
          ? extracted.warnings
          : ["Не удалось определить страницы PDF."]
      ),
      sourceFormat: "pdf"
    };
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

async function extractPdfWithPyMuPdf(filePath: string): Promise<PyMuPdfResult> {
  const pythonExecutable = resolvePythonExecutable();
  const scriptPath = path.resolve(
    process.cwd(),
    "server",
    "python",
    "extract_pdf.py"
  );

  try {
    const { stdout, stderr } = await execFileAsync(
      pythonExecutable,
      [scriptPath, path.resolve(filePath)],
      {
        encoding: "utf8",
        maxBuffer: 64 * 1024 * 1024,
        windowsHide: true
      }
    );

    const stderrText = stderr;
    if (stderrText.trim()) {
      console.warn(stderrText.trim());
    }

    const stdoutText = stdout;
    const parsed = JSON.parse(stdoutText) as PyMuPdfResult;

    if (!Array.isArray(parsed.pages)) {
      throw new Error("PyMuPDF вернул ответ без массива pages.");
    }

    return parsed;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(
      `Не удалось извлечь PDF через PyMuPDF. Проверьте Python, виртуальное окружение и пакет PyMuPDF. Причина: ${message}`
    );
  }
}

function resolvePythonExecutable() {
  const configured = process.env.PYTHON_BIN?.trim();
  if (configured) return configured;

  return process.platform === "win32"
    ? path.resolve(process.cwd(), ".venv", "Scripts", "python.exe")
    : path.resolve(process.cwd(), ".venv", "bin", "python");
}

function normalize(value: string) {
  return value
    .normalize("NFC")
    .replace(/\r/g, "")
    .replace(/\u00a0/gu, " ")
    .replace(/[\u200b\u2060]/gu, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
