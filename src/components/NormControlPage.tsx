import { useRef, useState } from "react";
import { api } from "../api";
import { normControlStatusLabel } from "../labels";
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
  const [file, setFile] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  const [checkingServer, setCheckingServer] = useState(false);
  const [serverStatus, setServerStatus] = useState<NormControlServerStatus | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function addFile(input: FileList | File[]) {
    const incoming = Array.from(input);
    const accepted = incoming.find((item) => /\.pdf$/i.test(item.name));
    if (!accepted) {
      onError("Для нормоконтроля поддерживаются только PDF.");
      return;
    }
    if (incoming.length > 1) onError("Для одного запуска нормоконтроля можно выбрать только один PDF.");
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
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setSending(false);
    }
  }

  async function checkServer() {
    if (checkingServer) return;
    setCheckingServer(true);
    try {
      setServerStatus(await api.normControlStatus());
    } catch (error) {
      setServerStatus(null);
      onError((error as Error).message);
    } finally {
      setCheckingServer(false);
    }
  }

  return <section className="page narrow">
    <div className="page-title">
      <div><h1>Нормоконтроль</h1><p>Проверка PDF по ГОСТ 7.32-2017 и ЕСПД через MCP «Автонормоконтроль».</p></div>
    </div>
    {!health.normcontrol.enabled && <div className="inline-error">Интеграция нормоконтроля не настроена.</div>}

    <div className="panel normcontrol-upload">
      <div className="normcontrol-meta">
        <span>DAG</span>
        <code>{health.normcontrol.dagId}</code>
        <span>{health.normcontrol.mcpAttempts || 3} попытки по {health.normcontrol.mcpAttemptTimeoutSeconds || 30} с</span>
        <button className="text-button" disabled={checkingServer || !health.normcontrol.enabled} onClick={() => void checkServer()}>{checkingServer ? "Проверяем MCP…" : "Проверить MCP"}</button>
      </div>
      {serverStatus && <div className="normcontrol-server-status">
        <strong>MCP доступен</strong>
        <span>endpoint: {serverStatus.diagnostics.host}:{serverStatus.diagnostics.port}</span>
        <span>tcp: {serverStatus.diagnostics.tcp || serverStatus.diagnostics.tcpError || "—"}</span>
        <span>http: {serverStatus.diagnostics.httpGetStatus || serverStatus.diagnostics.httpGetError || "—"}</span>
        <span>tools: {serverStatus.tools.map((item) => item.name).filter(Boolean).join(", ") || "—"}</span>
        <span>prompts: {serverStatus.prompts.map((item) => item.name).filter(Boolean).join(", ") || "—"}</span>
      </div>}
      <div className="dropzone compact" onClick={() => inputRef.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); addFile(event.dataTransfer.files); }}>
        <input ref={inputRef} type="file" accept=".pdf" onChange={(event) => event.target.files && addFile(event.target.files)} />
        <strong>{file ? file.name : "Перетащите PDF"}</strong>
        <span>{file ? formatBytes(file.size) : "или нажмите, чтобы выбрать файл"}</span>
      </div>
      <div className="actions between">
        <button className="button secondary" disabled={!file || sending} onClick={() => { setFile(null); if (inputRef.current) inputRef.current.value = ""; }}>Очистить</button>
        <button className="button primary" disabled={!file || sending || !health.normcontrol.enabled} onClick={() => void submit()}>{sending ? "Добавление…" : "Запустить нормоконтроль"}</button>
      </div>
    </div>

    <div className="normcontrol-list">
      <div className="row between normcontrol-list-title"><h2>Запуски</h2><span>{jobs.length}</span></div>
      {jobs.map((job) => <NormControlCard key={job.id} job={job} onChanged={onChanged} onError={onError} />)}
      {!jobs.length && <div className="empty panel">Запусков нормоконтроля пока нет.</div>}
    </div>
  </section>;
}

function NormControlCard({ job, onChanged, onError }: { job: NormControlJob; onChanged: () => Promise<void>; onError: (message: string) => void }) {
  const active = ACTIVE.includes(job.status);
  const [retrying, setRetrying] = useState(false);
  const attemptLogs = job.attemptLogs || [];
  const attempts = job.attempts || 0;
  const maxAttempts = job.maxAttempts || 0;
  const timeoutSeconds = job.timeoutSeconds || 0;
  const attemptInfo = active && attempts > 0 && maxAttempts > 0 && timeoutSeconds > 0
    ? `Попытка ${attempts}/${maxAttempts} · timeout ${timeoutSeconds} с`
    : "";
  const headerDetails = [
    normControlStatusLabel[job.status],
    active ? `${job.progress} %` : "",
    attemptInfo,
  ].filter(Boolean).join(" · ");

  async function retry() {
    if (retrying) return;
    setRetrying(true);
    try {
      await api.retryNormControlJob(job.id);
      await onChanged();
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setRetrying(false);
    }
  }

  async function remove() {
    try {
      await api.deleteNormControlJob(job.id);
      await onChanged();
    } catch (error) {
      onError((error as Error).message);
    }
  }

  return <article className={`panel normcontrol-card ${job.status}`}>
    <div className="row between normcontrol-card-head">
      <div>
        <h3>{job.originalName}</h3>
        <p>{headerDetails}</p>
      </div>
      <span className={`normcontrol-status ${job.status}`}>{normControlStatusLabel[job.status]}</span>
    </div>
    {job.progressMessage && <p className="progress-message">{job.progressMessage}</p>}
    {active && <progress max="100" value={job.progress} />}
    <div className="normcontrol-facts">
      <span>Размер: <b>{formatBytes(job.size)}</b></span>
      <span>Создано: <b>{formatDate(job.createdAt)}</b></span>
      {job.finishedAt && <span>Завершено: <b>{formatDate(job.finishedAt)}</b></span>}
      {job.lastAttemptAt && <span>Последняя попытка: <b>{formatDate(job.lastAttemptAt)}</b></span>}
      {(job.taskId || job.runId) && <span>run_id: <code>{job.runId || job.taskId}</code></span>}
    </div>
    {job.message && job.message !== job.progressMessage && <p className="hint">{job.message}</p>}
    {job.error && <div className="inline-error">{job.error}</div>}
    {job.status === "failed" && !(job.runId || job.taskId) && <p className="retry-warning">Повторная отправка использует сохранённый PDF и может создать новый внешний DAG-запуск, потому что прежняя попытка не вернула run_id.</p>}
    {job.status === "failed" && (job.runId || job.taskId) && <p className="hint">Повтор запросит отчёт по уже полученному run_id без новой отправки PDF.</p>}
    {attemptLogs.length > 0 && <details className="normcontrol-attempts" open={job.status === "failed"}>
      <summary>Журнал MCP ({attemptLogs.length})</summary>
      <div>{attemptLogs.map((item, index) => <AttemptLogRow key={`${item.at}-${index}`} item={item} />)}</div>
    </details>}
    {job.errors.length > 0 && <details className="normcontrol-findings" open={job.status === "completed"}>
      <summary>Ошибки ({job.errors.length})</summary>
      <ul>{job.errors.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
    </details>}
    {job.warnings.length > 0 && <details className="normcontrol-findings">
      <summary>Предупреждения ({job.warnings.length})</summary>
      <ul>{job.warnings.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
    </details>}
    <div className="actions end">
      {job.status === "completed" && <a className="button primary" href={api.normControlReportPdfUrl(job.id)}>Скачать PDF-отчёт</a>}
      {job.status === "failed" && <button className="button secondary" disabled={retrying} onClick={() => void retry()}>{retrying ? "Повторяем…" : "Повторить"}</button>}
      {!active && <button className="button danger" onClick={() => void remove()}>Удалить</button>}
    </div>
  </article>;
}

function AttemptLogRow({ item }: { item: NormControlAttemptLog }) {
  return <div className={`attempt-row ${item.status}`}>
    <b>{item.tool} · попытка {item.attempt}/{item.maxAttempts} · {attemptStatusLabel(item)} · {item.elapsedMs} мс</b>
    <span>{formatDate(item.at)} · timeout {item.timeoutSeconds} с · progress callback: {item.progressSeen ? "да" : "нет"}</span>
    <small>endpoint: {item.endpoint}; DAG: {item.dagId}</small>
    {item.mcpStatus !== undefined && <small>remote status: {item.mcpStatus || "—"}; run_id: {item.runIdReceived ? "да" : "нет"}; report_pdf: {item.reportPdfReceived ? "да" : "нет"}; output_pdf: {item.outputPdfReceived ? "да" : "нет"}; errors: {item.mcpErrorsCount ?? 0}; warnings: {item.mcpWarningsCount ?? 0}</small>}
    {item.mcpMessage && <span>{item.mcpMessage}</span>}
    {item.error && <pre>{item.error}</pre>}
    {item.mcpErrors?.length ? <ul>{item.mcpErrors.map((error, index) => <li key={`${index}-${error}`}>{error}</li>)}</ul> : null}
    {item.mcpWarnings?.length ? <ul>{item.mcpWarnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul> : null}
    {item.diagnostics && <small>diagnostics: {formatDiagnostics(item.diagnostics)}</small>}
  </div>;
}

function attemptStatusLabel(item: NormControlAttemptLog) {
  if (item.status === "remote_failed") return "remote failed";
  if (item.status === "succeeded" && item.mcpStatus && item.mcpStatus !== "done") return `MCP ответ: ${item.mcpStatus}`;
  if (item.status === "succeeded") return "успешно";
  if (item.status === "timed_out") return "timeout";
  return "ошибка";
}

function formatDiagnostics(diagnostics: Record<string, string>) {
  return Object.entries(diagnostics).filter(([, value]) => value).map(([key, value]) => `${key}=${value}`).join("; ") || "—";
}

function formatBytes(bytes: number) {
  return new Intl.NumberFormat("ru", { maximumFractionDigits: 1, style: "unit", unit: "megabyte" }).format(bytes / 1024 / 1024);
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}
