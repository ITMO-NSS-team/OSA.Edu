import type { DocumentMap, LlmUsageStats, Report, ReportTechnical, Rule, RuleResult, RuleStatus } from "./types.js";

const APP_VERSION = "3.5.0";
const SCORE_FAMILIES: string[][] = [
  ["CORE-5-4", "CORE-7-2", "CORE-19"],
  ["CORE-4-1", "CORE-4-3", "CORE-12"]
];

export function makeReport(
  rules: Rule[],
  results: RuleResult[],
  warnings: string[],
  llmUsage: LlmUsageStats,
  documentMap: DocumentMap | undefined,
  routing: Report["routing"],
  technicalInput?: Partial<ReportTechnical>
): Report {
  const preparedResults = linkRelatedViolations(results);
  const countStatus = (status: RuleStatus) => preparedResults.filter((item) => item.status === status).length;
  const violations = preparedResults.filter((item) => item.status === "violation");
  const applicable = preparedResults.filter((item) => item.status !== "not_applicable");
  const checked = applicable.filter((item) => item.status === "pass" || item.status === "violation");
  const coverage = applicable.length ? checked.length / applicable.length : 0;

  const candidateTotals = preparedResults.flatMap((item) => item.coverage ? [item.coverage] : []);
  const totalCandidates = candidateTotals.reduce((sum, item) => sum + item.candidateCount, 0);
  const checkedCandidates = candidateTotals.reduce((sum, item) => sum + item.checkedCandidateCount, 0);
  const candidateCoverage = totalCandidates ? Math.min(1, checkedCandidates / totalCandidates) : 1;

  const scoreGroups = buildScoreGroups(rules, preparedResults);
  const passWeight = scoreGroups.filter((item) => item.status === "pass").reduce((sum, item) => sum + item.weight, 0);
  const violationWeight = scoreGroups.filter((item) => item.status === "violation").reduce((sum, item) => sum + item.weight, 0);
  const score = coverage < 0.6 || passWeight + violationWeight === 0 ? null : Math.round(100 * passWeight / (passWeight + violationWeight));

  const counts = {
    critical: violations.filter((item) => item.severity === "critical").length,
    major: violations.filter((item) => item.severity === "major").length,
    minor: violations.filter((item) => item.severity === "minor").length,
    info: violations.filter((item) => item.severity === "info").length,
    pass: countStatus("pass"),
    violation: countStatus("violation"),
    uncertain: countStatus("uncertain"),
    notApplicable: countStatus("not_applicable"),
    notChecked: countStatus("not_checked")
  };

  const summary = score === null
    ? `Проверено ${checked.length} из ${applicable.length} применимых правил. Покрытие недостаточно для итоговой оценки.`
    : `Проверено ${checked.length} из ${applicable.length} применимых правил. Нарушено: ${counts.violation}; неопределённо: ${counts.uncertain}; не обработано: ${counts.notChecked}.`;

  const technical: ReportTechnical = {
    appVersion: technicalInput?.appVersion || APP_VERSION,
    provider: technicalInput?.provider || documentMap?.provider || "openrouter",
    model: technicalInput?.model || documentMap?.model || "unknown",
    promptHash: technicalInput?.promptHash || "unknown",
    mapPromptHash: technicalInput?.mapPromptHash || documentMap?.promptHash
  };
  const statuses: RuleStatus[] = ["violation", "pass", "uncertain", "not_checked", "not_applicable"];
  return {
    ruleResults: preparedResults,
    documentMap,
    ruleCatalog: rules.map((rule) => ({
      id: rule.id, category: rule.category, title: rule.title, requirement: rule.requirement,
      sourceLabel: rule.sourceLabel, sourceLine: rule.sourceLine, mode: rule.mode, scope: rule.scope,
      severity: rule.severity, correctExample: rule.correctExample, incorrectExample: rule.incorrectExample
    })),
    summary,
    score,
    scoreIsProvisional: score !== null && coverage < 0.9,
    coverage,
    candidateCoverage,
    counts,
    checkedRules: checked.length,
    totalRules: rules.length,
    warnings,
    llmUsage,
    technical,
    ruleStats: statuses.map((status) => ({ status, count: countStatus(status) })),
    routing
  };
}

export function reportToMarkdown(name: string, report: Report) {
  const score = report.score === null ? "не рассчитана" : `${report.score}/100${report.scoreIsProvisional ? " (предварительно)" : ""}`;
  const catalog = new Map(report.ruleCatalog.map((rule) => [rule.id, rule]));
  const lines = [
    `# Протокол нормоконтроля — ${name}`,
    "",
    `**Оценка по проверенным правилам:** ${score}`,
    `**Покрытие правил:** ${Math.round(report.coverage * 100)} % (${report.checkedRules} проверенных из ${report.totalRules} правил профиля)`,
    `**Отправлено назначенных фрагментов:** ${Math.round(report.candidateCoverage * 100)} %`,
    "",
    `Нарушено: ${report.counts.violation}; выполнено: ${report.counts.pass}; неопределённо: ${report.counts.uncertain}; не обработано: ${report.counts.notChecked}; неприменимо: ${report.counts.notApplicable}.`,
    "",
    "> Оценка не является решением о допуске к защите. Смысловые замечания и исправления необходимо проверить вручную.",
    "",
    "## Краткий итог",
    report.summary,
    ""
  ];

  if (report.documentMap) {
    const importantTypes = new Set(["title", "introduction", "goal", "tasks", "defense_statements", "chapter", "chapter_conclusions", "conclusion", "bibliography"]);
    const displayed = report.documentMap.elements.filter((item) => importantTypes.has(item.type));
    lines.push(
      "## Структурная карта",
      "",
      `- Статус: ${report.documentMap.status === "ready" ? "готова" : "частичная"}`,
      `- Подтверждена пользователем: ${report.documentMap.review.confirmedByUser ? "да" : "нет"}`,
      `- Обработано блоков одним запросом: ${report.documentMap.extraction.processedBlocks} из ${report.documentMap.extraction.totalBlocks}`,
      `- Всего смысловых диапазонов: ${report.documentMap.elements.length}; показано основных: ${displayed.length}`,
      `- Неоднозначных элементов: ${report.documentMap.elements.filter((item) => item.state === "ambiguous").length}`,
      ""
    );
    for (const element of displayed) lines.push(`- **${element.type} · ${element.label}:** ${element.startBlockId}…${element.endBlockId}${element.pages.length ? ` (стр. ${element.pages[0]}–${element.pages.at(-1)})` : ""}${element.state === "ambiguous" ? " — требует проверки" : ""}`);
    if (report.documentMap.issues.length) lines.push("", "### Замечания к карте", "", ...report.documentMap.issues.map((item) => `- ${item.message}`), "");
    else lines.push("");
  }

  for (const status of ["violation", "pass", "uncertain", "not_checked", "not_applicable"] as RuleStatus[]) {
    const items = report.ruleResults.filter((result) => result.status === status);
    lines.push(`## ${statusHeading(status)} (${items.length})`, "");
    for (const result of items) {
      const rule = catalog.get(result.ruleId);
      lines.push(`### ${result.ruleId}${rule ? ` — ${rule.title}` : ""}`, "");
      if (rule) {
        lines.push(`- **Требование:** ${rule.requirement}`);
        lines.push(`- **Источник:** ${rule.sourceLabel}, строка ${rule.sourceLine}`);
      }
      lines.push(`- **Результат:** ${result.explanation}`);
      lines.push(`- **Проверено:** ${result.checkedBy}`);
      lines.push(`- **Проверка доказательств:** ${evidenceStatusLabel(result.evidenceStatus)}`);
      if (result.relatedRuleIds?.length) lines.push(`- **Связанные правила:** ${result.relatedRuleIds.join(", ")}`);
      if (result.consistencyNotes?.length) lines.push(`- **Проверка согласованности:** ${result.consistencyNotes.join(" ")}`);
      if (result.coverage) lines.push(`- **Фрагменты:** ${result.coverage.checkedCandidateCount} из ${result.coverage.candidateCount}; полная область: ${result.coverage.exhaustive ? "да" : "нет"}`);
      if (result.termFindings?.length) {
        lines.push("- **Разбор обозначений:**");
        for (const term of result.termFindings) lines.push(`  - ${term.term}: ${term.kind}; ${term.status}`);
      }
      if (result.coverageMatrix?.length) {
        lines.push("", "#### Матрица полного покрытия", "", "| Фрагмент | Проверено блоков | Полнота | Элементы |", "|---|---:|---|---|");
        for (const row of result.coverageMatrix) lines.push(`| ${escapeTable(row.label)} | ${row.checkedBlocks}/${row.totalBlocks} | ${row.complete ? "полная" : "неполная"} | ${row.items.map((item) => `${item.name}: ${matrixStatus(item.status)}`).join("; ")} |`);
        lines.push("");
      }
      if (result.evidence.length) {
        lines.push("- **Доказательства:**");
        for (const evidence of result.evidence) lines.push(`  - ${evidence.location}${evidence.page ? `, стр. ${evidence.page}` : ""}: «${evidence.quote}»`);
      }
      if (result.fix) lines.push(`- **Исправление:** ${result.fix}`);
      lines.push("");
    }
  }

  lines.push(
    "## Технические сведения",
    "",
    `- Версия приложения: ${report.technical.appVersion}`,
    `- Провайдер API: ${report.technical.provider}`,
    `- Model ID: ${report.technical.model}`,
    `- Хеш промпта проверки: ${report.technical.promptHash}`,
    `- Хеш промпта структуры: ${report.technical.mapPromptHash || "—"}`,
    "",
    "## Нагрузка LLM",
    "",
    `- Физических запросов: ${report.llmUsage.requests}`,
    `- Повторных попыток: ${report.llmUsage.retries}`,
    `- Проверено пакетов: ${report.llmUsage.packets}`,
    `- Передано правил/объектов: ${report.llmUsage.candidates}`,
    `- Маршрутизация: ${report.routing.strategy}; явно задано: ${report.routing.explicitRules}; fallback: ${report.routing.fallbackRules}; фрагментов: ${report.routing.fragments}; запросов проверки: ${report.routing.checkRequests}`,
    `- Оценочно входных токенов: ${report.llmUsage.estimatedInputTokens}`,
    `- Ожидание rate limiter: ${Math.round(report.llmUsage.rateLimitWaitMs / 1000)} с`,
    `- Время запросов к модели: ${Math.round(report.llmUsage.requestDurationMs / 1000)} с`,
    ""
  );
  if ((report.llmUsage.traces || []).length) {
    lines.push("### Успешные обращения к модели", "");
    for (const trace of report.llmUsage.traces || []) lines.push(`- ${trace.operation}: ${trace.provider}/${trace.model}; upstream=${trace.providerName || "—"}; HTTP ${trace.httpStatus}; compatibility=${trace.compatibilityMode ? "да" : "нет"}; request=${trace.requestId || "—"}`);
    lines.push("");
  }
  if (report.llmUsage.diagnostics.length) lines.push("## Диагностика API", "", ...report.llmUsage.diagnostics.map((item) => `- ${item.operation}, попытка ${item.attempt}, HTTP ${item.httpStatus || "—"}: ${item.message}; retry=${item.retryable}; ожидание=${item.backoffMs} мс${item.quotaMetric ? `; quota=${item.quotaMetric}` : ""}`), "");
  if (report.warnings.length) lines.push("## Ограничения проверки", "", ...report.warnings.map((item) => `- ${item}`), "");
  return lines.join("\n");
}

function linkRelatedViolations(results: RuleResult[]) {
  const evidenceGroups = new Map<string, Set<string>>();
  const findingGroups = new Map<string, Set<string>>();
  for (const result of results.filter((item) => item.status === "violation")) {
    for (const evidence of result.evidence) {
      const key = `${evidence.blockId}|${evidence.quote.toLocaleLowerCase("ru").replace(/ё/g, "е").replace(/\s+/g, " ").trim()}`;
      const ids = evidenceGroups.get(key) || new Set<string>(); ids.add(result.ruleId); evidenceGroups.set(key, ids);
    }
    for (const findingId of result.findingIds || []) {
      const ids = findingGroups.get(findingId) || new Set<string>(); ids.add(result.ruleId); findingGroups.set(findingId, ids);
    }
  }
  const related = new Map<string, Set<string>>();
  for (const ids of [...evidenceGroups.values(), ...findingGroups.values()]) if (ids.size > 1) for (const id of ids) {
    const target = related.get(id) || new Set<string>();
    for (const other of ids) if (other !== id) target.add(other);
    related.set(id, target);
  }
  return results.map((result) => ({ ...result, relatedRuleIds: related.has(result.ruleId) ? [...related.get(result.ruleId)!].sort() : result.relatedRuleIds }));
}

function buildScoreGroups(rules: Rule[], results: RuleResult[]) {
  const resultMap = new Map(results.map((item) => [item.ruleId, item]));
  const groups = new Map<string, { rules: Rule[]; status: RuleStatus; weight: number }>();
  for (const rule of rules) {
    const key = scoreGroupKey(rule);
    const status = resultMap.get(rule.id)?.status || "not_checked";
    const existing = groups.get(key);
    if (!existing) groups.set(key, { rules: [rule], status, weight: rule.weight });
    else {
      existing.rules.push(rule);
      existing.weight = Math.max(existing.weight, rule.weight);
      existing.status = mergeStatus(existing.status, status);
    }
  }
  return [...groups.values()];
}

function scoreGroupKey(rule: Rule) {
  const family = SCORE_FAMILIES.find((ids) => ids.includes(rule.id));
  if (family) return `family:${family.join("|")}`;
  if (rule.layer === "user") return `rule:${rule.id}`;
  if (rule.detectorId) return `detector:${rule.detectorId}`;
  return `rule:${rule.id}`;
}

function mergeStatus(left: RuleStatus, right: RuleStatus): RuleStatus {
  const rank: Record<RuleStatus, number> = { violation: 5, pass: 4, uncertain: 3, not_checked: 2, not_applicable: 1 };
  return rank[right] > rank[left] ? right : left;
}
function evidenceStatusLabel(value: RuleResult["evidenceStatus"]) { return value === "verified" ? "цитаты подтверждены по исходным блокам" : value === "coverage_verified" ? "отсутствие подтверждено полным просмотром назначенной области" : value === "rejected" ? "заявленное нарушение не подтверждено допустимой цитатой или полной матрицей" : "доказательство не требовалось"; }
function matrixStatus(value: "found" | "not_found" | "ambiguous") { return value === "found" ? "найдено" : value === "not_found" ? "не найдено" : "неоднозначно"; }
function escapeTable(value: string) { return value.replace(/\|/g, "\\|").replace(/\s+/g, " "); }
function statusHeading(value: RuleStatus) { return ({ violation: "Нарушено", pass: "Выполнено", uncertain: "Неопределённо", not_checked: "Не обработано", not_applicable: "Неприменимо" })[value]; }
