import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { useLanguage, type UiLanguage } from "../i18n";
import type {
  RepositoryQualityJob,
  RepositoryQualityPreflight,
  RepositoryQualityStatus,
} from "../types";

type CheckTone = "pass" | "fail" | "uncertain" | "na";

interface QualityCheckView {
  key: string;
  label: string;
  description: string;
  tone: CheckTone;
  status: string;
  details?: string;
  recommendation?: string;
  weight?: number;
  earned?: number;
  informational?: boolean;
  raw: Record<string, any>;
}

interface QualityReportView {
  repository: string;
  analyzedAt?: string;
  score: number;
  repoType: string;
  repoTypeLabel: string;
  repoTypeConfidence?: string;
  repoTypeError?: string;
  checks: QualityCheckView[];
  informational: QualityCheckView[];
  raw: unknown;
}

const COPY = {
  ru: {
    title: "Качество репозитория",
    subtitle: "Оценка структуры, документации и воспроизводимости проекта.",
    repository: "Репозиторий",
    checkEnvironment: "Проверить окружение",
    checkingEnvironment: "Проверяем…",
    run: "Оценить репозиторий",
    running: "Проверка выполняется…",
    backendNotReady: "Сервис проверки не готов к запуску.",
    osaMissing: "Модуль проверки не установлен.",
    llmMissing: "Не настроен API-ключ LLM.",
    gitMissing: "git не найден в PATH.",
    ready: "Окружение готово",
    environmentIssues: "Есть проблемы с окружением",
    enterRepository: "Укажите ссылку на репозиторий.",
    preflightFailed: "Не пройдена предварительная проверка",
    environmentNotReady: "Окружение не готово.",
    stop: "Остановить",
    retry: "Повторить",
    deleteRun: "Удалить запуск",
    type: "Тип",
    confidence: "уверенность",
    score: "оценка",
    uncertainType: "Тип репозитория определён неуверенно",
    uncertainTypeText: "От типа зависит набор критериев и их вес. Проверьте, что определённый тип соответствует назначению проекта.",
    result: "Результат",
    whatToDo: "Что сделать",
    whatToCheck: "Что проверить",
    osaData: "Технические детали",
    infoKicker: "Не влияют на итоговую оценку",
    infoTitle: "Дополнительные проверки",
    criteriaTitle: "Основные критерии",
    criteriaHint: "",
    downloadJson: "Скачать результат JSON",
    history: "История и технический лог",
    noRuns: "Запусков пока нет.",
    osaLog: "Технический лог",
    logPlaceholder: "Лог появится после запуска проверки.",
    logTruncated: "Показана последняя часть лога.",
    downloadLog: "Скачать лог",
    passed: "Выполнено",
    needsWork: "Нужно улучшить",
    manual: "Проверьте вручную",
    na: "Не применяется",
    queued: "В очереди",
    jobRunning: "Проверка выполняется",
    cancelling: "Останавливаем проверку",
    completed: "Проверка завершена",
    cancelled: "Проверка остановлена",
    failed: "Проверка завершилась с ошибкой",
    requestFailed: "Не удалось выполнить запрос к backend.",
    analyzed: "Проверено",
  },
  en: {
    title: "Repository quality",
    subtitle: "Evaluate project structure, documentation, and reproducibility.",
    repository: "Repository",
    checkEnvironment: "Check environment",
    checkingEnvironment: "Checking…",
    run: "Evaluate repository",
    running: "Check in progress…",
    backendNotReady: "The checking service is not ready.",
    osaMissing: "The checking module is not installed.",
    llmMissing: "The LLM API key is not configured.",
    gitMissing: "git was not found in PATH.",
    ready: "Environment is ready",
    environmentIssues: "Environment needs attention",
    enterRepository: "Enter a repository URL.",
    preflightFailed: "Preflight check failed",
    environmentNotReady: "The environment is not ready.",
    stop: "Stop",
    retry: "Retry",
    deleteRun: "Delete run",
    type: "Type",
    confidence: "confidence",
    score: "score",
    uncertainType: "Repository type is uncertain",
    uncertainTypeText: "The repository type changes the criteria and their weights. Verify that the detected type matches the project.",
    result: "Result",
    whatToDo: "What to improve",
    whatToCheck: "What to verify",
    osaData: "Technical details",
    infoKicker: "Does not affect the final score",
    infoTitle: "Additional checks",
    criteriaTitle: "Scored criteria",
    criteriaHint: "",
    downloadJson: "Download result JSON",
    history: "History and technical log",
    noRuns: "No runs yet.",
    osaLog: "Technical log",
    logPlaceholder: "The log will appear after the check starts.",
    logTruncated: "Only the latest part of the log is shown.",
    downloadLog: "Download log",
    passed: "Passed",
    needsWork: "Needs improvement",
    manual: "Manual review",
    na: "Not applicable",
    queued: "Queued",
    jobRunning: "Check in progress",
    cancelling: "Stopping check",
    completed: "Check completed",
    cancelled: "Check stopped",
    failed: "Check failed",
    requestFailed: "Backend request failed.",
    analyzed: "Analyzed",
  },
} as const;

const CHECK_COPY = {
  ru: {
    readme: ["README", "В корне репозитория есть содержательный README.", "Добавьте или дополните README: назначение проекта, запуск, основные возможности и пример использования."],
    license: ["Лицензия", "В репозитории есть файл лицензии.", "Добавьте LICENSE с подходящей открытой лицензией и убедитесь, что она соответствует условиям проекта."],
    commits: ["История разработки", "В истории репозитория не менее пяти коммитов.", "Сохраняйте этапы разработки отдельными осмысленными коммитами. Для выполнения критерия нужно минимум пять."],
    execution_files: ["Точка запуска", "В проекте удалось определить точку запуска.", "Добавьте понятную точку запуска и явно опишите команду запуска в README."],
    requirements: ["Зависимости", "Зависимости проекта зафиксированы в requirements.txt или pyproject.toml.", "Добавьте requirements.txt или pyproject.toml с воспроизводимым набором зависимостей."],
    tests: ["Тесты", "В приложении обнаружены тесты.", "Добавьте автоматические тесты для основной логики и инструкции по их запуску."],
    data_files: ["Данные", "Для экспериментального проекта найдены используемые данные или связанные с ними файлы.", "Добавьте данные, ссылки на них или понятную инструкцию по получению и подготовке данных."],
    experiment_scripts: ["Экспериментальные сценарии", "Найдены скрипты, позволяющие воспроизвести эксперименты.", "Добавьте отдельные скрипты/команды экспериментов и опишите последовательность их запуска."],
    syntax: ["Синтаксис Python", "Проверка синтаксиса Python-файлов.", "Исправьте найденные синтаксические ошибки."],
    docstrings: ["Документация кода", "Оценка покрытия функций и классов docstrings.", "Добавьте docstrings к публичным функциям, классам и сложной логике, если покрытие недостаточно."],
  },
  en: {
    readme: ["README", "A meaningful README is present at the repository root.", "Add or improve the README with the project purpose, setup, main features, and a usage example."],
    license: ["License", "A license file is present in the repository.", "Add a LICENSE file with an appropriate open-source license."],
    commits: ["Development history", "The repository contains at least five commits.", "Keep meaningful development stages as separate commits. At least five commits are required for this criterion."],
    execution_files: ["Entry point", "A project entry point can be identified.", "Add a clear entry point and document the launch command in the README."],
    requirements: ["Dependencies", "Project dependencies are declared in requirements.txt or pyproject.toml.", "Add requirements.txt or pyproject.toml with a reproducible dependency set."],
    tests: ["Tests", "Automated tests were found for the application.", "Add automated tests for the core logic and document how to run them."],
    data_files: ["Data", "Data files or related artifacts were found for the experimental project.", "Add the data, links to it, or clear instructions for obtaining and preparing it."],
    experiment_scripts: ["Experiment scripts", "Scripts that can reproduce the experiments were found.", "Add dedicated experiment scripts/commands and document the execution sequence."],
    syntax: ["Python syntax", "Checks Python files for syntax errors.", "Fix the reported syntax errors."],
    docstrings: ["Code documentation", "Measures docstring coverage for functions and classes.", "Add docstrings to public functions, classes, and non-trivial logic where coverage is low."],
  },
} as const;

const TYPE_LABELS = {
  ru: {
    app: "Приложение",
    algorithm_experiments: "Алгоритмические эксперименты",
    model_training_experiments: "Обучение моделей / ML-эксперименты",
    unknown: "Тип не определён",
  },
  en: {
    app: "Application",
    algorithm_experiments: "Algorithm experiments",
    model_training_experiments: "Model training / ML experiments",
    unknown: "Unknown type",
  },
} as const;

export function RepositoryQualityPage() {
  const { language } = useLanguage();
  const t = COPY[language];
  const [repo, setRepo] = useState("");
  const [jobs, setJobs] = useState<RepositoryQualityJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [serviceStatus, setServiceStatus] = useState<RepositoryQualityStatus | null>(null);
  const [preflight, setPreflight] = useState<RepositoryQualityPreflight | null>(null);
  const [preflightBusy, setPreflightBusy] = useState(false);
  const [raw, setRaw] = useState<unknown>(null);
  const [error, setError] = useState("");
  const [liveLog, setLiveLog] = useState("");
  const [logTruncated, setLogTruncated] = useState(false);

  const selectedJob = useMemo(
    () => selectedJobId ? jobs.find((item) => item.id === selectedJobId) ?? null : jobs[0] ?? null,
    [jobs, selectedJobId],
  );
  const jobActive = Boolean(selectedJob && ["queued", "running", "cancelling"].includes(selectedJob.status));
  const report = useMemo(() => raw ? normalizeReport(raw, language) : null, [raw, language]);
  const displayLog = useMemo(() => cleanOsaLog(liveLog), [liveLog]);

  useEffect(() => {
    let disposed = false;
    const boot = async () => {
      try {
        const [status, list] = await Promise.all([api.repositoryQualityStatus(), api.repositoryQualityJobs()]);
        if (disposed) return;
        setServiceStatus(status);
        setJobs(list);
        if (!selectedJobId && list[0]) setSelectedJobId(list[0].id);
      } catch (bootError) {
        if (!disposed) setError(errorMessage(bootError, language));
      }
    };
    void boot();
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    let disposed = false;
    const poll = async () => {
      try {
        const list = await api.repositoryQualityJobs();
        if (disposed) return;
        setJobs(list);
        if (!selectedJobId && list[0]) setSelectedJobId(list[0].id);
      } catch {
        // background polling is best-effort
      }
    };
    void poll();
    const hasActive = jobs.some((item) => ["queued", "running", "cancelling"].includes(item.status));
    const timer = window.setInterval(() => void poll(), hasActive ? 1800 : 5000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [jobs, selectedJobId]);

  useEffect(() => {
    if (!selectedJob || selectedJob.status !== "completed") {
      setRaw(null);
      return;
    }
    let disposed = false;
    void api.repositoryQualityResult(selectedJob.id)
      .then((value) => {
        if (disposed) return;
        setRaw(value);
        setRepo((current) => current || selectedJob.repository || "");
      })
      .catch((resultError) => { if (!disposed) setError(errorMessage(resultError, language)); });
    return () => { disposed = true; };
  }, [selectedJob?.id, selectedJob?.status]);

  useEffect(() => {
    if (!selectedJob) { setLiveLog(""); setLogTruncated(false); return; }
    let disposed = false;
    const pollLog = async () => {
      try {
        const value = await api.repositoryQualityLog(selectedJob.id);
        if (disposed) return;
        setLiveLog(value.text || "");
        setLogTruncated(Boolean(value.truncated));
      } catch {
        // log may not exist yet
      }
    };
    void pollLog();
    const timer = window.setInterval(() => void pollLog(), jobActive ? 1500 : 6000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [selectedJob?.id, jobActive]);

  function upsertJob(job: RepositoryQualityJob) {
    setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    setSelectedJobId(job.id);
  }

  async function checkEnvironment(): Promise<RepositoryQualityPreflight | null> {
    if (!repo.trim()) { setError(t.enterRepository); return null; }
    setPreflightBusy(true);
    setError("");
    try {
      const result = await api.repositoryQualityPreflight(repo.trim());
      setPreflight(result);
      if (!result.ok) {
        const failed = result.checks.filter((item) => item.blocking && !item.ok);
        setError(failed.length ? `${t.preflightFailed}: ${failed.map((item) => item.label).join(", ")}.` : t.environmentNotReady);
      }
      return result;
    } catch (preflightError) {
      setError(errorMessage(preflightError, language));
      return null;
    } finally {
      setPreflightBusy(false);
    }
  }

  async function runCheck() {
    if (!repo.trim()) { setError(t.enterRepository); return; }
    const readiness = await checkEnvironment();
    if (!readiness?.ok) return;
    const body = new FormData();
    body.append("repository", repo.trim());
    setError("");
    setRaw(null);
    setLiveLog("");
    try {
      upsertJob(await api.createRepositoryQualityJob(body));
    } catch (runError) {
      setError(errorMessage(runError, language));
    }
  }

  async function cancelCheck() {
    if (!selectedJob || !jobActive) return;
    try { upsertJob(await api.cancelRepositoryQualityJob(selectedJob.id)); }
    catch (cancelError) { setError(errorMessage(cancelError, language)); }
  }

  async function retryCheck() {
    if (!selectedJob) return;
    try {
      setRaw(null);
      setError("");
      setLiveLog("");
      upsertJob(await api.retryRepositoryQualityJob(selectedJob.id));
    } catch (retryError) { setError(errorMessage(retryError, language)); }
  }

  async function deleteCheck() {
    if (!selectedJob || jobActive) return;
    try {
      await api.deleteRepositoryQualityJob(selectedJob.id);
      const remaining = jobs.filter((item) => item.id !== selectedJob.id);
      setJobs(remaining);
      setSelectedJobId(remaining[0]?.id ?? null);
      setRaw(null);
    } catch (deleteError) { setError(errorMessage(deleteError, language)); }
  }

  return <section className="page narrow repository-quality-page">
    <header className="rq-hero">
      <h1>{t.title}</h1>
      <p>{t.subtitle}</p>
    </header>

    {serviceStatus && (!serviceStatus.osaInstalled || !serviceStatus.llmConfigured || serviceStatus.gitInstalled === false) && <div className="inline-error rq-inline-message">
      <strong>{t.backendNotReady}</strong>{" "}
      {!serviceStatus.osaInstalled ? `${t.osaMissing} ` : ""}{!serviceStatus.llmConfigured ? `${t.llmMissing} ` : ""}{serviceStatus.gitInstalled === false ? t.gitMissing : ""}
    </div>}

    <div className="panel rq-launch-card">
      <label className="rq-repository-field">
        <span>{t.repository}</span>
        <input value={repo} onChange={(event) => setRepo(event.target.value)} placeholder="https://github.com/owner/project" />
      </label>
      <div className="rq-launch-actions">
        <button className="button primary" type="button" onClick={() => void runCheck()} disabled={!repo.trim() || jobActive || preflightBusy}>{jobActive ? t.running : t.run}</button>
      </div>
    </div>

    {preflight && !preflight.ok && <details className="panel rq-preflight" open>
      <summary><strong>{preflight.ok ? t.ready : t.environmentIssues}</strong><span>{preflight.model}</span></summary>
      <div className="rq-preflight-list">{preflight.checks.map((check) => <div key={check.id} className={`rq-preflight-item ${check.ok ? "ok" : check.blocking ? "bad" : "warn"}`}><b>{check.ok ? "✓" : check.blocking ? "×" : "!"}</b><div><strong>{preflightLabel(check.id, check.label, language)}</strong><span>{preflightDetail(check.id, check.detail, check.ok, language)}</span></div></div>)}</div>
    </details>}

    {error && <div className="inline-error rq-inline-message">{error}</div>}

    {selectedJob && <section className="panel rq-job-state">
      <div className="rq-job-copy">
        <h2>{shortRepository(selectedJob.repository)}</h2>
        <p><strong>{jobStatusLabel(selectedJob.status, language)}</strong>{jobActive ? ` · ${Math.round(selectedJob.progress || 0)}%` : ""}</p>
        {selectedJob.progressMessage && <span>{progressMessage(selectedJob.progressMessage, language)}</span>}
      </div>
      {jobActive && <progress max="100" value={selectedJob.progress || 0} />}
      {selectedJob.error && <div className="inline-error">{selectedJob.error}</div>}
      <div className="rq-job-actions">
        {jobActive && <button className="button secondary" type="button" onClick={() => void cancelCheck()}>{t.stop}</button>}
        {!jobActive && selectedJob.status !== "completed" && <button className="button primary" type="button" onClick={() => void retryCheck()}>{t.retry}</button>}
        {!jobActive && <button className="text-button" type="button" onClick={() => void deleteCheck()}>{t.deleteRun}</button>}
      </div>
    </section>}

    {report && <>
      <section className="panel rq-result-summary">
        <div className="rq-result-main">
          <span className="rq-result-kicker">{shortRepository(report.repository)}</span>
          <div className="rq-score-line"><strong>{report.score}</strong><span>/100</span></div>
          <span className="rq-score-caption">{t.score}</span>
        </div>
        <div className="rq-result-meta">
          <div><span>{t.type}</span><strong>{report.repoTypeLabel}</strong></div>
          {report.repoTypeConfidence && <div><span>{capitalize(t.confidence)}</span><strong>{confidenceLabel(report.repoTypeConfidence, language)}</strong></div>}
          {report.analyzedAt && <div><span>{t.analyzed}</span><strong>{formatDateTime(report.analyzedAt, language)}</strong></div>}
        </div>
      </section>

      {(report.repoTypeError || report.repoTypeConfidence === "low") && <div className="rq-warning panel">
        <strong>{t.uncertainType}</strong>
        <span>{t.uncertainTypeText}</span>
      </div>}

      <section className="rq-criteria-section">
        <div className="rq-section-heading rq-section-heading-inline">
          <div><h2>{t.criteriaTitle}</h2>{t.criteriaHint && <p>{t.criteriaHint}</p>}</div>
          {selectedJob && <a className="text-button rq-json-link" href={api.repositoryQualityResultJsonUrl(selectedJob.id)}>{t.downloadJson}</a>}
        </div>
        <div className="panel rq-check-list">
        {report.checks.map((check) => <article key={check.key} className={`rq-check-card ${check.tone}`}>
          <div className="rq-check-head">
            <div className="rq-check-heading"><span className={`status-badge ${toneClass(check.tone)}`}>{checkStatusLabel(check.tone, language)}</span><h3>{check.label}</h3></div>
            {typeof check.weight === "number" && <strong className="rq-points">{check.earned ?? 0}/{check.weight}</strong>}
          </div>
          <p className="rq-check-description">{check.description}</p>
          <div className="rq-check-status"><span>{t.result}</span><p>{check.status}</p></div>
          {check.details && <p className="rq-check-details">{check.details}</p>}
          {check.recommendation && check.tone !== "pass" && check.tone !== "na" && <div className="rq-recommendation"><strong>{check.tone === "uncertain" ? t.whatToCheck : t.whatToDo}</strong><span>{check.recommendation}</span></div>}
        </article>)}
        </div>
      </section>

      {report.informational.length > 0 && <section className="rq-info-section">
        <div className="rq-section-heading"><span>{t.infoKicker}</span><h2>{t.infoTitle}</h2></div>
        <div className="panel rq-info-list">{report.informational.map((check) => <div key={check.key} className="rq-info-card"><strong>{check.label}</strong><p>{check.status}</p>{check.details && <span>{check.details}</span>}</div>)}</div>
      </section>}
    </>}

    <details className="panel rq-technical">
      <summary>{t.history}</summary>
      <div className="rq-technical-body">
        <div className="jobs-list rq-history-list">
          {jobs.map((item) => <button key={item.id} className={`job-row ${selectedJob?.id === item.id ? "selected" : ""}`} type="button" onClick={() => setSelectedJobId(item.id)}><strong>{shortRepository(item.repository)}</strong><span>{jobStatusLabel(item.status, language)} · {formatDateTime(item.createdAt, language)}</span></button>)}
          {!jobs.length && <div className="empty small">{t.noRuns}</div>}
        </div>
        {selectedJob && <label className="rq-log-field"><span>{t.osaLog}</span><textarea readOnly value={displayLog || t.logPlaceholder} rows={10} /><small>{logTruncated ? t.logTruncated : ""}</small></label>}
        {selectedJob?.logAvailable && <a className="button secondary rq-log-download" href={api.repositoryQualityLogDownloadUrl(selectedJob.id)} target="_blank" rel="noreferrer">{t.downloadLog}</a>}
      </div>
    </details>
  </section>;
}

function normalizeReport(raw: unknown, language: UiLanguage): QualityReportView {
  const root = asRecord(raw);
  const report = Object.keys(asRecord(root.result)).length ? asRecord(root.result) : root;
  const summary = asRecord(report.summary);
  const checks = asRecord(report.checks);
  const breakdown = asRecord(summary.score_breakdown);
  const repoTypeRaw = asRecord(checks.repo_type);
  const repoType = pickString(summary, ["repo_type"]) || pickString(repoTypeRaw, ["value"]) || "unknown";

  const weighted: QualityCheckView[] = Object.entries(breakdown).map(([key, rawBreakdown]) => {
    const scorePart = asRecord(rawBreakdown);
    return buildCheckView(key, asRecord(checks[key]), scorePart, language);
  });
  const informational = ["syntax", "docstrings"].filter((key) => checks[key]).map((key) => buildCheckView(key, asRecord(checks[key]), {}, language, true));
  const typeLabels = TYPE_LABELS[language] as Record<string, string>;

  return {
    repository: pickString(report, ["repo_url"]) || (language === "ru" ? "Репозиторий" : "Repository"),
    analyzedAt: pickString(report, ["analyzed_at"]),
    score: pickNumber(summary, ["score"]) ?? 0,
    repoType,
    repoTypeLabel: typeLabels[repoType] || humanize(repoType),
    repoTypeConfidence: pickString(repoTypeRaw, ["confidence"]),
    repoTypeError: pickString(repoTypeRaw, ["error"]),
    checks: weighted,
    informational,
    raw,
  };
}

function buildCheckView(key: string, raw: Record<string, any>, score: Record<string, any>, language: UiLanguage, informational = false): QualityCheckView {
  const languageCopy = CHECK_COPY[language] as Record<string, readonly [string, string, string]>;
  const fallback: readonly [string, string, string] = language === "ru"
    ? [humanize(key), "Проверка критерия.", "Проверьте критерий вручную."]
    : [humanize(key), "Criterion check.", "Review this criterion manually."];
  const entry = languageCopy[key] || fallback;
  const copy = { label: entry[0], description: entry[1], recommendation: entry[2] };
  const applicable = raw.applicable !== false && score.applicable !== false;
  const llmError = pickString(raw, ["error"]);
  const passed = typeof score.passed === "boolean" ? score.passed : inferPassed(key, raw);
  let tone: CheckTone = applicable ? (passed ? "pass" : "fail") : "na";
  if (llmError) tone = "uncertain";

  let status = language === "ru"
    ? (tone === "pass" ? "Критерий выполнен." : tone === "na" ? "Критерий не применяется к этому типу репозитория." : "Критерий не выполнен.")
    : (tone === "pass" ? "Criterion passed." : tone === "na" ? "This criterion does not apply to this repository type." : "Criterion not met.");
  if (llmError === "llm_failed") status = language === "ru" ? "Не удалось однозначно определить результат этой проверки." : "This result could not be determined reliably.";
  else if (llmError) status = language === "ru" ? `Проверка завершилась неоднозначно: ${llmError}.` : `The check was inconclusive: ${llmError}.`;

  if (key === "readme") {
    if (!raw.present) status = language === "ru" ? "README в корне репозитория не найден." : "No README was found at the repository root.";
    else if (raw.meaningful === false) status = language === "ru" ? "README найден, но он недостаточно подробный." : "README was found, but it is not detailed enough.";
    else if (tone === "pass") status = language === "ru" ? "README найден." : "README found.";
  } else if (key === "commits" && typeof raw.count === "number") {
    status = language === "ru"
      ? (passed ? `Найдено ${raw.count} коммитов.` : `Найдено ${raw.count} коммитов. Для выполнения критерия нужно минимум 5.`)
      : (passed ? `${raw.count} commits found.` : `${raw.count} commits found. At least 5 are required for this criterion.`);
  } else if (pickString(raw, ["matched_file"])) {
    status = language === "ru" ? `${passed ? "Найден" : "Проверен"} файл: ${pickString(raw, ["matched_file"])}.` : `${passed ? "Found" : "Checked"}: ${pickString(raw, ["matched_file"])}.`;
  } else if (Array.isArray(raw.verified) && raw.verified.length) {
    status = language === "ru" ? `${passed ? "Подтверждено" : "Найдены кандидаты"}: ${raw.verified.slice(0, 4).join(", ")}.` : `${passed ? "Verified" : "Candidates found"}: ${raw.verified.slice(0, 4).join(", ")}.`;
  } else if (informational && pickString(raw, ["summary"])) {
    status = pickString(raw, ["summary"]) || status;
  }

  const details = buildDetails(raw, language);
  const recommendation = tone === "uncertain"
    ? `${language === "ru" ? "Проверьте этот критерий вручную." : "Review this criterion manually."} ${copy.recommendation}`
    : copy.recommendation;

  return {
    key,
    label: copy.label,
    description: copy.description,
    tone,
    status,
    details,
    recommendation,
    weight: pickNumber(score, ["weight"]),
    earned: pickNumber(score, ["earned"]),
    informational,
    raw,
  };
}

function inferPassed(key: string, raw: Record<string, any>): boolean {
  if (key === "readme") return Boolean(raw.present && raw.meaningful);
  return Boolean(raw.present);
}

function buildDetails(raw: Record<string, any>, language: UiLanguage): string | undefined {
  const parts: string[] = [];
  const reason = pickString(raw, ["reason", "detail", "message"]);
  if (reason) parts.push(reason);
  if (Array.isArray(raw.verified) && raw.verified.length) parts.push(`${language === "ru" ? "Файлы" : "Files"}: ${raw.verified.slice(0, 6).join(", ")}.`);
  if (Array.isArray(raw.missing) && raw.missing.length) parts.push(`${language === "ru" ? "Не найдено" : "Missing"}: ${raw.missing.slice(0, 6).join(", ")}.`);
  return parts.length ? parts.join(" ") : undefined;
}

function toneClass(tone: CheckTone): "pass" | "violation" | "uncertain" | "not_applicable" {
  if (tone === "pass") return "pass";
  if (tone === "fail") return "violation";
  if (tone === "uncertain") return "uncertain";
  return "not_applicable";
}

function checkStatusLabel(tone: CheckTone, language: UiLanguage): string {
  const t = COPY[language];
  if (tone === "pass") return t.passed;
  if (tone === "fail") return t.needsWork;
  if (tone === "uncertain") return t.manual;
  return t.na;
}

function jobStatusLabel(status: RepositoryQualityJob["status"], language: UiLanguage): string {
  const t = COPY[language];
  if (status === "queued") return t.queued;
  if (status === "running") return t.jobRunning;
  if (status === "cancelling") return t.cancelling;
  if (status === "completed") return t.completed;
  if (status === "cancelled") return t.cancelled;
  return t.failed;
}

function preflightLabel(id: string, fallback: string, language: UiLanguage): string {
  if (language === "ru") return fallback;
  if (id === "repository") return "Repository";
  if (id === "osa") return "OSA";
  if (id === "git") return "Git";
  if (id === "llm") return "LLM";
  return fallback;
}

function preflightDetail(id: string, fallback: string, ok: boolean, language: UiLanguage): string {
  if (language === "ru" || !ok) return fallback;
  if (id === "repository") return "Repository is accessible through Git.";
  if (id === "llm") return "LLM access is configured and available.";
  return fallback;
}

function progressMessage(value: string, language: UiLanguage): string {
  if (language === "ru") return value;
  const known: Array<[RegExp, string]> = [
    [/добавлена в очередь/i, "Queued for repository quality analysis."],
    [/запускаем штатный OSA repository_quality/i, "Starting the OSA repository_quality pipeline."],
    [/OSA запущена/i, "OSA is running."],
    [/клонирует репозиторий/i, "OSA is cloning the repository."],
    [/репозиторий загружен/i, "Repository cloned."],
    [/строит дерево файлов/i, "OSA is building the file tree."],
    [/проверяет структуру и качество репозитория/i, "OSA is evaluating repository structure and quality."],
    [/формальные и LLM-проверки/i, "Formal and LLM-based checks are running."],
    [/формирует итоговый отч[её]т/i, "OSA is preparing the final report."],
    [/результат OSA получен/i, "OSA result received."],
  ];
  for (const [pattern, translated] of known) if (pattern.test(value)) return translated;
  return value;
}

function shortRepository(value: string): string {
  try {
    const url = new URL(value);
    return url.pathname.replace(/^\//, "").replace(/\.git$/, "") || value;
  } catch {
    return value;
  }
}

function confidenceLabel(value: string, language: UiLanguage): string {
  const normalized = value.toLowerCase();
  if (language === "ru") {
    if (normalized === "high") return "высокая";
    if (normalized === "medium") return "средняя";
    if (normalized === "low") return "низкая";
  } else {
    if (normalized === "high") return "high";
    if (normalized === "medium") return "medium";
    if (normalized === "low") return "low";
  }
  return value;
}

function humanize(value: string): string {
  const normalized = value.replace(/_/g, " ");
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function asRecord(value: unknown): Record<string, any> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, any> : {};
}

function pickString(record: Record<string, any>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function pickNumber(record: Record<string, any>, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return undefined;
}

function errorMessage(error: unknown, language: UiLanguage): string {
  return error instanceof Error && error.message ? error.message : COPY[language].requestFailed;
}

function formatDateTime(value: string | null | undefined, language: UiLanguage): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(language === "ru" ? "ru-RU" : "en-GB", { dateStyle: "short", timeStyle: "short" });
}

function cleanOsaLog(value: string): string {
  if (!value) return "";
  const withoutAnsi = value.replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "").replace(/\r/g, "\n");
  const lines = withoutAnsi.split("\n");
  const kept: string[] = [];
  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (!line.trim()) continue;
    if (/^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/.test(line) && /(Cloning repository|Calculating formal repository quality|Preparing repository-quality score)/.test(line)) continue;
    if (kept.at(-1) === line) continue;
    kept.push(line);
  }
  return kept.join("\n");
}
