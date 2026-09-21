import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api";
import { useLanguage } from "../i18n";
import type {
  ReproducibilityJob,
  ReproducibilityPreflight,
  ReproducibilityStatus,
} from "../types";

type UiLang = "ru" | "en";
type ClaimTone = "confirmed" | "partial" | "rejected" | "uncertain";
type ClaimFilter = "checked" | "confirmed" | "rejected" | "extracted";

interface NormalizedEvidence {
  path?: string;
  details?: string;
}

interface NormalizedClaim {
  id: string;
  text: string;
  originalText?: string;
  category?: string;
  section?: string;
  verifiability?: string;
  implementationConfidence?: string;
  contradiction: boolean;
  tone: ClaimTone;
  explanation?: string;
  evidence: NormalizedEvidence[];
  raw: unknown;
}

interface NormalizedAnalysis {
  title: string;
  repository?: string;
  paperPath?: string;
  extractionModel?: string;
  verificationModel?: string;
  claims: NormalizedClaim[];
  stats: {
    sourceTotal: number;
    eligibleTotal: number;
    scoredTotal: number;
    implemented: number;
    notImplemented: number;
    excluded: number;
    hiddenLowConfidence: number;
    implementationRatePct: number;
    uncertain: number;
  };
}


const COPY = {
  ru: {
    title: "Воспроизводимость",
    subtitle: "Проверка того, насколько утверждения из ВКР подтверждаются кодом и материалами репозитория.",
    repo: "Репозиторий",
    paper: "PDF работы",
    stats: "Статистика",
    allClaims: "Выделено",
    eligible: "Проверено",
    confirmed: "Подтверждено",
    rejected: "Не подтверждено",
    excluded: "Исключено",
    uncertain: "Неопределённо",
    claims: "Проверенные утверждения",
    details: "Почему так решено",
    evidence: "Подтверждение в репозитории",
    sourceText: "Исходный фрагмент ВКР",
    raw: "Исходные данные claim",
    category: "Категория",
    section: "Раздел",
    verifiability: "Проверяемость",
    confidence: "Уверенность",
    modelExtraction: "Извлечение claims",
    modelVerification: "Проверка по коду",
  },
  en: {
    title: "Reproducibility",
    subtitle: "Check how well the claims made in the thesis are supported by the repository code and artifacts.",
    repo: "Repository",
    paper: "Paper PDF",
    stats: "Statistics",
    allClaims: "Extracted",
    eligible: "Verified",
    confirmed: "Confirmed",
    rejected: "Not confirmed",
    excluded: "Excluded",
    uncertain: "Inconclusive",
    claims: "Verified claims",
    details: "Verification rationale",
    evidence: "Repository evidence",
    sourceText: "Original paper text",
    raw: "Raw claim data",
    category: "Category",
    section: "Section",
    verifiability: "Verifiability",
    confidence: "Confidence",
    modelExtraction: "Claim extraction",
    modelVerification: "Code verification",
  },
} as const;
const REPRO_UI = {
  ru: {
    pageTitle: "Проверка воспроизводимости", pageSubtitle: "Сопоставляет технические утверждения из ВКР с кодом указанного репозитория.",
    backendNotReady: "Backend не готов к запуску OSA.", osaMissing: "Не установлен osa_tool.", llmMissing: "Не настроен API-ключ LLM.", pythonUnsupported: "не поддерживается paper-claims.", gitMissing: "git не найден в PATH.",
    repoHint: "GitHub, GitLab, GitVerse или SourceCraft.", pdfOnly: "Поддерживается только PDF.", dropPdf: "Перетащите PDF работы", choosePdf: "или нажмите, чтобы выбрать файл", file: "Файл", clear: "Очистить", remove: "Удалить",
    openJson: "Открыть готовый JSON", running: "Проверка выполняется…", run: "Проверить воспроизводимость", savedClaims: "Claims уже сохранены", savedClaimsSuffix: "Проверку по репозиторию можно продолжить без повторной обработки PDF.", stop: "Остановить", resume: "Продолжить проверку", restart: "Начать заново",
    historyTech: "История и технические детали", runs: "Запуски", check: "Проверка", noRuns: "Запусков пока нет.", checking: "Проверяем…", checkEnvironment: "Проверить окружение", downloadLog: "Скачать лог", environmentReady: "Окружение готово", environmentProblems: "Есть проблемы с окружением",
    sections: "Разделы", claims: "Claims", verified: "Проверено", result: "Результат", readyPlural: "готовы", readyOne: "готово", resultReady: "готов", osaLog: "Лог OSA", logPlaceholder: "Лог появится после запуска OSA.", logTail: "Показана последняя часть лога.",
    resultSubtitle: "Результат проверки воспроизводимости", confirmedPct: "подтверждено", allClaims: "Все claims", checked: "Проверено", confirmed: "Подтверждено", rejected: "Не подтверждено", excluded: "Исключено", checkedClaims: "Проверенные утверждения", noClaims: "В этой категории утверждений нет.", evidenceFound: "Найдено подтверждение в репозитории.",
    loadFailed: "Не удалось загрузить результат анализа.", enterRepo: "Укажите ссылку на репозиторий.", preflightFailed: "Не пройдена предварительная проверка", envNotReady: "Окружение не готово к запуску OSA.", choosePaper: "Выберите PDF работы.", requestFailed: "Не удалось выполнить запрос к backend.",
    statusQueued: "В очереди OSA", statusRunning: "Проверка выполняется", statusCancelling: "Останавливаем проверку", statusCompleted: "Проверка завершена", statusCancelled: "Проверка остановлена", statusFailed: "Проверка завершилась с ошибкой"
  },
  en: {
    pageTitle: "Reproducibility check", pageSubtitle: "Compares technical claims from the thesis with the code in the selected repository.",
    backendNotReady: "The backend is not ready to run OSA.", osaMissing: "osa_tool is not installed.", llmMissing: "The LLM API key is not configured.", pythonUnsupported: "does not support paper-claims.", gitMissing: "git was not found in PATH.",
    repoHint: "GitHub, GitLab, GitVerse, or SourceCraft.", pdfOnly: "Only PDF files are supported.", dropPdf: "Drop the thesis PDF here", choosePdf: "or click to choose a file", file: "File", clear: "Clear", remove: "Remove",
    openJson: "Open existing JSON", running: "Check in progress…", run: "Check reproducibility", savedClaims: "Claims are already saved", savedClaimsSuffix: "Repository verification can continue without processing the PDF again.", stop: "Stop", resume: "Continue verification", restart: "Start over",
    historyTech: "History and technical details", runs: "Runs", check: "Check", noRuns: "No runs yet.", checking: "Checking…", checkEnvironment: "Check environment", downloadLog: "Download log", environmentReady: "Environment is ready", environmentProblems: "Environment has issues",
    sections: "Sections", claims: "Claims", verified: "Verified", result: "Result", readyPlural: "ready", readyOne: "ready", resultReady: "ready", osaLog: "OSA log", logPlaceholder: "The log will appear after OSA starts.", logTail: "Showing the latest part of the log.",
    resultSubtitle: "Reproducibility check result", confirmedPct: "confirmed", allClaims: "All claims", checked: "Verified", confirmed: "Confirmed", rejected: "Not confirmed", excluded: "Excluded", checkedClaims: "Verified claims", noClaims: "No claims in this category.", evidenceFound: "Supporting evidence was found in the repository.",
    loadFailed: "Could not load the analysis result.", enterRepo: "Enter a repository URL.", preflightFailed: "Preflight check failed", envNotReady: "The environment is not ready to run OSA.", choosePaper: "Choose the thesis PDF.", requestFailed: "Could not complete the backend request.",
    statusQueued: "Queued for OSA", statusRunning: "Check in progress", statusCancelling: "Stopping the check", statusCompleted: "Check completed", statusCancelled: "Check stopped", statusFailed: "Check failed"
  }
} as const;


export function ReproducibilityPage() {
  const { language } = useLanguage();
  const u = REPRO_UI[language];
  const [repo, setRepo] = useState("");
  const [paperName, setPaperName] = useState("");
  const [paperFile, setPaperFile] = useState<File | null>(null);
  const [jsonName, setJsonName] = useState("");
  const [raw, setRaw] = useState<unknown>(null);
  const [error, setError] = useState("");
  const [printLang, setPrintLang] = useState<UiLang>(language);
  const [claimFilter, setClaimFilter] = useState<ClaimFilter>("checked");
  const [jobs, setJobs] = useState<ReproducibilityJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [serviceStatus, setServiceStatus] = useState<ReproducibilityStatus | null>(null);
  const [preflight, setPreflight] = useState<ReproducibilityPreflight | null>(null);
  const [preflightBusy, setPreflightBusy] = useState(false);
  const [liveLog, setLiveLog] = useState("");
  const [logTruncated, setLogTruncated] = useState(false);
  const pdfInput = useRef<HTMLInputElement>(null);
  const jsonInput = useRef<HTMLInputElement>(null);

  const selectedJob = useMemo(
    () => (selectedJobId ? jobs.find((item) => item.id === selectedJobId) ?? null : jobs[0] ?? null),
    [jobs, selectedJobId],
  );
  const jobActive = Boolean(selectedJob && ["queued", "running", "cancelling"].includes(selectedJob.status));
  const analysis = useMemo(() => raw ? normalizeAnalysis(raw, paperName) : null, [raw, paperName]);
  const t = COPY[printLang];

  useEffect(() => { setPrintLang(language); }, [language]);

  const filteredClaims = useMemo(() => {
    if (!analysis) return [];
    if (claimFilter === "confirmed") return analysis.claims.filter((claim) => claim.tone === "confirmed");
    if (claimFilter === "rejected") return analysis.claims.filter((claim) => claim.tone === "rejected");
    return analysis.claims;
  }, [analysis, claimFilter]);

  useEffect(() => {
    let cancelled = false;
    const boot = async () => {
      try {
        const [status, list] = await Promise.all([api.reproducibilityStatus(), api.reproducibilityJobs()]);
        if (cancelled) return;
        setServiceStatus(status);
        setJobs(list);
        if (!selectedJobId && list[0]) setSelectedJobId(list[0].id);
      } catch {
        if (!cancelled) setServiceStatus(null);
      }
    };
    void boot();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let disposed = false;
    const poll = async () => {
      try {
        const list = await api.reproducibilityJobs();
        if (disposed) return;
        setJobs(list);
        if (!selectedJobId && list[0]) setSelectedJobId(list[0].id);
      } catch {
        // ignore background polling failures
      }
    };
    void poll();
    const hasActive = jobs.some((item) => ["queued", "running", "cancelling"].includes(item.status));
    const timer = window.setInterval(() => void poll(), hasActive ? 2000 : 5000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [jobs, selectedJobId]);

  useEffect(() => {
    if (!selectedJob) {
      setLiveLog("");
      setLogTruncated(false);
      return;
    }
    let disposed = false;
    const pollLog = async () => {
      try {
        const value = await api.reproducibilityLog(selectedJob.id);
        if (disposed) return;
        setLiveLog(value.text || "");
        setLogTruncated(Boolean(value.truncated));
      } catch {
        // log may not exist yet
      }
    };
    void pollLog();
    const timer = window.setInterval(() => void pollLog(), jobActive ? 1500 : 6000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [selectedJob?.id, jobActive]);

  useEffect(() => {
    if (!selectedJob) {
      setRaw(null);
      setJsonName("");
      return;
    }
    if (selectedJob.status !== "completed") {
      setRaw(null);
      setJsonName("");
      return;
    }
    let disposed = false;
    void api.reproducibilityResult(selectedJob.id)
      .then((result) => {
        if (disposed) return;
        const normalized = normalizeAnalysis(result, selectedJob.originalName || paperName);
        setRaw(result);
        setJsonName("paper_analysis.json");
        setClaimFilter("checked");
        if (normalized.repository) setRepo((current) => current || normalized.repository || "");
        setPaperName(selectedJob.originalName || (normalized.paperPath ? basename(normalized.paperPath) : ""));
      })
      .catch((resultError) => {
        if (!disposed) setError(errorMessage(resultError, language));
      });
    return () => { disposed = true; };
  }, [selectedJob?.id, selectedJob?.status]);

  function upsertJob(job: ReproducibilityJob) {
    setJobs((current) => {
      const others = current.filter((item) => item.id !== job.id);
      return [job, ...others];
    });
    setSelectedJobId(job.id);
  }

  async function loadJson(file?: File) {
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      const normalized = normalizeAnalysis(parsed, paperName);
      setRaw(parsed);
      setJsonName(file.name);
      setError("");
      setClaimFilter("checked");
      if (normalized.repository) setRepo((current) => current || normalized.repository || "");
      if (normalized.paperPath) setPaperName((current) => current || basename(normalized.paperPath || ""));
    } catch {
      setError(u.loadFailed);
    }
  }

  async function checkEnvironment(): Promise<ReproducibilityPreflight | null> {
    if (!repo.trim()) {
      setError(u.enterRepo);
      return null;
    }
    setPreflightBusy(true);
    setError("");
    try {
      const result = await api.reproducibilityPreflight(repo.trim());
      setPreflight(result);
      if (!result.ok) {
        const failed = result.checks.filter((item) => item.blocking && !item.ok);
        setError(failed.length ? `${u.preflightFailed}: ${failed.map((item) => item.label).join(", ")}.` : u.envNotReady);
      }
      return result;
    } catch (preflightError) {
      setError(errorMessage(preflightError, language));
      return null;
    } finally {
      setPreflightBusy(false);
    }
  }

  async function runAnalysis() {
    if (!repo.trim()) {
      setError(u.enterRepo);
      return;
    }
    if (!paperFile) {
      setError(u.choosePaper);
      return;
    }
    const readiness = await checkEnvironment();
    if (!readiness?.ok) return;

    const body = new FormData();
    body.append("repository", repo.trim());
    body.append("file", paperFile);
    setError("");
    setRaw(null);
    setJsonName("");
    setLiveLog("");
    setLogTruncated(false);
    setClaimFilter("checked");
    try {
      const created = await api.createReproducibilityJob(body);
      upsertJob(created);
    } catch (runError) {
      setError(errorMessage(runError, language));
    }
  }

  async function cancelAnalysis() {
    if (!selectedJob || !jobActive) return;
    try {
      upsertJob(await api.cancelReproducibilityJob(selectedJob.id));
    } catch (cancelError) {
      setError(errorMessage(cancelError, language));
    }
  }

  async function retryAnalysis() {
    if (!selectedJob) return;
    try {
      setError("");
      setRaw(null);
      setJsonName("");
      setLiveLog("");
      setLogTruncated(false);
      upsertJob(await api.retryReproducibilityJob(selectedJob.id));
    } catch (retryError) {
      setError(errorMessage(retryError, language));
    }
  }

  async function resumeVerification() {
    if (!selectedJob) return;
    try {
      setError("");
      setRaw(null);
      setJsonName("");
      setLiveLog("");
      setLogTruncated(false);
      upsertJob(await api.resumeReproducibilityJob(selectedJob.id));
    } catch (resumeError) {
      setError(errorMessage(resumeError, language));
    }
  }

  function downloadJson() {
    if (!raw) return;
    const blob = new Blob([JSON.stringify(raw, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = jsonName || "paper_analysis.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  function printPdf(lang: UiLang) {
    setPrintLang(lang);
    const oldTitle = document.title;
    const base = paperName ? paperName.replace(/\.pdf$/i, "") : "paper-analysis";
    document.title = `${base}-reproducibility-${lang}`;
    window.setTimeout(() => {
      window.print();
      window.setTimeout(() => {
        document.title = oldTitle;
        setPrintLang(language);
      }, 250);
    }, 80);
  }

  const artifacts = selectedJob?.artifacts;

  return <section className="page narrow reproducibility-page">
    <div className="page-title repro-no-print">
      <div>
        <h1>{u.pageTitle}</h1>
        <p>{u.pageSubtitle}</p>
      </div>
    </div>

    {serviceStatus && (!serviceStatus.osaInstalled || !serviceStatus.llmConfigured || serviceStatus.pythonSupportedForPaperClaims === false || serviceStatus.gitInstalled === false) && <div className="inline-error repro-no-print">
      {u.backendNotReady} {!serviceStatus.osaInstalled ? `${u.osaMissing} ` : ""}{!serviceStatus.llmConfigured ? `${u.llmMissing} ` : ""}{serviceStatus.pythonSupportedForPaperClaims === false ? `Python ${serviceStatus.pythonVersion || ""} ${u.pythonUnsupported} ` : ""}{serviceStatus.gitInstalled === false ? u.gitMissing : ""}
    </div>}

    <div className="panel repro-no-print">
      <label>
        <span>{COPY[language].repo}</span>
        <input value={repo} onChange={(event) => setRepo(event.target.value)} placeholder={language === "ru" ? "https://github.com/owner/project или https://sourcecraft.dev/owner/project" : "https://github.com/owner/project or https://sourcecraft.dev/owner/project"} />
        <small>{u.repoHint}</small>
      </label>
    </div>

    <div className="dropzone repro-no-print" onClick={() => !jobActive && pdfInput.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => {
      event.preventDefault();
      if (jobActive) return;
      const file = Array.from(event.dataTransfer.files).find((item) => /\.pdf$/i.test(item.name)) || null;
      if (!file) { setError(u.pdfOnly); return; }
      setPaperFile(file); setPaperName(file.name); setError("");
    }}>
      <input ref={pdfInput} type="file" accept=".pdf,application/pdf" onChange={(event) => {
        const file = event.target.files?.[0] || null;
        setPaperFile(file);
        setPaperName(file?.name || "");
      }} />
      <strong>{paperFile ? paperFile.name : u.dropPdf}</strong>
      <span>{paperFile ? formatFileSize(paperFile.size, language) : u.choosePdf}</span>
    </div>

    {paperFile && <div className="panel file-list repro-no-print">
      <div className="row between"><strong>{u.file}</strong><button className="text-button" type="button" onClick={() => { setPaperFile(null); setPaperName(""); }}>{u.clear}</button></div>
      <div className="file-item"><span>{paperFile.name}</span><small>{formatFileSize(paperFile.size, language)}</small><button aria-label={u.remove} type="button" onClick={() => { setPaperFile(null); setPaperName(""); }}>×</button></div>
    </div>}

    {error && <div className="inline-error repro-no-print">{error}</div>}

    <div className="actions end repro-no-print repro-main-actions">
      <button className="button secondary" type="button" onClick={() => jsonInput.current?.click()} disabled={jobActive}>{u.openJson}</button>
      <input ref={jsonInput} hidden type="file" accept=".json,application/json" onChange={(event) => void loadJson(event.target.files?.[0])} />
      <button className="button primary" type="button" onClick={() => void runAnalysis()} disabled={!repo.trim() || !paperFile || jobActive || preflightBusy}>
        {jobActive ? u.running : u.run}
      </button>
    </div>

    {selectedJob && <div className="panel job-state repro-no-print repro-job-state">
      <h1>{selectedJob.originalName || u.pageTitle}</h1>
      <p><b>{reproducibilityStatusLabel(selectedJob.status, language)}</b>{jobActive ? ` · ${Math.round(selectedJob.progress || 0)} %` : ""}</p>
      {selectedJob.progressMessage && <p className="progress-message">{selectedJob.progressMessage}</p>}
      {jobActive && <progress max="100" value={selectedJob.progress || 0} />}
      {selectedJob.error && <div className="inline-error">{selectedJob.error}</div>}
      {(selectedJob.status === "failed" || selectedJob.status === "cancelled") && artifacts?.claimsAvailable && <p className="hint">{u.savedClaims} ({artifacts.claimsCount ?? "?"}). {u.savedClaimsSuffix}</p>}
      <div className="actions">
        {jobActive && <button className="button secondary" type="button" onClick={() => void cancelAnalysis()}>{u.stop}</button>}
        {selectedJob.status !== "completed" && selectedJob.availableActions?.resumeVerification && <button className="button primary" type="button" onClick={() => void resumeVerification()}>{u.resume}</button>}
        {selectedJob.status !== "completed" && selectedJob.availableActions?.retryFull && <button className="button secondary" type="button" onClick={() => void retryAnalysis()}>{u.restart}</button>}
      </div>
    </div>}

    <details className="panel technical-details repro-no-print repro-technical-details">
      <summary>{u.historyTech}</summary>
      <div className="technical-body">
        <div className="row between"><b>{u.runs}</b><span className="hint">{jobs.length}</span></div>
        <div className="jobs-list repro-jobs-list">
          {jobs.map((item) => <button key={item.id} className={`job-row ${selectedJob?.id === item.id ? "selected" : ""}`} type="button" onClick={() => setSelectedJobId(item.id)}>
            <strong>{item.originalName || u.check}</strong>
            <span>{reproducibilityStatusLabel(item.status, language)} · {formatDateTime(item.createdAt, language)}</span>
          </button>)}
          {!jobs.length && <div className="empty small">{u.noRuns}</div>}
        </div>

        <div className="actions repro-diagnostics-actions">
          <button className="button secondary" type="button" onClick={() => void checkEnvironment()} disabled={!repo.trim() || jobActive || preflightBusy}>{preflightBusy ? u.checking : u.checkEnvironment}</button>
          {selectedJob && liveLog && <a className="button secondary" href={api.reproducibilityLogDownloadUrl(selectedJob.id)} target="_blank" rel="noreferrer">{u.downloadLog}</a>}
        </div>

        {preflight && <div className="repro-simple-diagnostics">
          <p><b>{preflight.ok ? u.environmentReady : u.environmentProblems}</b>{preflight.model ? ` · ${preflight.model}` : ""}</p>
          <ul>{preflight.checks.map((check) => <li key={check.id}>{check.ok ? "✓" : check.blocking ? "×" : "!"} {check.label}: {check.detail}</li>)}</ul>
        </div>}

        {selectedJob && <div className="meta-grid repro-artifacts-simple">
          <span>{u.sections}: <b>{artifacts?.sectionsAvailable ? artifacts.sectionCount ?? u.readyPlural : "—"}</b></span>
          <span>{u.claims}: <b>{artifacts?.claimsAvailable ? artifacts.claimsCount ?? u.readyPlural : "—"}</b></span>
          <span>{u.verified}: <b>{artifacts?.verificationAvailable ? artifacts.verifiedClaimsCount ?? u.readyOne : "—"}</b></span>
          <span>{u.result}: <b>{selectedJob.resultAvailable ? u.resultReady : "—"}</b></span>
        </div>}

        {selectedJob && <label><span>{u.osaLog}</span><textarea readOnly value={liveLog || u.logPlaceholder} rows={10} /><small>{logTruncated ? u.logTail : ""}</small></label>}
      </div>
    </details>

    {!analysis ? null : <>
      <div className="repro-print-heading">
        <div><h1>{t.title}</h1><p>{t.subtitle}</p></div>
        <div className="repro-print-meta">
          {analysis.repository && <span><b>{t.repo}:</b> {analysis.repository}</span>}
          {analysis.paperPath && <span><b>{t.paper}:</b> {basename(analysis.paperPath)}</span>}
        </div>
      </div>

      <div className="page-title report-title repro-result-title">
        <div>
          <h1>{analysis.title}</h1>
          <p>{analysis.repository || u.resultSubtitle}</p>
        </div>
        <div className="score-box"><strong>{analysis.stats.implementationRatePct}%</strong><span>{u.confirmedPct}</span></div>
      </div>

      <div className="status-grid repro-no-print">
        <button className={`status-card not_checked ${claimFilter === "extracted" ? "active" : ""}`} type="button" onClick={() => selectClaimFilter(setClaimFilter, "extracted")}><strong>{analysis.stats.sourceTotal}</strong><span>{u.allClaims}</span></button>
        <button className={`status-card not_applicable ${claimFilter === "checked" ? "active" : ""}`} type="button" onClick={() => selectClaimFilter(setClaimFilter, "checked")}><strong>{analysis.stats.scoredTotal}</strong><span>{u.checked}</span></button>
        <button className={`status-card pass ${claimFilter === "confirmed" ? "active" : ""}`} type="button" onClick={() => selectClaimFilter(setClaimFilter, "confirmed")}><strong>{analysis.stats.implemented}</strong><span>{u.confirmed}</span></button>
        <button className={`status-card violation ${claimFilter === "rejected" ? "active" : ""}`} type="button" onClick={() => selectClaimFilter(setClaimFilter, "rejected")}><strong>{analysis.stats.notImplemented}</strong><span>{u.rejected}</span></button>
        <button className="status-card not_checked" type="button"><strong>{analysis.stats.excluded + analysis.stats.hiddenLowConfidence}</strong><span>{u.excluded}</span></button>
      </div>

      <div className="panel report-controls repro-no-print repro-result-controls">
        <button className={claimFilter === "checked" ? "text-button active" : "text-button"} type="button" onClick={() => selectClaimFilter(setClaimFilter, "checked")}>{u.checkedClaims} ({analysis.stats.scoredTotal})</button>
        <span>{analysis.stats.implemented} {u.confirmedPct} · {analysis.stats.notImplemented} {u.rejected.toLowerCase()}</span>
      </div>

      <div className="result-list repro-claims-section">
        {filteredClaims.map((claim, index) => <details className="result-card" key={`${claim.id}-${index}`} open={claim.tone === "rejected"}>
          <summary>
            <span className={`status-badge ${claimStatusClass(claim.tone)}`}>{verdictLabel(claim, printLang)}</span>
            <span className="rule-title">{claim.text}</span>
            <span className="rule-id">#{index + 1}</span>
          </summary>
          <div className="result-body">
            {(claim.category || claim.section || claim.verifiability || claim.implementationConfidence) && <div className="meta-grid">
              {claim.category && <span><b>{t.category}:</b> {humanize(claim.category)}</span>}
              {claim.section && <span><b>{t.section}:</b> {claim.section}</span>}
              {claim.verifiability && <span><b>{t.verifiability}:</b> {humanize(claim.verifiability)}</span>}
              {claim.implementationConfidence && <span><b>{t.confidence}:</b> {humanize(claim.implementationConfidence)}</span>}
            </div>}
            {claim.explanation && <div className="explanation"><b>{t.details}</b><p>{claim.explanation}</p></div>}
            {claim.originalText && <div className="evidence repro-pdf-omit"><b>{t.sourceText}</b><blockquote>{claim.originalText}</blockquote></div>}
            {claim.evidence.length > 0 && <div className="evidence"><b>{t.evidence}</b>{claim.evidence.map((item, evidenceIndex) => <blockquote key={evidenceIndex}>{item.path && <span>{item.path}</span>}{item.details || u.evidenceFound}</blockquote>)}</div>}
            <details className="technical-details repro-claim-raw repro-pdf-omit"><summary>{t.raw}</summary><div className="technical-body"><pre>{JSON.stringify(claim.raw, null, 2)}</pre></div></details>
          </div>
        </details>)}
        {!filteredClaims.length && <div className="empty panel">{u.noClaims}</div>}
      </div>

      <div className="actions end report-actions repro-no-print">
        <button className="button primary" type="button" onClick={() => printPdf("ru")}>PDF (RU)</button>
        <button className="button secondary" type="button" onClick={() => printPdf("en")}>PDF (EN)</button>
        <button className="button secondary" type="button" onClick={downloadJson}>JSON</button>
      </div>
    </>}
  </section>;
}

function claimStatusClass(tone: ClaimTone): "pass" | "violation" | "uncertain" {
  if (tone === "confirmed") return "pass";
  if (tone === "rejected") return "violation";
  return "uncertain";
}

function selectClaimFilter(setter: (value: ClaimFilter) => void, filter: ClaimFilter) {
  setter(filter);
  window.setTimeout(() => {
    document.querySelector(".repro-claims-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 0);
}

function normalizeAnalysis(raw: unknown, selectedPaperName: string): NormalizedAnalysis {
  const root = asRecord(raw);
  const meta = asRecord(root.meta);
  const source = asRecord(meta.source);
  const sourcePaper = asRecord(source.paper);
  const paperClaims = asRecord(root.paper_claims);
  const claimVerification = asRecord(root.claim_verification);
  const verificationStats = asRecord(claimVerification.stats);
  const models = asRecord(meta.models);
  const paperClaimsModel = asRecord(models.paper_claims);
  const verificationModel = asRecord(models.paper_verification);

  const claimsSource = Array.isArray(claimVerification.claims)
    ? claimVerification.claims
    : [];
  const claims = claimsSource.map((item, index) => normalizeClaim(item, index));

  const sourceTotal = pickNumber(verificationStats, ["source_total"]) ?? pickNumber(paperClaims, ["claim_count"]) ?? claims.length;
  const eligibleTotal = pickNumber(verificationStats, ["eligible_total", "total"]) ?? claims.length;
  const scoredTotal = pickNumber(verificationStats, ["scored_total"]) ?? claims.length;
  const implemented = pickNumber(verificationStats, ["implemented"]) ?? claims.filter((claim) => claim.tone === "confirmed").length;
  const notImplemented = pickNumber(verificationStats, ["not_implemented"]) ?? claims.filter((claim) => claim.tone === "rejected").length;
  const excluded = pickNumber(verificationStats, ["excluded_low_verifiability"]) ?? Math.max(0, sourceTotal - eligibleTotal);
  const hiddenLowConfidence = pickNumber(verificationStats, ["hidden_low_confidence"]) ?? 0;
  const implementationRatePct = pickNumber(verificationStats, ["implementation_rate_pct"]) ?? (scoredTotal ? Math.round(implemented / scoredTotal * 100) : 0);
  const uncertain = Math.max(0, scoredTotal - implemented - notImplemented);

  const sourcePaperPath = pickString(sourcePaper, ["path"]) || pickString(paperClaims, ["source_path"]);
  const displayPaperName = selectedPaperName || (sourcePaperPath ? basename(sourcePaperPath) : "");
  const repository = pickString(source, ["repository"]);

  return {
    title: displayPaperName || "paper_analysis.json",
    repository,
    paperPath: displayPaperName || sourcePaperPath,
    extractionModel: modelLabel(paperClaimsModel) || modelLabel(asRecord(paperClaims.model)),
    verificationModel: modelLabel(verificationModel),
    claims,
    stats: {
      sourceTotal,
      eligibleTotal,
      scoredTotal,
      implemented,
      notImplemented,
      excluded,
      hiddenLowConfidence,
      implementationRatePct,
      uncertain,
    },
  };
}

function normalizeClaim(value: unknown, index: number): NormalizedClaim {
  const item = asRecord(value);
  const implementation = asRecord(item.implementation);
  const implemented = typeof implementation.implemented === "boolean" ? implementation.implemented : null;
  const contradiction = item.contradiction === true;

  let tone: ClaimTone = "uncertain";
  if (contradiction) tone = "rejected";
  else if (implemented === true) tone = "confirmed";
  else if (implemented === false) tone = "rejected";

  const evidenceFile = pickString(implementation, ["evidence_file"]);
  const explanation = pickString(implementation, ["explanation"]);
  const evidence: NormalizedEvidence[] = evidenceFile || explanation
    ? [{ path: evidenceFile, details: explanation }]
    : [];

  return {
    id: pickString(item, ["claim_id"]) || String(index + 1),
    text: pickString(item, ["claim"]) || `Claim ${index + 1}`,
    originalText: pickString(item, ["original_text"]),
    category: pickString(item, ["category"]),
    section: pickString(item, ["section_name", "section_heading_raw"]),
    verifiability: pickString(item, ["verifiability"]),
    implementationConfidence: pickString(implementation, ["confidence"]),
    contradiction,
    tone,
    explanation,
    evidence,
    raw: value,
  };
}

function modelLabel(record: Record<string, any>): string | undefined {
  const used = Array.isArray(record.used) ? record.used.filter((value) => typeof value === "string") : [];
  if (used.length) return used.join(", ");
  return pickString(record, ["configured"]);
}

function basename(path: string): string {
  const chunks = path.split(/[\\/]/).filter(Boolean);
  return chunks[chunks.length - 1] || path;
}

function humanize(value: string): string {
  const normalized = value.replace(/_/g, " ");
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function verdictLabel(claim: NormalizedClaim, lang: UiLang) {
  const labels = lang === "ru"
    ? { confirmed: "Подтверждено кодом", partial: "Частично подтверждено", rejected: claim.contradiction ? "Противоречит коду" : "Не подтверждено", uncertain: "Неопределённо" }
    : { confirmed: "Confirmed by code", partial: "Partially confirmed", rejected: claim.contradiction ? "Contradicted by code" : "Not confirmed", uncertain: "Inconclusive" };
  return labels[claim.tone];
}

function reproducibilityStatusLabel(status: ReproducibilityJob["status"], language: UiLang): string {
  const u = REPRO_UI[language];
  if (status === "queued") return u.statusQueued;
  if (status === "running") return u.statusRunning;
  if (status === "cancelling") return u.statusCancelling;
  if (status === "completed") return u.statusCompleted;
  if (status === "cancelled") return u.statusCancelled;
  return u.statusFailed;
}

function formatFileSize(bytes: number, language: UiLang): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} ${language === "ru" ? "КБ" : "KB"}`;
  return `${(bytes / 1024 / 1024).toFixed(1)} ${language === "ru" ? "МБ" : "MB"}`;
}

function errorMessage(error: unknown, language: UiLang): string {
  return error instanceof Error && error.message ? error.message : REPRO_UI[language].requestFailed;
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

function formatDateTime(value: string | null | undefined, language: UiLang): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(language === "ru" ? "ru-RU" : "en-US", { dateStyle: "short", timeStyle: "short" });
}

