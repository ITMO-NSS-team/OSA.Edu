import type { UiLanguage } from "./i18n";
import type { NormControlStatus, RuleMode, RuleScope, RuleStatus, Severity, Status } from "./types";

const labels = {
  ru: {
    status: {
      violation: "Нарушено",
      pass: "Выполнено",
      uncertain: "Неопределённо",
      not_checked: "Не обработано",
      not_applicable: "Неприменимо",
    } as Record<RuleStatus, string>,
    severity: {
      critical: "Критическое",
      major: "Существенное",
      minor: "Малое",
      info: "Информационное",
    } as Record<Severity, string>,
    jobStatus: {
      queued: "В очереди",
      extracting: "Извлечение текста",
      mapping: "Выделение структуры одним запросом",
      awaiting_review: "Ожидает проверки структуры",
      queued_check: "Проверка поставлена в очередь",
      checking: "Проверка фрагментов",
      completed: "Готово",
      failed: "Ошибка",
      cancelled: "Отменено",
    } as Record<Status, string>,
    normControlStatus: {
      queued: "В очереди",
      queued_report: "Запрос отчёта",
      submitting: "Отправка PDF",
      running: "Нормоконтроль",
      reporting: "Генерация отчёта",
      downloading: "Скачивание отчёта",
      completed: "Готово",
      failed: "Ошибка",
      cancelled: "Отменено",
    } as Record<NormControlStatus, string>,
    mode: {
      deterministic: "Код",
      candidate: "Кандидаты + LLM",
      structural: "Структура",
      semantic: "LLM",
      manual: "Ручная проверка",
    } as Record<RuleMode, string>,
    scope: {
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
      process: "Процесс подготовки",
    } as Record<RuleScope, string>,
  },
  en: {
    status: {
      violation: "Needs changes",
      pass: "Passed",
      uncertain: "Inconclusive",
      not_checked: "Not checked",
      not_applicable: "Not applicable",
    } as Record<RuleStatus, string>,
    severity: {
      critical: "Critical",
      major: "Major",
      minor: "Minor",
      info: "Informational",
    } as Record<Severity, string>,
    jobStatus: {
      queued: "Queued",
      extracting: "Extracting text",
      mapping: "Building document structure",
      awaiting_review: "Waiting for structure review",
      queued_check: "Check queued",
      checking: "Checking document fragments",
      completed: "Done",
      failed: "Error",
      cancelled: "Cancelled",
    } as Record<Status, string>,
    normControlStatus: {
      queued: "Queued",
      queued_report: "Requesting report",
      submitting: "Uploading PDF",
      running: "Formal review",
      reporting: "Generating report",
      downloading: "Downloading report",
      completed: "Done",
      failed: "Error",
      cancelled: "Cancelled",
    } as Record<NormControlStatus, string>,
    mode: {
      deterministic: "Code",
      candidate: "Candidates + LLM",
      structural: "Structure",
      semantic: "LLM",
      manual: "Manual review",
    } as Record<RuleMode, string>,
    scope: {
      document: "Whole document",
      title: "Title",
      goal: "Goal",
      defense_statements: "Defense statements",
      chapter: "Chapters",
      list: "Lists",
      figure_table: "Figures and tables",
      formula: "Formulas",
      bibliography: "Bibliography",
      presentation: "Presentation",
      defense: "Defense talk",
      process: "Preparation process",
    } as Record<RuleScope, string>,
  },
} as const;

export function uiLabels(language: UiLanguage) {
  return labels[language];
}

// Backward-compatible Russian aliases for places that do not opt into UI language yet.
export const statusLabel = labels.ru.status;
export const severityLabel = labels.ru.severity;
export const jobStatusLabel = labels.ru.jobStatus;
export const normControlStatusLabel = labels.ru.normControlStatus;
export const modeLabel = labels.ru.mode;
export const scopeLabel = labels.ru.scope;
