import "dotenv/config";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import cors from "cors";
import express from "express";
import multer from "multer";
import { DEFAULT_ADDITIONAL_CRITERIA, DEFAULT_MAP_PROMPT, DEFAULT_PROFILE, DEFAULT_PROMPT, MODELS, modelDefinition } from "./defaults.js";
import { refreshMapElements } from "./document/llm-map-builder.js";
import { readExtracted, saveExtracted } from "./extract.js";
import { configuredRateLimits } from "./llm/rate-limiter.js";
import { startQueue } from "./queue.js";
import { reportToMarkdown } from "./report.js";
import { loadRuleRegistry } from "./rules/registry.js";
import { addJobs, deleteJob, readJobs, recoverInterruptedJobs, updateJob } from "./store.js";
import type { CheckProfile, DocumentElementType, DocumentMapElement, Job } from "./types.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const port = Number(process.env.PORT || 8787);
const maxMb = Number(process.env.MAX_FILE_SIZE_MB || 35);
const uploads = path.resolve(__dirname, "../data/uploads");
await fs.mkdir(uploads, { recursive: true });
await recoverInterruptedJobs();

app.use(cors({ origin: process.env.WEB_ORIGIN || "http://127.0.0.1:5173" }));
app.use(express.json({ limit: "4mb" }));

const storage = multer.diskStorage({
  destination: (_req, _file, cb) => cb(null, uploads),
  filename: (_req, file, cb) => cb(null, `${Date.now()}-${crypto.randomUUID()}${path.extname(file.originalname).toLowerCase()}`)
});
const upload = multer({
  storage,
  limits: { fileSize: maxMb * 1024 * 1024, files: 30 },
  fileFilter: (_req, file, cb) => cb(null, [".pdf", ".docx"].includes(path.extname(file.originalname).toLowerCase()))
});

app.get("/api/health", async (_req, res) => {
  const registry = await loadRuleRegistry();
  res.json({
    ok: true,
    models: MODELS,
    configured: { gemini: Boolean(process.env.GOOGLE_API_KEY?.trim()), openrouter: Boolean(process.env.OPENROUTER_API_KEY?.trim()) },
    defaults: { prompt: DEFAULT_PROMPT, mapPrompt: DEFAULT_MAP_PROMPT, additionalCriteria: DEFAULT_ADDITIONAL_CRITERIA, profile: DEFAULT_PROFILE },
    rateLimits: { gemini: configuredRateLimits("gemini"), openrouter: configuredRateLimits("openrouter") },
    knowledge: {
      coreCount: registry.core.length,
      softCount: registry.soft.length,
      fullCount: registry.all.length,
      retrieval: "Один запрос строит крупные диапазоны ВКР; после подтверждения правила маршрутизируются по явной карте областей. Точные факты проверяются кодом, смысловые требования — выбранной моделью OpenRouter группами."
    }
  });
});

app.get("/api/rules", async (req, res) => {
  const registry = await loadRuleRegistry();
  const profile: CheckProfile = req.query.profile === "full" ? "full" : "core";
  res.json(profile === "full" ? registry.all : registry.core);
});
app.get("/api/jobs", async (_req, res) => res.json(await readJobs()));

app.post("/api/jobs", upload.array("files", 30), async (req, res) => {
  const files = (req.files || []) as Express.Multer.File[];
  if (!files.length) return res.status(400).json({ error: "Добавьте хотя бы один PDF или DOCX файл." });
  if (!process.env.OPENROUTER_API_KEY?.trim()) return res.status(400).json({ error: "Для OpenRouter не найден OPENROUTER_API_KEY в .env." });
  const requestedModel = String(req.body.model || MODELS[0].id);
  const selectedModel = modelDefinition(requestedModel);
  if (!selectedModel) return res.status(400).json({ error: "Выбрана неизвестная модель OpenRouter." });
  const model = selectedModel.id;
  const profile: CheckProfile = req.body.profile === "full" ? "full" : "core";
  const prompt = String(req.body.prompt || DEFAULT_PROMPT).trim();
  const mapPrompt = String(req.body.mapPrompt || DEFAULT_MAP_PROMPT).trim();
  const additionalCriteria = String(req.body.additionalCriteria || "").trim();
  if (prompt.length < 300 || mapPrompt.length < 300) return res.status(400).json({ error: "Промпты слишком короткие для строгого JSON-режима." });
  const now = new Date().toISOString();
  const jobs: Job[] = files.map((file) => ({
    id: crypto.randomUUID(), originalName: sanitize(file.originalname), filePath: file.path, mimeType: file.mimetype, size: file.size,
    createdAt: now, updatedAt: now, status: "queued", provider: selectedModel.provider, model, profile,
    prompt, mapPrompt, additionalCriteria, attempts: 0, progress: 0
  }));
  await addJobs(jobs);
  startQueue();
  res.status(201).json(jobs);
});

app.get("/api/jobs/:id/structure", async (req, res) => {
  const job = await findJob(req.params.id);
  if (!job?.extractedPath) return res.status(404).json({ error: "Структура документа ещё не готова." });
  const document = await readExtracted(job.extractedPath);
  if (!document.map) return res.status(404).json({ error: "Структура документа ещё не готова." });
  res.json({ map: document.map, blocks: document.blocks });
});

app.patch("/api/jobs/:id/map/elements/:elementId", async (req, res) => {
  const context = await mapContext(req.params.id);
  if (!context) return res.status(404).json({ error: "Структура документа не найдена." });
  const element = context.document.map!.elements.find((item) => item.id === req.params.elementId);
  if (!element) return res.status(404).json({ error: "Фрагмент структуры не найден." });
  const proposedStart = typeof req.body.startBlockId === "string" ? req.body.startBlockId : element.startBlockId;
  const proposedEnd = typeof req.body.endBlockId === "string" ? req.body.endBlockId : element.endBlockId;
  const boundaryError = validateBoundaries(context.document.blocks, proposedStart, proposedEnd);
  if (boundaryError) return res.status(400).json({ error: boundaryError });
  if (typeof req.body.quote === "string" && req.body.quote.trim()) {
    const quoteError = validateQuote(context.document.blocks, proposedStart, proposedEnd, req.body.quote);
    if (quoteError) return res.status(400).json({ error: quoteError });
  }
  if (typeof req.body.type === "string" && allowedTypes.has(req.body.type)) element.type = req.body.type as DocumentElementType;
  if (typeof req.body.label === "string" && req.body.label.trim()) element.label = req.body.label.trim().slice(0, 180);
  if (typeof req.body.quote === "string") element.quote = req.body.quote.trim().slice(0, 1200);
  element.startBlockId = proposedStart;
  element.endBlockId = proposedEnd;
  if (req.body.state === "confirmed" || req.body.state === "ambiguous") element.state = req.body.state;
  element.source = "user";
  context.document.map!.review.confirmedByUser = false;
  context.document.map!.review.confirmedAt = undefined;
  refreshMapElements(context.document.map!, context.document.blocks);
  await persistMap(context.job, context.document);
  res.json(await findJob(context.job.id));
});

app.post("/api/jobs/:id/map/elements", async (req, res) => {
  const context = await mapContext(req.params.id);
  if (!context) return res.status(404).json({ error: "Структура документа не найдена." });
  const first = context.document.blocks[0];
  if (!first) return res.status(400).json({ error: "В документе нет текстовых блоков." });
  const type = allowedTypes.has(req.body.type) ? req.body.type as DocumentElementType : "other";
  const startBlockId = String(req.body.startBlockId || first.id);
  const endBlockId = String(req.body.endBlockId || first.id);
  const boundaryError = validateBoundaries(context.document.blocks, startBlockId, endBlockId);
  if (boundaryError) return res.status(400).json({ error: boundaryError });
  const element: DocumentMapElement = {
    id: `section-${crypto.randomUUID().slice(0, 8)}`, type, label: String(req.body.label || "Новый фрагмент").slice(0, 180),
    startBlockId, endBlockId,
    blockIds: [], pages: [], text: "", quote: "", confidence: 1, state: "confirmed", source: "user"
  };
  context.document.map!.elements.push(element);
  context.document.map!.review.confirmedByUser = false;
  context.document.map!.review.confirmedAt = undefined;
  refreshMapElements(context.document.map!, context.document.blocks);
  await persistMap(context.job, context.document);
  res.status(201).json(await findJob(context.job.id));
});

app.delete("/api/jobs/:id/map/elements/:elementId", async (req, res) => {
  const context = await mapContext(req.params.id);
  if (!context) return res.status(404).json({ error: "Структура документа не найдена." });
  const before = context.document.map!.elements.length;
  context.document.map!.elements = context.document.map!.elements.filter((item) => item.id !== req.params.elementId);
  if (before === context.document.map!.elements.length) return res.status(404).json({ error: "Фрагмент структуры не найден." });
  context.document.map!.review.confirmedByUser = false;
  context.document.map!.review.confirmedAt = undefined;
  refreshMapElements(context.document.map!, context.document.blocks);
  await persistMap(context.job, context.document);
  res.status(204).end();
});

app.post("/api/jobs/:id/confirm-structure", async (req, res) => {
  const context = await mapContext(req.params.id);
  if (!context) return res.status(404).json({ error: "Структура документа не найдена." });
  refreshMapElements(context.document.map!, context.document.blocks);
  if (!context.document.map!.elements.length) return res.status(400).json({ error: "Добавьте хотя бы один смысловой фрагмент." });
  const invalid = context.document.map!.issues.some((item) => item.code === "invalid_boundaries" || item.code === "empty_structure");
  if (invalid) return res.status(400).json({ error: "Исправьте недействительные границы фрагментов перед запуском проверки." });
  context.document.map!.review.confirmedByUser = true;
  context.document.map!.review.confirmedAt = new Date().toISOString();
  await saveExtracted(context.job.extractedPath!, context.document);
  const updated = await updateJob(context.job.id, { status: "queued_check", progress: 32, documentMap: context.document.map, error: undefined });
  startQueue();
  res.json(updated);
});

app.post("/api/jobs/:id/cancel", async (req, res) => {
  const job = await updateJob(req.params.id, { status: "cancelled", finishedAt: new Date().toISOString() });
  if (!job) return res.status(404).json({ error: "Задача не найдена." });
  res.json(job);
});

app.post("/api/jobs/:id/retry", async (req, res) => {
  const current = await findJob(req.params.id);
  if (!current) return res.status(404).json({ error: "Задача не найдена." });
  const cacheExists = current.extractedPath ? await exists(current.extractedPath) : false;
  const sourceExists = await exists(current.filePath);
  if (!sourceExists && !cacheExists) return res.status(409).json({ error: "Нет исходного файла или кэша. Загрузите ВКР заново." });
  const nextStatus = current.documentMap?.review.confirmedByUser && cacheExists ? "queued_check" : "queued";
  const job = await updateJob(req.params.id, { status: nextStatus, progress: nextStatus === "queued_check" ? 32 : 0, error: undefined, diagnostics: [], finishedAt: undefined, report: undefined, retryRuleIds: [], attempts: 0 });
  startQueue(); res.json(job);
});
app.post("/api/jobs/:id/retry-failed", async (req, res) => {
  const current = await findJob(req.params.id);
  if (!current?.report || !current.extractedPath) return res.status(409).json({ error: "Сначала должна завершиться хотя бы одна проверка." });
  const retryRuleIds = current.report.ruleResults.filter((item) => (item.status === "not_checked" && item.checkedBy === "llm") || (item.status === "uncertain" && item.checkedBy === "llm" && (item.evidenceStatus === "rejected" || Boolean(item.coverage && item.coverage.checkedCandidateCount < item.coverage.candidateCount)))).map((item) => item.ruleId);
  if (!retryRuleIds.length) return res.status(409).json({ error: "Правил с ошибкой запроса или неполным покрытием нет." });
  const job = await updateJob(req.params.id, { status: "queued_check", progress: 32, error: undefined, diagnostics: [], finishedAt: undefined, retryRuleIds, attempts: 0 });
  startQueue(); res.json(job);
});

app.delete("/api/jobs/:id", async (req, res) => {
  const job = await deleteJob(req.params.id);
  if (!job) return res.status(404).json({ error: "Задача не найдена." });
  await Promise.all([fs.unlink(job.filePath).catch(() => undefined), job.extractedPath ? fs.unlink(job.extractedPath).catch(() => undefined) : Promise.resolve()]);
  res.status(204).end();
});

app.get("/api/jobs/:id/report.md", async (req, res) => {
  const job = await findJob(req.params.id);
  if (!job?.report) return res.status(404).send("Отчёт ещё не готов.");
  res.setHeader("Content-Type", "text/markdown; charset=utf-8");
  res.setHeader("Content-Disposition", `attachment; filename*=UTF-8''${encodeURIComponent(`${job.originalName}-protocol.md`)}`);
  res.send(reportToMarkdown(job.originalName, job.report));
});
app.get("/api/jobs/:id/report.json", async (req, res) => {
  const job = await findJob(req.params.id);
  if (!job?.report) return res.status(404).json({ error: "Отчёт ещё не готов." });
  res.setHeader("Content-Disposition", `attachment; filename*=UTF-8''${encodeURIComponent(`${job.originalName}-protocol.json`)}`);
  res.json(job.report);
});

app.use((error: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  const message = error instanceof Error ? error.message : "Неизвестная ошибка сервера.";
  if (/File too large/i.test(message)) return res.status(413).json({ error: `Файл больше ${maxMb} МБ.` });
  res.status(500).json({ error: message });
});

const allowedTypes = new Set<DocumentElementType>(["title", "abstract", "introduction", "goal", "tasks", "defense_statements", "chapter", "chapter_conclusions", "conclusion", "bibliography", "appendices", "other"]);
async function mapContext(id: string) { const job = await findJob(id); if (!job?.extractedPath) return undefined; const document = await readExtracted(job.extractedPath); return document.map ? { job, document } : undefined; }
async function persistMap(job: Job, document: Awaited<ReturnType<typeof readExtracted>>) { await saveExtracted(job.extractedPath!, document); await updateJob(job.id, { documentMap: document.map, status: "awaiting_review", progress: 30, report: undefined }); }
async function findJob(id: string) { return (await readJobs()).find((item) => item.id === id); }
function sanitize(value: string) { return value.replace(/[\\/:*?"<>|]/g, "_"); }
async function exists(value: string) { try { await fs.access(value); return true; } catch { return false; } }

function validateBoundaries(blocks: Array<{ id: string }>, startBlockId: string, endBlockId: string) {
  const start = blocks.findIndex((block) => block.id === startBlockId);
  const end = blocks.findIndex((block) => block.id === endBlockId);
  if (start < 0 || end < 0) return "Выбранный блок не найден в документе.";
  if (start > end) return "Первый блок диапазона должен находиться раньше последнего.";
  return "";
}


function validateQuote(blocks: Array<{ id: string; text: string }>, startBlockId: string, endBlockId: string, quote: string) {
  const start = blocks.findIndex((block) => block.id === startBlockId);
  const end = blocks.findIndex((block) => block.id === endBlockId);
  if (start < 0 || end < start) return "Нельзя проверить цитату при недействительных границах.";
  const normalized = (value: string) => value.toLocaleLowerCase("ru").replace(/ё/g, "е").replace(/[«»“”]/g, '"').replace(/\s+/g, " ").trim();
  const target = normalized(quote);
  if (target.length < 4) return "Опорная цитата слишком короткая.";
  return blocks.slice(start, end + 1).some((block) => normalized(block.text).includes(target)) ? "" : "Опорная цитата должна дословно находиться внутри выбранного диапазона.";
}

app.listen(port, "127.0.0.1", () => { console.log(`API: http://127.0.0.1:${port}`); startQueue(); });
