import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DEFAULT_MAP_PROMPT } from "./defaults.js";
import { buildDocumentMap, mapCanBeReused } from "./document/llm-map-builder.js";
import { extractDocument, readExtracted, saveExtracted } from "./extract.js";
import { checkDocument } from "./orchestration/checker.js";
import { makeReport } from "./report.js";
import { readJobs, updateJob } from "./store.js";
import type { ExtractedDocument, Job, LlmUsageStats, ProviderDiagnostic } from "./types.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const extractedDir = path.resolve(__dirname, "../data/extracted");
let working = false;

export function startQueue() { void runQueue(); }

async function runQueue() {
  if (working) return;
  working = true;
  try {
    while (true) {
      const jobs = await readJobs();
      const job = jobs.find((item) => item.status === "queued" || item.status === "queued_check");
      if (!job) break;
      if (job.status === "queued") await prepareStructure(job);
      else await performCheck(job);
    }
  } finally { working = false; }
}

async function prepareStructure(job: Job) {
  const autoDelete = (process.env.AUTO_DELETE_SOURCE || "true").toLowerCase() === "true";
  try {
    await updateJob(job.id, { status: "extracting", progress: 3, startedAt: new Date().toISOString(), error: undefined, diagnostics: [], attempts: 0, report: undefined });
    const { document, extractedPath } = await obtainDocument(job);
    if (document.text.length < 100) throw new Error("Из файла удалось извлечь слишком мало текста. Попробуйте DOCX или PDF с текстовым слоем.");
    await updateJob(job.id, { extractedPath, progress: 10 });

    const mapPrompt = job.mapPrompt || DEFAULT_MAP_PROMPT;
    if (!mapCanBeReused(document.map, job.provider, job.model, mapPrompt)) {
      await updateJob(job.id, { status: "mapping", progress: 12 });
      document.map = await buildDocumentMap({
        document, provider: job.provider, model: job.model, prompt: mapPrompt,
        isCancelled: async () => (await currentJob(job.id))?.status === "cancelled",
        onProgress: async (done, total) => {
          if ((await currentJob(job.id))?.status === "cancelled") return;
          await updateJob(job.id, { status: "mapping", progress: 12 + Math.round((done / Math.max(1, total)) * 18) });
        }
      });
      await saveExtracted(extractedPath, document);
    }
    if (autoDelete) await fs.unlink(job.filePath).catch(() => undefined);
    if ((await currentJob(job.id))?.status === "cancelled") return;
    await updateJob(job.id, {
      status: "awaiting_review", progress: 30, documentMap: document.map,
      diagnostics: document.map?.usage.diagnostics || [], error: undefined, finishedAt: undefined
    });
  } catch (error) {
    const diagnostics = diagnosticsFromError(error);
    const suffix = diagnostics.length ? " Подробности сохранены в диагностике LLM ниже." : "";
    await updateJob(job.id, { status: "failed", progress: 100, finishedAt: new Date().toISOString(), diagnostics, error: `${(error as Error).message}${suffix}` });
  }
}

async function performCheck(job: Job) {
  try {
    if (!job.extractedPath) throw new Error("Кэш документа не найден.");
    const document = await readExtracted(job.extractedPath);
    if (!document.map?.review.confirmedByUser) throw new Error("Сначала подтвердите выделенную структуру документа.");
    await updateJob(job.id, { status: "checking", progress: 35, error: undefined, diagnostics: document.map?.usage.diagnostics || [], attempts: 0 });
    const previousReport = job.retryRuleIds?.length ? job.report : undefined;
    const checked = await checkDocument({
      document, provider: job.provider, model: job.model, prompt: job.prompt, profile: job.profile, additionalCriteria: job.additionalCriteria,
      onlyRuleIds: job.retryRuleIds?.length ? job.retryRuleIds : undefined,
      isCancelled: async () => (await currentJob(job.id))?.status === "cancelled",
      onProgress: async (done, total) => {
        if ((await currentJob(job.id))?.status === "cancelled") return;
        await updateJob(job.id, { status: "checking", progress: 35 + Math.round((done / Math.max(1, total)) * 63), attempts: done });
      }
    });
    if ((await currentJob(job.id))?.status === "cancelled") return;
    const results = previousReport ? mergeRuleResults(previousReport.ruleResults, checked.results) : checked.results;
    const warnings = uniqueStrings([...document.warnings, ...(document.map?.warnings || []), ...checked.warnings]);
    const usage = previousReport ? mergeUsageStats(previousReport.llmUsage, checked.llmUsage) : checked.llmUsage;
    const routing = previousReport ? { ...previousReport.routing, checkRequests: previousReport.routing.checkRequests + checked.routing.checkRequests } : checked.routing;
    const report = makeReport(checked.rules, results, warnings, usage, document.map, routing, { appVersion: "3.5.0", provider: job.provider, model: job.model, promptHash: hashText(job.prompt), mapPromptHash: hashText(job.mapPrompt) });
    await updateJob(job.id, { status: "completed", progress: 100, finishedAt: new Date().toISOString(), documentMap: document.map, report, retryRuleIds: [], diagnostics: [...(document.map?.usage.diagnostics || []), ...usage.diagnostics], error: undefined });
  } catch (error) {
    const diagnostics = diagnosticsFromError(error);
    await updateJob(job.id, { status: "failed", progress: 100, finishedAt: new Date().toISOString(), diagnostics, error: (error as Error).message });
  }
}

async function obtainDocument(job: Job): Promise<{ document: ExtractedDocument; extractedPath: string }> {
  if (job.extractedPath) {
    try { return { document: await readExtracted(job.extractedPath), extractedPath: job.extractedPath }; }
    catch { /* extract again */ }
  }
  try { await fs.access(job.filePath); }
  catch { throw new Error("Исходный файл удалён, а кэш извлечённого документа отсутствует. Загрузите файл заново."); }
  const document = await extractDocument(job.filePath);
  const extractedPath = path.join(extractedDir, `${job.id}.json`);
  await saveExtracted(extractedPath, document);
  return { document, extractedPath };
}

async function currentJob(id: string) { return (await readJobs()).find((item) => item.id === id); }

function diagnosticsFromError(error: unknown): ProviderDiagnostic[] {
  const usage = (error as Error & { llmUsage?: LlmUsageStats }).llmUsage;
  return usage?.diagnostics ? [...usage.diagnostics] : [];
}

function mergeRuleResults(previous: import("./types.js").RuleResult[], current: import("./types.js").RuleResult[]) {
  const replacements = new Map(current.map((item) => [item.ruleId, item]));
  return previous.map((item) => replacements.get(item.ruleId) || item);
}
function mergeUsageStats(left: LlmUsageStats, right: LlmUsageStats): LlmUsageStats {
  return { requests: left.requests + right.requests, retries: left.retries + right.retries, packets: left.packets + right.packets, candidates: left.candidates + right.candidates, estimatedInputTokens: left.estimatedInputTokens + right.estimatedInputTokens, rateLimitWaitMs: left.rateLimitWaitMs + right.rateLimitWaitMs, requestDurationMs: left.requestDurationMs + right.requestDurationMs, diagnostics: [...left.diagnostics, ...right.diagnostics], traces: [...(left.traces || []), ...(right.traces || [])] };
}
function uniqueStrings(items: string[]) { return [...new Set(items.filter(Boolean))]; }
function hashText(value: string) { return crypto.createHash("sha256").update(value || "").digest("hex").slice(0, 16); }
