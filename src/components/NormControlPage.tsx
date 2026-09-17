import { useRef, useState } from "react";
import { api } from "../api";
import { useLanguage, type UiLanguage } from "../i18n";
import { uiLabels } from "../labels";
import type { Health, NormControlAttemptLog, NormControlJob, NormControlServerStatus, NormControlStatus } from "../types";

interface Props {
  health: Health;
  jobs: NormControlJob[];
  onCreated: (job: NormControlJob) => void;
  onChanged: () => Promise<void>;
  onError: (message: string) => void;
}

const ACTIVE: NormControlStatus[] = ["queued", "queued_report", "submitting", "running", "reporting", "downloading"];

export function NormControlPage({ health, jobs, onCreated, onChanged, onError }: Props) {
  const { language } = useLanguage();
  const t = language === "ru" ? {
    onlyPdf: "Для нормоконтроля поддерживаются только PDF.", single: "Для одного запуска нормоконтроля можно выбрать только один PDF.", title: "Нормоконтроль", subtitle: "Проверка PDF по ГОСТ 7.32-2017 и ЕСПД через MCP «Автонормоконтроль».", disabled: "Интеграция нормоконтроля не настроена.", attempts: "попытки по", seconds: "с", checkingMcp: "Проверяем MCP…", checkMcp: "Проверить MCP", mcpReady: "MCP доступен", drop: "Перетащите PDF", choose: "или нажмите, чтобы выбрать файл", clear: "Очистить", adding: "Добавление…", run: "Запустить нормоконтроль", runs: "Запуски", empty: "Запусков нормоконтроля пока нет.",
  } : {
    onlyPdf: "Only PDF files are supported for formal review.", single: "Only one PDF can be selected per formal-review run.", title: "Formal review", subtitle: "Check a PDF against GOST 7.32-2017 and ESKD requirements through the Auto Norm Control MCP.", disabled: "Formal-review integration is not configured.", attempts: "attempts with", seconds: "s", checkingMcp: "Checking MCP…", checkMcp: "Check MCP", mcpReady: "MCP available", drop: "Drop a PDF here", choose: "or click to choose a file", clear: "Clear", adding: "Adding…", run: "Run formal review", runs: "Runs", empty: "No formal-review runs yet.",
  };
  const [file, setFile] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  const [checkingServer, setCheckingServer] = useState(false);
  const [serverStatus, setServerStatus] = useState<NormControlServerStatus | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function addFile(input: FileList | File[]) {
    const incoming = Array.from(input);
    const accepted = incoming.find((item) => /\.pdf$/i.test(item.name));
    if (!accepted) { onError(t.onlyPdf); return; }
    if (incoming.length > 1) onError(t.single);
    setFile(accepted);
  }

  async function submit() {
    if (!file || sending) return;
    setSending(true);
    try {
      const body = new FormData();
      body.append("file", file);
      const created = await api.createNormControlJob(body);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      onCreated(created);
    } catch (error) { onError((error as Error).message); }
    finally { setSending(false); }
  }

  async function checkServer() {
    if (checkingServer) return;
    setCheckingServer(true);
    try { setServerStatus(await api.normControlStatus()); }
    catch (error) { setServerStatus(null); onError((error as Error).message); }
    finally { setCheckingServer(false); }
  }

  return <section className="page narrow">
    <div className="page-title"><div><h1>{t.title}</h1><p>{t.subtitle}</p></div></div>
    {!health.normcontrol.enabled && <div className="inline-error">{t.disabled}</div>}

    <div className="panel normcontrol-upload">
      <div className="normcontrol-meta">
        <span>DAG</span><code>{health.normcontrol.dagId}</code>
        <span>{health.normcontrol.mcpAttempts || 3} {t.attempts} {health.normcontrol.mcpAttemptTimeoutSeconds || 30} {t.seconds}</span>
        <button className="text-button" disabled={checkingServer || !health.normcontrol.enabled} onClick={() => void checkServer()}>{checkingServer ? t.checkingMcp : t.checkMcp}</button>
      </div>
      {serverStatus && <div className="normcontrol-server-status">
        <strong>{t.mcpReady}</strong>
        <span>endpoint: {serverStatus.diagnostics.host}:{serverStatus.diagnostics.port}</span>
        <span>tcp: {serverStatus.diagnostics.tcp || serverStatus.diagnostics.tcpError || "—"}</span>
        <span>http: {serverStatus.diagnostics.httpGetStatus || serverStatus.diagnostics.httpGetError || "—"}</span>
        <span>tools: {serverStatus.tools.map((item) => item.name).filter(Boolean).join(", ") || "—"}</span>
        <span>prompts: {serverStatus.prompts.map((item) => item.name).filter(Boolean).join(", ") || "—"}</span>
      </div>}
      <div className="dropzone compact" onClick={() => inputRef.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); addFile(event.dataTransfer.files); }}>
        <input ref={inputRef} type="file" accept=".pdf" onChange={(event) => event.target.files && addFile(event.target.files)} />
        <strong>{file ? file.name : t.drop}</strong><span>{file ? formatBytes(file.size, language) : t.choose}</span>
      </div>
      <div className="actions between">
        <button className="button secondary" disabled={!file || sending} onClick={() => { setFile(null); if (inputRef.current) inputRef.current.value = ""; }}>{t.clear}</button>
        <button className="button primary" disabled={!file || sending || !health.normcontrol.enabled} onClick={() => void submit()}>{sending ? t.adding : t.run}</button>
      </div>
    </div>

    <div className="normcontrol-list">
      <div className="row between normcontrol-list-title"><h2>{t.runs}</h2><span>{jobs.length}</span></div>
      {jobs.map((job) => <NormControlCard key={job.id} job={job} onChanged={onChanged} onError={onError} language={language} />)}
      {!jobs.length && <div className="empty panel">{t.empty}</div>}
    </div>
  </section>;
}

function NormControlCard({ job, onChanged, onError, language }: { job: NormControlJob; onChanged: () => Promise<void>; onError: (message: string) => void; language: UiLanguage }) {
  const labels = uiLabels(language);
  const t = language === "ru" ? {
    attempt: "Попытка", timeout: "timeout", size: "Размер", created: "Создано", finished: "Завершено", lastAttempt: "Последняя попытка", resubmitWarning: "Повторная отправка использует сохранённый PDF и может создать новый внешний DAG-запуск, потому что прежняя попытка не вернула run_id.", retryExisting: "Повтор запросит отчёт по уже полученному run_id без новой отправки PDF.", mcpLog: "Журнал MCP", errors: "Ошибки", warnings: "Предупреждения", download: "Скачать PDF-отчёт", retrying: "Повторяем…", retry: "Повторить", delete: "Удалить",
  } : {
    attempt: "Attempt", timeout: "timeout", size: "Size", created: "Created", finished: "Finished", lastAttempt: "Last attempt", resubmitWarning: "Retrying uses the saved PDF and may create a new external DAG run because the previous attempt did not return run_id.", retryExisting: "Retrying will request the report for the existing run_id without uploading the PDF again.", mcpLog: "MCP log", errors: "Errors", warnings: "Warnings", download: "Download PDF report", retrying: "Retrying…", retry: "Retry", delete: "Delete",
  };
  const active = ACTIVE.includes(job.status);
  const [retrying, setRetrying] = useState(false);
  const attemptLogs = job.attemptLogs || [];
  const attempts = job.attempts || 0;
  const maxAttempts = job.maxAttempts || 0;
  const timeoutSeconds = job.timeoutSeconds || 0;
  const attemptInfo = active && attempts > 0 && maxAttempts > 0 && timeoutSeconds > 0 ? `${t.attempt} ${attempts}/${maxAttempts} · ${t.timeout} ${timeoutSeconds} s` : "";
  const headerDetails = [labels.normControlStatus[job.status], active ? `${job.progress} %` : "", attemptInfo].filter(Boolean).join(" · ");

  async function retry() {
    if (retrying) return;
    setRetrying(true);
    try { await api.retryNormControlJob(job.id); await onChanged(); }
    catch (error) { onError((error as Error).message); }
    finally { setRetrying(false); }
  }

  async function remove() {
    try { await api.deleteNormControlJob(job.id); await onChanged(); }
    catch (error) { onError((error as Error).message); }
  }

  return <article className={`panel normcontrol-card ${job.status}`}>
    <div className="row between normcontrol-card-head"><div><h3>{job.originalName}</h3><p>{headerDetails}</p></div><span className={`normcontrol-status ${job.status}`}>{labels.normControlStatus[job.status]}</span></div>
    {job.progressMessage && <p className="progress-message">{job.progressMessage}</p>}
    {active && <progress max="100" value={job.progress} />}
    <div className="normcontrol-facts">
      <span>{t.size}: <b>{formatBytes(job.size, language)}</b></span><span>{t.created}: <b>{formatDate(job.createdAt, language)}</b></span>
      {job.finishedAt && <span>{t.finished}: <b>{formatDate(job.finishedAt, language)}</b></span>}
      {job.lastAttemptAt && <span>{t.lastAttempt}: <b>{formatDate(job.lastAttemptAt, language)}</b></span>}
      {(job.taskId || job.runId) && <span>run_id: <code>{job.runId || job.taskId}</code></span>}
    </div>
    {job.message && job.message !== job.progressMessage && <p className="hint">{job.message}</p>}
    {job.error && <div className="inline-error">{job.error}</div>}
    {job.status === "failed" && !(job.runId || job.taskId) && <p className="retry-warning">{t.resubmitWarning}</p>}
    {job.status === "failed" && (job.runId || job.taskId) && <p className="hint">{t.retryExisting}</p>}
    {attemptLogs.length > 0 && <details className="normcontrol-attempts" open={job.status === "failed"}><summary>{t.mcpLog} ({attemptLogs.length})</summary><div>{attemptLogs.map((item, index) => <AttemptLogRow key={`${item.at}-${index}`} item={item} language={language} />)}</div></details>}
    {job.errors.length > 0 && <details className="normcontrol-findings" open={job.status === "completed"}><summary>{t.errors} ({job.errors.length})</summary><ul>{job.errors.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></details>}
    {job.warnings.length > 0 && <details className="normcontrol-findings"><summary>{t.warnings} ({job.warnings.length})</summary><ul>{job.warnings.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></details>}
    <div className="actions end">
      {job.status === "completed" && <a className="button primary" href={api.normControlReportPdfUrl(job.id)}>{t.download}</a>}
      {job.status === "failed" && <button className="button secondary" disabled={retrying} onClick={() => void retry()}>{retrying ? t.retrying : t.retry}</button>}
      {!active && <button className="button danger" onClick={() => void remove()}>{t.delete}</button>}
    </div>
  </article>;
}

function AttemptLogRow({ item, language }: { item: NormControlAttemptLog; language: UiLanguage }) {
  const t = language === "ru" ? { attempt: "попытка", progress: "progress callback", yes: "да", no: "нет" } : { attempt: "attempt", progress: "progress callback", yes: "yes", no: "no" };
  return <div className={`attempt-row ${item.status}`}>
    <b>{item.tool} · {t.attempt} {item.attempt}/{item.maxAttempts} · {attemptStatusLabel(item, language)} · {item.elapsedMs} ms</b>
    <span>{formatDate(item.at, language)} · timeout {item.timeoutSeconds} s · {t.progress}: {item.progressSeen ? t.yes : t.no}</span>
    <small>endpoint: {item.endpoint}; DAG: {item.dagId}</small>
    {item.mcpStatus !== undefined && <small>remote status: {item.mcpStatus || "—"}; run_id: {item.runIdReceived ? t.yes : t.no}; report_pdf: {item.reportPdfReceived ? t.yes : t.no}; output_pdf: {item.outputPdfReceived ? t.yes : t.no}; errors: {item.mcpErrorsCount ?? 0}; warnings: {item.mcpWarningsCount ?? 0}</small>}
    {item.mcpMessage && <span>{item.mcpMessage}</span>}{item.error && <pre>{item.error}</pre>}
    {item.mcpErrors?.length ? <ul>{item.mcpErrors.map((error, index) => <li key={`${index}-${error}`}>{error}</li>)}</ul> : null}
    {item.mcpWarnings?.length ? <ul>{item.mcpWarnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul> : null}
    {item.diagnostics && <small>diagnostics: {formatDiagnostics(item.diagnostics)}</small>}
  </div>;
}

function attemptStatusLabel(item: NormControlAttemptLog, language: UiLanguage) {
  if (item.status === "remote_failed") return "remote failed";
  if (item.status === "succeeded" && item.mcpStatus && item.mcpStatus !== "done") return `${language === "ru" ? "MCP ответ" : "MCP response"}: ${item.mcpStatus}`;
  if (item.status === "succeeded") return language === "ru" ? "успешно" : "success";
  if (item.status === "timed_out") return "timeout";
  return language === "ru" ? "ошибка" : "error";
}

function formatDiagnostics(diagnostics: Record<string, string>) { return Object.entries(diagnostics).filter(([, value]) => value).map(([key, value]) => `${key}=${value}`).join("; ") || "—"; }
function formatBytes(bytes: number, language: UiLanguage) { return new Intl.NumberFormat(language === "ru" ? "ru" : "en", { maximumFractionDigits: 1, style: "unit", unit: "megabyte" }).format(bytes / 1024 / 1024); }
function formatDate(value: string, language: UiLanguage) { const date = new Date(value); if (Number.isNaN(date.getTime())) return value; return new Intl.DateTimeFormat(language === "ru" ? "ru" : "en-GB", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date); }
