import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api";
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

export function ReproducibilityPage() {
  const [repo, setRepo] = useState("");
  const [paperName, setPaperName] = useState("");
  const [paperFile, setPaperFile] = useState<File | null>(null);
  const [jsonName, setJsonName] = useState("");
  const [raw, setRaw] = useState<unknown>(null);
  const [error, setError] = useState("");
  const [printLang, setPrintLang] = useState<UiLang>("ru");
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
        if (!disposed) setError(errorMessage(resultError));
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
      setError("Не удалось загрузить результат анализа.");
    }
  }

  async function checkEnvironment(): Promise<ReproducibilityPreflight | null> {
    if (!repo.trim()) {
      setError("Укажите ссылку на репозиторий.");
      return null;
    }
    setPreflightBusy(true);
    setError("");
    try {
      const result = await api.reproducibilityPreflight(repo.trim());
      setPreflight(result);
      if (!result.ok) {
        const failed = result.checks.filter((item) => item.blocking && !item.ok);
        setError(failed.length ? `Не пройдена предварительная проверка: ${failed.map((item) => item.label).join(", ")}.` : "Окружение не готово к запуску OSA.");
      }
      return result;
    } catch (preflightError) {
      setError(errorMessage(preflightError));
      return null;
    } finally {
      setPreflightBusy(false);
    }
  }

  async function runAnalysis() {
    if (!repo.trim()) {
      setError("Укажите ссылку на репозиторий.");
      return;
    }
    if (!paperFile) {
      setError("Выберите PDF работы.");
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
      setError(errorMessage(runError));
    }
  }

  async function cancelAnalysis() {
    if (!selectedJob || !jobActive) return;
    try {
      upsertJob(await api.cancelReproducibilityJob(selectedJob.id));
    } catch (cancelError) {
      setError(errorMessage(cancelError));
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
      setError(errorMessage(retryError));
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
      setError(errorMessage(resumeError));
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
        setPrintLang("ru");
      }, 250);
    }, 80);
  }

  const artifacts = selectedJob?.artifacts;

  return <section className="page narrow reproducibility-page">
    <div className="page-title repro-no-print">
      <div>
        <h1>Проверка воспроизводимости</h1>
        <p>Сопоставляет технические утверждения из ВКР с кодом указанного репозитория.</p>
      </div>
    </div>

    {serviceStatus && (!serviceStatus.osaInstalled || !serviceStatus.llmConfigured || serviceStatus.pythonSupportedForPaperClaims === false || serviceStatus.gitInstalled === false) && <div className="inline-error repro-no-print">
      Backend не готов к запуску OSA. {!serviceStatus.osaInstalled ? "Не установлен osa_tool. " : ""}{!serviceStatus.llmConfigured ? "Не настроен API-ключ LLM. " : ""}{serviceStatus.pythonSupportedForPaperClaims === false ? `Python ${serviceStatus.pythonVersion || ""} не поддерживается paper-claims. ` : ""}{serviceStatus.gitInstalled === false ? "git не найден в PATH." : ""}
    </div>}

    <div className="panel repro-no-print">
      <label>
        <span>Репозиторий</span>
        <input value={repo} onChange={(event) => setRepo(event.target.value)} placeholder="https://github.com/owner/project или https://sourcecraft.dev/owner/project" />
        <small>GitHub, GitLab, GitVerse или SourceCraft.</small>
      </label>
    </div>

    <div className="dropzone repro-no-print" onClick={() => !jobActive && pdfInput.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => {
      event.preventDefault();
      if (jobActive) return;
      const file = Array.from(event.dataTransfer.files).find((item) => /\.pdf$/i.test(item.name)) || null;
      if (!file) { setError("Поддерживается только PDF."); return; }
      setPaperFile(file); setPaperName(file.name); setError("");
    }}>
      <input ref={pdfInput} type="file" accept=".pdf,application/pdf" onChange={(event) => {
        const file = event.target.files?.[0] || null;
        setPaperFile(file);
        setPaperName(file?.name || "");
      }} />
      <strong>{paperFile ? paperFile.name : "Перетащите PDF работы"}</strong>
      <span>{paperFile ? formatFileSize(paperFile.size) : "или нажмите, чтобы выбрать файл"}</span>
    </div>

    {paperFile && <div className="panel file-list repro-no-print">
      <div className="row between"><strong>Файл</strong><button className="text-button" type="button" onClick={() => { setPaperFile(null); setPaperName(""); }}>Очистить</button></div>
      <div className="file-item"><span>{paperFile.name}</span><small>{formatFileSize(paperFile.size)}</small><button aria-label="Удалить" type="button" onClick={() => { setPaperFile(null); setPaperName(""); }}>×</button></div>
    </div>}

    {error && <div className="inline-error repro-no-print">{error}</div>}

    <div className="actions end repro-no-print repro-main-actions">
      <button className="button secondary" type="button" onClick={() => jsonInput.current?.click()} disabled={jobActive}>Открыть готовый JSON</button>
      <input ref={jsonInput} hidden type="file" accept=".json,application/json" onChange={(event) => void loadJson(event.target.files?.[0])} />
      <button className="button primary" type="button" onClick={() => void runAnalysis()} disabled={!repo.trim() || !paperFile || jobActive || preflightBusy}>
        {jobActive ? "Проверка выполняется…" : "Проверить воспроизводимость"}
      </button>
    </div>

    {selectedJob && <div className="panel job-state repro-no-print repro-job-state">
      <h1>{selectedJob.originalName || "Проверка воспроизводимости"}</h1>
      <p><b>{reproducibilityStatusLabel(selectedJob.status)}</b>{jobActive ? ` · ${Math.round(selectedJob.progress || 0)} %` : ""}</p>
      {selectedJob.progressMessage && <p className="progress-message">{selectedJob.progressMessage}</p>}
      {jobActive && <progress max="100" value={selectedJob.progress || 0} />}
      {selectedJob.error && <div className="inline-error">{selectedJob.error}</div>}
      {(selectedJob.status === "failed" || selectedJob.status === "cancelled") && artifacts?.claimsAvailable && <p className="hint">Claims уже сохранены ({artifacts.claimsCount ?? "?"}). Проверку по репозиторию можно продолжить без повторной обработки PDF.</p>}
      <div className="actions">
        {jobActive && <button className="button secondary" type="button" onClick={() => void cancelAnalysis()}>Остановить</button>}
        {selectedJob.status !== "completed" && selectedJob.availableActions?.resumeVerification && <button className="button primary" type="button" onClick={() => void resumeVerification()}>Продолжить проверку</button>}
        {selectedJob.status !== "completed" && selectedJob.availableActions?.retryFull && <button className="button secondary" type="button" onClick={() => void retryAnalysis()}>Начать заново</button>}
      </div>
    </div>}

    <details className="panel technical-details repro-no-print repro-technical-details">
      <summary>История и технические детали</summary>
      <div className="technical-body">
        <div className="row between"><b>Запуски</b><span className="hint">{jobs.length}</span></div>
        <div className="jobs-list repro-jobs-list">
          {jobs.map((item) => <button key={item.id} className={`job-row ${selectedJob?.id === item.id ? "selected" : ""}`} type="button" onClick={() => setSelectedJobId(item.id)}>
            <strong>{item.originalName || "Проверка"}</strong>
            <span>{reproducibilityStatusLabel(item.status)} · {formatDateTime(item.createdAt)}</span>
          </button>)}
          {!jobs.length && <div className="empty small">Запусков пока нет.</div>}
        </div>

        <div className="actions repro-diagnostics-actions">
          <button className="button secondary" type="button" onClick={() => void checkEnvironment()} disabled={!repo.trim() || jobActive || preflightBusy}>{preflightBusy ? "Проверяем…" : "Проверить окружение"}</button>
          {selectedJob && liveLog && <a className="button secondary" href={api.reproducibilityLogDownloadUrl(selectedJob.id)} target="_blank" rel="noreferrer">Скачать лог</a>}
        </div>

        {preflight && <div className="repro-simple-diagnostics">
          <p><b>{preflight.ok ? "Окружение готово" : "Есть проблемы с окружением"}</b>{preflight.model ? ` · ${preflight.model}` : ""}</p>
          <ul>{preflight.checks.map((check) => <li key={check.id}>{check.ok ? "✓" : check.blocking ? "×" : "!"} {check.label}: {check.detail}</li>)}</ul>
        </div>}

        {selectedJob && <div className="meta-grid repro-artifacts-simple">
          <span>Разделы: <b>{artifacts?.sectionsAvailable ? artifacts.sectionCount ?? "готовы" : "—"}</b></span>
          <span>Claims: <b>{artifacts?.claimsAvailable ? artifacts.claimsCount ?? "готовы" : "—"}</b></span>
          <span>Проверено: <b>{artifacts?.verificationAvailable ? artifacts.verifiedClaimsCount ?? "готово" : "—"}</b></span>
          <span>Результат: <b>{selectedJob.resultAvailable ? "готов" : "—"}</b></span>
        </div>}

        {selectedJob && <label><span>Лог OSA</span><textarea readOnly value={liveLog || "Лог появится после запуска OSA."} rows={10} /><small>{logTruncated ? "Показана последняя часть лога." : ""}</small></label>}
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
          <p>{analysis.repository || "Результат проверки воспроизводимости"}</p>
        </div>
        <div className="score-box"><strong>{analysis.stats.implementationRatePct}%</strong><span>подтверждено</span></div>
      </div>

      <div className="status-grid repro-no-print">
        <button className={`status-card not_checked ${claimFilter === "extracted" ? "active" : ""}`} type="button" onClick={() => selectClaimFilter(setClaimFilter, "extracted")}><strong>{analysis.stats.sourceTotal}</strong><span>Все claims</span></button>
        <button className={`status-card not_applicable ${claimFilter === "checked" ? "active" : ""}`} type="button" onClick={() => selectClaimFilter(setClaimFilter, "checked")}><strong>{analysis.stats.scoredTotal}</strong><span>Проверено</span></button>
        <button className={`status-card pass ${claimFilter === "confirmed" ? "active" : ""}`} type="button" onClick={() => selectClaimFilter(setClaimFilter, "confirmed")}><strong>{analysis.stats.implemented}</strong><span>Подтверждено</span></button>
        <button className={`status-card violation ${claimFilter === "rejected" ? "active" : ""}`} type="button" onClick={() => selectClaimFilter(setClaimFilter, "rejected")}><strong>{analysis.stats.notImplemented}</strong><span>Не подтверждено</span></button>
        <button className="status-card not_checked" type="button"><strong>{analysis.stats.excluded + analysis.stats.hiddenLowConfidence}</strong><span>Исключено</span></button>
      </div>

      <div className="panel report-controls repro-no-print repro-result-controls">
        <button className={claimFilter === "checked" ? "text-button active" : "text-button"} type="button" onClick={() => selectClaimFilter(setClaimFilter, "checked")}>Проверенные утверждения ({analysis.stats.scoredTotal})</button>
        <span>{analysis.stats.implemented} подтверждено · {analysis.stats.notImplemented} не подтверждено</span>
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
            {claim.evidence.length > 0 && <div className="evidence"><b>{t.evidence}</b>{claim.evidence.map((item, evidenceIndex) => <blockquote key={evidenceIndex}>{item.path && <span>{item.path}</span>}{item.details || "Найдено подтверждение в репозитории."}</blockquote>)}</div>}
            <details className="technical-details repro-claim-raw repro-pdf-omit"><summary>{t.raw}</summary><div className="technical-body"><pre>{JSON.stringify(claim.raw, null, 2)}</pre></div></details>
          </div>
        </details>)}
        {!filteredClaims.length && <div className="empty panel">В этой категории утверждений нет.</div>}
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

function reproducibilityStatusLabel(status: ReproducibilityJob["status"]): string {
  if (status === "queued") return "В очереди OSA";
  if (status === "running") return "Проверка выполняется";
  if (status === "cancelling") return "Останавливаем проверку";
  if (status === "completed") return "Проверка завершена";
  if (status === "cancelled") return "Проверка остановлена";
  return "Проверка завершилась с ошибкой";
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "Не удалось выполнить запрос к backend.";
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

function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

