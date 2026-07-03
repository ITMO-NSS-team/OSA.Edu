import type { Provider } from "../types.js";

interface UsageEvent { at: number; tokens: number; }
interface LimiterState { events: UsageEvent[]; active: number; waiters: Array<() => void>; lastStartedAt: number; cooldownUntil: number; }
const states = new Map<string, LimiterState>();
const WINDOW_MS = 60_000;

export interface RateLimitReservation { waitMs: number; release: () => void; }

export async function reserveModelCapacity(provider: Provider, model: string, estimatedInputTokens: number): Promise<RateLimitReservation> {
  const state = getState(provider, model);
  const limits = providerLimits(provider);
  const started = Date.now();
  await acquireConcurrency(state, limits.maxConcurrent);
  try {
    while (true) {
      const now = Date.now();
      state.events = state.events.filter((event) => now - event.at < WINDOW_MS);
      const requestCount = state.events.length;
      const tokenCount = state.events.reduce((sum, event) => sum + event.tokens, 0);
      const cooldownWait = Math.max(0, state.cooldownUntil - now);
      const intervalWait = Math.max(0, limits.minRequestIntervalMs - (now - state.lastStartedAt));
      const requestAllowed = limits.requestsPerMinute <= 0 || requestCount < limits.requestsPerMinute;
      const tokensAllowed = limits.inputTokensPerMinute <= 0 || (estimatedInputTokens > limits.inputTokensPerMinute
        ? tokenCount === 0
        : tokenCount + estimatedInputTokens <= limits.inputTokensPerMinute);
      if (cooldownWait <= 0 && intervalWait <= 0 && requestAllowed && tokensAllowed) {
        state.lastStartedAt = Date.now();
        state.events.push({ at: state.lastStartedAt, tokens: Math.max(0, estimatedInputTokens) });
        return { waitMs: Date.now() - started, release: () => releaseConcurrency(state) };
      }
      const requestWait = requestAllowed || !state.events.length ? 0 : Math.max(250, WINDOW_MS - (now - state.events[0].at) + 50);
      const tokenWait = tokensAllowed ? 0 : waitUntilTokenCapacity(state.events, estimatedInputTokens, limits.inputTokensPerMinute, now);
      await sleep(Math.max(50, cooldownWait, intervalWait, requestWait, tokenWait));
    }
  } catch (error) {
    releaseConcurrency(state);
    throw error;
  }
}

export function penalizeModelCapacity(provider: Provider, model: string, waitMs: number) {
  const state = getState(provider, model);
  state.cooldownUntil = Math.max(state.cooldownUntil, Date.now() + Math.max(0, waitMs));
}

export function configuredRateLimits(provider: Provider) { return providerLimits(provider); }

function getState(provider: Provider, model: string) {
  const key = `${provider}:${model}`;
  const current = states.get(key) || { events: [], active: 0, waiters: [], lastStartedAt: 0, cooldownUntil: 0 };
  states.set(key, current);
  return current;
}

function providerLimits(provider: Provider) {
  const prefix = provider === "openrouter" ? "OPENROUTER" : "GEMINI";
  const defaultRpm = provider === "openrouter" ? 18 : 6;
  const defaultTpm = provider === "openrouter" ? 0 : 180_000;
  const rpm = envNumber(`${prefix}_MAX_REQUESTS_PER_MINUTE`, defaultRpm);
  return {
    requestsPerMinute: rpm,
    inputTokensPerMinute: envNumber(`${prefix}_MAX_INPUT_TOKENS_PER_MINUTE`, defaultTpm),
    maxConcurrent: Math.max(1, envNumber(`${prefix}_MAX_CONCURRENT_REQUESTS`, 1)),
    minRequestIntervalMs: intervalMs(`${prefix}_MIN_REQUEST_INTERVAL_MS`, rpm)
  };
}

async function acquireConcurrency(state: LimiterState, maxConcurrent: number) {
  if (state.active < maxConcurrent) { state.active += 1; return; }
  await new Promise<void>((resolve) => state.waiters.push(resolve));
  state.active += 1;
}
function releaseConcurrency(state: LimiterState) { state.active = Math.max(0, state.active - 1); state.waiters.shift()?.(); }
function waitUntilTokenCapacity(events: UsageEvent[], incoming: number, limit: number, now: number) {
  if (limit <= 0) return 250;
  let retained = events.reduce((sum, event) => sum + event.tokens, 0);
  for (const event of events) {
    retained -= event.tokens;
    if (retained + incoming <= limit) return Math.max(250, WINDOW_MS - (now - event.at) + 50);
  }
  return WINDOW_MS;
}
function intervalMs(name: string, rpm: number) {
  const explicit = Number(process.env[name]);
  if (Number.isFinite(explicit) && explicit >= 0) return explicit;
  return rpm > 0 ? Math.ceil(WINDOW_MS / rpm) : 0;
}
function envNumber(name: string, fallback: number) { const value = Number(process.env[name]); return Number.isFinite(value) && value >= 0 ? value : fallback; }
function sleep(ms: number) { return new Promise((resolve) => setTimeout(resolve, ms)); }
