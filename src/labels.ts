import type { RuleMode, RuleScope, RuleStatus, Severity, Status } from "./types";

export const statusLabel: Record<RuleStatus, string> = {
  violation: "Нарушено",
  pass: "Выполнено",
  uncertain: "Неопределённо",
  not_checked: "Не обработано",
  not_applicable: "Неприменимо"
};

export const severityLabel: Record<Severity, string> = {
  critical: "Критическое",
  major: "Существенное",
  minor: "Малое",
  info: "Информационное"
};

export const jobStatusLabel: Record<Status, string> = {
  queued: "В очереди",
  extracting: "Извлечение текста",
  mapping: "Выделение структуры одним запросом",
  awaiting_review: "Ожидает проверки структуры",
  queued_check: "Проверка поставлена в очередь",
  checking: "Проверка фрагментов",
  completed: "Готово",
  failed: "Ошибка",
  cancelled: "Отменено"
};

export const modeLabel: Record<RuleMode, string> = {
  deterministic: "Код",
  structural: "Структура",
  semantic: "LLM",
  manual: "Ручная проверка"
};

export const scopeLabel: Record<RuleScope, string> = {
  document: "Весь документ",
  title: "Название",
  goal: "Цель",
  defense_statements: "Положения",
  chapter: "Главы",
  list: "Списки",
  figure_table: "Рисунки и таблицы",
  formula: "Формулы",
  bibliography: "Литература",
  presentation: "Презентация",
  defense: "Доклад и защита",
  process: "Процесс подготовки"
};
