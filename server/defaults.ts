import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { CheckProfile, Provider } from "./types.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const promptPath = path.resolve(__dirname, "../config/semantic-prompt.txt");
const mapPromptPath = path.resolve(__dirname, "../config/document-map-prompt.txt");

export const DEFAULT_PROMPT = (await fs.readFile(promptPath, "utf8")).trim();
export const DEFAULT_MAP_PROMPT = (await fs.readFile(mapPromptPath, "utf8")).trim();
export const DEFAULT_ADDITIONAL_CRITERIA = "";
export const DEFAULT_PROFILE: CheckProfile = "core";

export interface ModelDefinition {
  id: string;
  label: string;
  provider: Provider;
  tier: "free" | "production";
  contextTokens: number;
  note: string;
  recommended?: boolean;
}

export const MODELS: ModelDefinition[] = [
    {
    id: "nvidia/nemotron-3-super-120b-a12b:free",
    label: "Nemotron 3 Super · бесплатно",
    provider: "openrouter",
    tier: "free",
    contextTokens: 1_000_000,
    note: "Фиксированная бесплатная модель с контекстом 1 млн токенов. Подходит как резервный вариант, если DeepSeek V4 Flash временно недоступен."
  },
  {
    id: "nvidia/nemotron-3-ultra-550b-a55b:free",
    label: "Nemotron 3 Ultra · бесплатно",
    provider: "openrouter",
    tier: "free",
    contextTokens: 1_000_000,
    note: "Более крупная бесплатная модель для сложной структуры и смысловой проверки. Обычно медленнее и может быть временно перегружена."
  },
  {
    id: "openrouter/free",
    label: "OpenRouter Free Router · бесплатно",
    provider: "openrouter",
    tier: "free",
    contextTokens: 200_000,
    note: "OpenRouter автоматически выбирает доступную бесплатную модель. Удобно для тестов, но конкретная модель и результат могут различаться между запусками."
  },
  {
    id: "google/gemma-4-31b-it:free",
    label: "Gemma 4 31B · бесплатно",
    provider: "openrouter",
    tier: "free",
    contextTokens: 262_000,
    note: "Быстрый бесплатный вариант для небольших и средних работ. Для очень длинной ВКР лучше выбрать модель с контекстом 1 млн токенов."
  },
  {
    id: "deepseek/deepseek-v4-flash",
    label: "DeepSeek V4 Flash · production",
    provider: "openrouter",
    tier: "production",
    contextTokens: 1_000_000,
    note: "Контекст 1 млн токенов, высокая пропускная способность и низкая стоимость. Подходит для структуры и большинства содержательных проверок."
  },
  {
    id: "deepseek/deepseek-v4-pro",
    label: "DeepSeek V4 Pro · усиленная production",
    provider: "openrouter",
    tier: "production",
    contextTokens: 1_000_000,
    note: "Более сильный и дорогой вариант для сложных правил: научная новизна, связь положений с главами, достаточность экспериментов и финальная верификация спорных выводов."
  },
  {
    id: "google/gemini-2.5-flash",
    label: "Gemini 2.5 Flash · production",
    provider: "openrouter",
    tier: "production",
    contextTokens: 1_000_000,
    note: "Экономичный вариант: большой контекст, хорошая скорость и умеренная стоимость для регулярных проверок."
  },
  {
    id: "openai/gpt-5.5",
    label: "GPT-5.5 · premium production",
    provider: "openrouter",
    tier: "production",
    contextTokens: 1_000_000,
    note: " ",
  }
];

export function modelDefinition(id: string) {
  return MODELS.find((model) => model.id === id);
}
