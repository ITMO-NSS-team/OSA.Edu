import { penalizeModelCapacity, reserveModelCapacity } from "./llm/rate-limiter.js";
import type { LlmUsageStats, Provider, ProviderDiagnostic } from "./types.js";

export type LlmOperation = "structure" | "check";

export async function askStructuredJson(input: {
  provider: Provider;
  model: string;
  systemPrompt: string;
  userMessage: string;
  operation: LlmOperation;
  packets?: number;
  candidates?: number;
}): Promise<{ value: unknown; raw: string; usage: LlmUsageStats }> {
  const response = input.provider === "openrouter"
    ? await callOpenRouterWithRetry(input)
    : await callGeminiWithRetry(input);
  return { value: parseJson(response.raw), raw: response.raw, usage: response.usage };
}

async function callOpenRouterWithRetry(input: {
  provider: Provider;
  model: string;
  systemPrompt: string;
  userMessage: string;
  operation: LlmOperation;
  packets?: number;
  candidates?: number;
}) {
  const usage = emptyUsage();
  usage.packets = input.packets ?? 1;
  usage.candidates = input.candidates ?? 0;
  const estimatedInputTokens = estimateTokens(`${input.systemPrompt}\n${input.userMessage}`);
  const raw = await withProviderRetries({
    provider: "openrouter",
    model: input.model,
    operation: input.operation,
    estimatedInputTokens,
    usage
  }, async () => {
    const endpoint = `${openRouterBaseUrl()}/api/v1/chat/completions`;
    let compatibilityMode = false;
    const requestBody = openRouterRequestBody(input, false);
    let response = await fetchWithTimeout(endpoint, {
      method: "POST",
      headers: openRouterHeaders(),
      body: JSON.stringify(requestBody)
    });
    let payload = await response.json().catch(() => ({}));

    // A free endpoint may exist but not advertise every optional OpenAI parameter.
    // Retry once in compatibility mode instead of repeating the same unroutable request.
    if (!response.ok && response.status === 404 && isNoCompatibleEndpointError(payload)) {
      usage.requests += 1;
      usage.estimatedInputTokens += estimatedInputTokens;
      compatibilityMode = true;
      response = await fetchWithTimeout(endpoint, {
        method: "POST",
        headers: openRouterHeaders(),
        body: JSON.stringify(openRouterRequestBody(input, true))
      });
      payload = await response.json().catch(() => ({}));
    }

    if (!response.ok) {
      if (response.status === 404 && isNoCompatibleEndpointError(payload)) {
        throw openRouterNoEndpointError(response, payload, input.model);
      }
      throw providerHttpError("openrouter", response, payload);
    }

    const choice = payload?.choices?.[0];
    if (choice?.error || choice?.finish_reason === "error") {
      throw embeddedOpenRouterError(choice?.error || payload?.error, response);
    }
    const content = choice?.message?.content;
    usage.traces.push({
      at: new Date().toISOString(), operation: input.operation, provider: "openrouter", model: input.model,
      providerName: String(payload?.provider || payload?.metadata?.provider_name || "") || undefined,
      requestId: response.headers.get("x-request-id") || response.headers.get("cf-ray") || undefined,
      compatibilityMode, httpStatus: response.status
    });
    const text = contentText(content);
    if (!text) throw new Error("OpenRouter вернул пустой ответ.");
    return text;
  });
  return { raw, usage };
}

async function callGeminiWithRetry(input: {
  provider: Provider;
  model: string;
  systemPrompt: string;
  userMessage: string;
  operation: LlmOperation;
  packets?: number;
  candidates?: number;
}) {
  const usage = emptyUsage();
  usage.packets = input.packets ?? 1;
  usage.candidates = input.candidates ?? 0;
  const estimatedInputTokens = estimateTokens(`${input.systemPrompt}\n${input.userMessage}`);
  const raw = await withProviderRetries({
    provider: "gemini",
    model: input.model,
    operation: input.operation,
    estimatedInputTokens,
    usage
  }, async () => {
    const endpoint = `${geminiBaseUrl()}/v1beta/models/${encodeURIComponent(input.model)}:generateContent?key=${encodeURIComponent(geminiApiKey())}`;
    const response = await fetchWithTimeout(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        systemInstruction: { parts: [{ text: input.systemPrompt }] },
        contents: [{ role: "user", parts: [{ text: input.userMessage }] }],
        generationConfig: { temperature: 0.05, responseMimeType: "application/json" }
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw providerHttpError("gemini", response, payload);
    usage.traces.push({
      at: new Date().toISOString(), operation: input.operation, provider: "gemini", model: input.model,
      providerName: "Google", requestId: response.headers.get("x-request-id") || undefined,
      compatibilityMode: false, httpStatus: response.status
    });
    const parts = payload?.candidates?.[0]?.content?.parts;
    const text = Array.isArray(parts) ? parts.map((part: { text?: string }) => part.text || "").join("\n") : "";
    if (!text) throw new Error("Gemini вернул пустой ответ.");
    return text;
  });
  return { raw, usage };
}

async function withProviderRetries<T>(context: {
  provider: Provider;
  model: string;
  operation: LlmOperation;
  estimatedInputTokens: number;
  usage: LlmUsageStats;
}, action: () => Promise<T>): Promise<T> {
  const maxAttempts = positiveInt(
    context.provider === "openrouter" ? process.env.OPENROUTER_MAX_ATTEMPTS : process.env.GEMINI_MAX_ATTEMPTS,
    4
  );
  let lastError: Error | undefined;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const reservation = await reserveModelCapacity(context.provider, context.model, context.estimatedInputTokens);
    context.usage.rateLimitWaitMs += reservation.waitMs;
    context.usage.requests += 1;
    context.usage.estimatedInputTokens += context.estimatedInputTokens;
    const started = Date.now();
    let waitMs = 0;
    try {
      return await action();
    } catch (unknownError) {
      lastError = normalizeError(unknownError);
      const info = errorInfo(lastError);
      const retryable = shouldRetry(info, attempt, maxAttempts);
      waitMs = retryable ? Math.max(info.retryAfterMs, exponentialBackoff(attempt)) : 0;
      const diagnostic: ProviderDiagnostic = {
        at: new Date().toISOString(),
        operation: context.operation,
        attempt,
        httpStatus: info.status || undefined,
        providerCode: info.providerCode || undefined,
        message: info.message,
        retryable,
        retryAfterMs: info.retryAfterMs,
        backoffMs: waitMs,
        quotaMetric: info.quotaMetric || undefined,
        quotaDescription: info.quotaDescription || undefined,
        provider: context.provider,
        model: context.model,
        providerName: info.providerName || undefined,
        requestId: info.requestId || undefined,
        networkCode: info.networkCode || undefined
      };
      context.usage.diagnostics.push(diagnostic);
      if (!retryable) break;
      context.usage.retries += 1;
      penalizeModelCapacity(context.provider, context.model, waitMs);
    } finally {
      context.usage.requestDurationMs += Date.now() - started;
      reservation.release();
    }
    if (waitMs) await sleep(waitMs);
  }
  const fallback = context.provider === "openrouter" ? "OpenRouter не вернул ответ." : "Gemini не вернул ответ.";
  throw Object.assign(lastError || new Error(fallback), { llmUsage: context.usage });
}

export function isFatalProviderError(error: unknown) {
  const info = errorInfo(normalizeError(error));
  return [401, 402, 403].includes(info.status)
    || /authentication|invalid[_ ]api[_ ]key|unauthorized|forbidden|insufficient credits|negative credit|billing|PERMISSION_DENIED|API.*disabled/i.test(`${info.providerCode} ${info.message}`);
}

function shouldRetry(info: ReturnType<typeof errorInfo>, attempt: number, maxAttempts: number) {
  if (attempt >= maxAttempts) return false;
  if ([408, 409, 425, 500, 502, 503, 504].includes(info.status)) return true;
  if (/provider_overloaded|provider_unavailable|timeout|UNAVAILABLE|overloaded|ECONNRESET|ETIMEDOUT|EAI_AGAIN|UND_ERR_CONNECT_TIMEOUT|fetch failed|operation was aborted|AbortError|aborted/i.test(`${info.providerCode} ${info.networkCode} ${info.message}`)) return true;
  if (info.status !== 429 && !/RESOURCE_EXHAUSTED|rate[_ ]limit/i.test(`${info.providerCode} ${info.message}`)) return false;
  const permanentQuota = /per day|daily|rpd|free[- ]model.*day|requests.*day|spend|billing|negative credit|project.*quota.*0/i.test(`${info.message} ${info.quotaMetric} ${info.quotaDescription}`);
  return !permanentQuota;
}

function providerHttpError(provider: Provider, response: Response, payload: any) {
  if (provider === "gemini") return geminiHttpError(response, payload);
  const error = payload?.error || {};
  const metadata = error?.metadata || payload?.metadata || {};
  const message = String(error?.message || payload?.message || `OpenRouter HTTP ${response.status}`);
  const providerCode = String(metadata?.error_type || error?.type || error?.code || payload?.code || "");
  const quotaMetric = String(metadata?.quota_metric || metadata?.limit || "");
  const quotaDescription = String(metadata?.quota_description || metadata?.message || "");
  const providerName = String(metadata?.provider_name || payload?.provider || "");
  const requestId = response.headers.get("x-request-id") || response.headers.get("cf-ray") || "";
  return new ModelHttpError(message, response.status, parseRetryAfter(response.headers), providerCode, quotaMetric, quotaDescription, providerName, requestId);
}

function embeddedOpenRouterError(raw: any, response: Response) {
  const metadata = raw?.metadata || {};
  return new ModelHttpError(
    String(raw?.message || "OpenRouter provider error"),
    Number(raw?.code) || 502,
    parseRetryAfter(response.headers),
    String(metadata?.error_type || raw?.type || raw?.code || "provider_error"),
    "",
    "",
    String(metadata?.provider_name || ""),
    response.headers.get("x-request-id") || ""
  );
}

function geminiHttpError(response: Response, payload: any) {
  const details = Array.isArray(payload?.error?.details) ? payload.error.details : [];
  let retryAfterMs = parseRetryAfter(response.headers);
  let quotaMetric = "";
  let quotaDescription = "";
  for (const detail of details) {
    const type = String(detail?.["@type"] || "");
    if (/RetryInfo/.test(type)) retryAfterMs = Math.max(retryAfterMs, durationMs(detail.retryDelay));
    if (/QuotaFailure/.test(type) && Array.isArray(detail.violations)) {
      const first = detail.violations[0] || {};
      quotaMetric = String(first.subject || first.quotaMetric || "");
      quotaDescription = String(first.description || "");
    }
  }
  return new ModelHttpError(
    String(payload?.error?.message || `Gemini HTTP ${response.status}`),
    response.status,
    retryAfterMs,
    String(payload?.error?.status || ""),
    quotaMetric,
    quotaDescription,
    "Google",
    response.headers.get("x-request-id") || ""
  );
}

function errorInfo(error: Error) {
  if (error instanceof ModelHttpError) return {
    status: error.status,
    retryAfterMs: error.retryAfterMs,
    providerCode: error.providerCode,
    quotaMetric: error.quotaMetric,
    quotaDescription: error.quotaDescription,
    providerName: error.providerName,
    requestId: error.requestId,
    networkCode: "",
    message: error.message
  };
  const cause = (error as Error & { cause?: unknown }).cause as { code?: unknown; message?: unknown; hostname?: unknown } | undefined;
  const networkCode = typeof cause?.code === "string" ? cause.code : "";
  const causeMessage = typeof cause?.message === "string" ? cause.message : "";
  const host = typeof cause?.hostname === "string" ? cause.hostname : "";
  const details = [error.message, networkCode, causeMessage, host && `host=${host}`].filter(Boolean).join(" · ");
  return { status: 0, retryAfterMs: 0, providerCode: "", quotaMetric: "", quotaDescription: "", providerName: "", requestId: "", networkCode, message: details || error.message };
}

class ModelHttpError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly retryAfterMs: number,
    public readonly providerCode: string,
    public readonly quotaMetric: string,
    public readonly quotaDescription: string,
    public readonly providerName: string,
    public readonly requestId: string
  ) { super(message); }
}

export function parseJson(raw: string) {
  const stripped = raw.trim().replace(/^```json\s*/i, "").replace(/```$/i, "").trim();
  try { return JSON.parse(stripped) as unknown; }
  catch {
    const starts = [stripped.indexOf("{"), stripped.indexOf("[")].filter((value) => value >= 0);
    const start = starts.length ? Math.min(...starts) : -1;
    const end = Math.max(stripped.lastIndexOf("}"), stripped.lastIndexOf("]"));
    if (start < 0 || end < start) throw new Error("LLM вернула невалидный JSON.");
    return JSON.parse(stripped.slice(start, end + 1)) as unknown;
  }
}

export function emptyUsage(): LlmUsageStats {
  return { requests: 0, retries: 0, packets: 0, candidates: 0, estimatedInputTokens: 0, rateLimitWaitMs: 0, requestDurationMs: 0, diagnostics: [], traces: [] };
}

export function mergeUsage(target: LlmUsageStats, value: LlmUsageStats) {
  target.requests += value.requests;
  target.retries += value.retries;
  target.packets += value.packets;
  target.candidates += value.candidates;
  target.estimatedInputTokens += value.estimatedInputTokens;
  target.rateLimitWaitMs += value.rateLimitWaitMs;
  target.requestDurationMs += value.requestDurationMs;
  target.diagnostics.push(...value.diagnostics);
  target.traces.push(...(value.traces || []));
}


function openRouterRequestBody(input: {
  model: string;
  systemPrompt: string;
  userMessage: string;
  operation: LlmOperation;
}, compatibilityMode: boolean) {
  return {
    model: input.model,
    messages: [
      { role: "system", content: input.systemPrompt },
      { role: "user", content: input.userMessage }
    ],
    temperature: 0.05,
    max_completion_tokens: positiveInt(
      process.env.OPENROUTER_MAX_COMPLETION_TOKENS,
      input.operation === "structure" ? 16_000 : 8_000
    ),
    ...(compatibilityMode ? {} : { response_format: { type: "json_object" } }),
    provider: openRouterProviderPreferences(compatibilityMode),
    metadata: { operation: input.operation, app: "OSA.Edu" }
  };
}

function openRouterProviderPreferences(compatibilityMode = false) {
  const dataCollection = process.env.OPENROUTER_DATA_COLLECTION?.trim().toLowerCase() === "deny" ? "deny" : "allow";
  const zdr = /^(1|true|yes)$/i.test(process.env.OPENROUTER_ZDR || "");
  const explicitlyRequireParameters = /^(1|true|yes)$/i.test(process.env.OPENROUTER_REQUIRE_PARAMETERS || "");
  return {
    allow_fallbacks: true,
    require_parameters: compatibilityMode ? false : explicitlyRequireParameters,
    data_collection: dataCollection,
    ...(zdr ? { zdr: true } : {})
  };
}

function isNoCompatibleEndpointError(payload: any) {
  const message = String(payload?.error?.message || payload?.message || "");
  return /no endpoints found.*handle the requested parameters/i.test(message);
}

function openRouterNoEndpointError(response: Response, payload: any, model: string) {
  const original = String(payload?.error?.message || payload?.message || `OpenRouter HTTP ${response.status}`);
  const message = [
    `OpenRouter не нашёл доступный endpoint для модели ${model}.`,
    "Проверьте настройки Privacy/ZDR аккаунта и доступность бесплатной модели.",
    "Для бесплатных endpoint разрешите обработку данных либо выберите openrouter/free или платную модель.",
    `Исходная ошибка: ${original}`
  ].join(" ");
  const requestId = response.headers.get("x-request-id") || response.headers.get("cf-ray") || "";
  return new ModelHttpError(message, response.status, 0, "no_compatible_endpoint", "", "", "OpenRouter", requestId);
}

function openRouterHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${openRouterApiKey()}`,
    "Content-Type": "application/json",
    "X-OpenRouter-Metadata": "enabled"
  };
  const referer = process.env.OPENROUTER_HTTP_REFERER?.trim();
  const title = process.env.OPENROUTER_APP_TITLE?.trim() || "OSA.Edu";
  if (referer) headers["HTTP-Referer"] = referer;
  if (title) headers["X-OpenRouter-Title"] = title;
  return headers;
}

function contentText(content: unknown) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.map((item) => {
    if (typeof item === "string") return item;
    if (item && typeof item === "object" && typeof (item as { text?: unknown }).text === "string") return (item as { text: string }).text;
    return "";
  }).join("\n");
}

function normalizeError(error: unknown) {
  return error instanceof Error ? error : new Error(String(error));
}
function openRouterBaseUrl() { return (process.env.OPENROUTER_API_BASE_URL || "https://openrouter.ai").replace(/\/$/, ""); }
function geminiBaseUrl() { return (process.env.GEMINI_API_BASE_URL || "https://generativelanguage.googleapis.com").replace(/\/$/, ""); }
function openRouterApiKey() { const key = process.env.OPENROUTER_API_KEY?.trim(); if (!key) throw new Error("Для OpenRouter не найден OPENROUTER_API_KEY в .env."); return key; }
function geminiApiKey() { const key = process.env.GOOGLE_API_KEY?.trim(); if (!key) throw new Error("Для Gemini не найден GOOGLE_API_KEY в .env."); return key; }
function estimateTokens(value: string) { const chars = Number(process.env.LLM_CHARS_PER_TOKEN || 3); return Math.ceil(value.length / (Number.isFinite(chars) && chars > 0 ? chars : 3)); }
function positiveInt(value: string | undefined, fallback: number) { const parsed = Number(value); return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback; }
function exponentialBackoff(attempt: number) { return 1800 * Math.pow(2, attempt - 1) + Math.floor(Math.random() * 700); }
function parseRetryAfter(headers: Headers) {
  const raw = headers.get("retry-after");
  if (raw) {
    const seconds = Number(raw);
    if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
    const at = Date.parse(raw);
    if (Number.isFinite(at)) return Math.max(0, at - Date.now());
  }
  const reset = headers.get("x-ratelimit-reset");
  if (reset) {
    const value = Number(reset);
    if (Number.isFinite(value)) return Math.max(0, value > 10_000_000_000 ? value - Date.now() : value * 1000 - Date.now());
  }
  return 0;
}
function durationMs(value: unknown) { if (typeof value !== "string") return 0; const match = value.match(/^([\d.]+)s$/); return match ? Math.ceil(Number(match[1]) * 1000) : 0; }
function sleep(ms: number) { return new Promise((resolve) => setTimeout(resolve, ms)); }
async function fetchWithTimeout(url: string, init: RequestInit) {
  const timeoutMs = positiveInt(process.env.LLM_REQUEST_TIMEOUT_MS, 240_000);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try { return await fetch(url, { ...init, signal: controller.signal }); }
  finally { clearTimeout(timer); }
}
