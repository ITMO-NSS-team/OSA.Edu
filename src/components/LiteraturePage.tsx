import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { LiteratureJob, ModelInfo } from "../types";
import { ATTENTION_STATUSES, LiteratureReport, literatureResultCounts } from "./LiteratureReport";

type Filter = "attention" | "all" | "confirmed";

interface Props {
  models: ModelInfo[];
}

const JOB_COPY: Record<LiteratureJob["status"], string> = {
  queued: "В очереди",
  running: "Проверяется",
  cancelling: "Останавливается",
  done: "Готово",
  failed: "Ошибка",
  cancelled: "Остановлено",
};

export function LiteraturePage({ models }: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const productionModels = models.filter((item) => item.tier === "production");
  const defaultModel = productionModels.find((item) => item.recommended)?.id
    ?? productionModels.find((item) => item.id === "z-ai/glm-5.3-flash")?.id
    ?? productionModels[0]?.id
    ?? models[0]?.id
    ?? "";

  const [model, setModel] = useState(() => localStorage.getItem("osaLiteratureModel") || defaultModel);
  const [files, setFiles] = useState<File[]>([]);
  const [jobs, setJobs] = useState<LiteratureJob[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("attention");

  useEffect(() => {
    if (!productionModels.some((item) => item.id === model) && defaultModel) setModel(defaultModel);
  }, [defaultModel, model, models]);

  useEffect(() => {
    void loadJobs();
    const timer = window.setInterval(() => void loadJobs(), 1400);
    return () => window.clearInterval(timer);
  }, []);

  async function loadJobs() {
    try {
      const next = await api.literatureJobs();
      setJobs(next);
      setSelectedId((current) => current && next.some((item) => item.id === current) ? current : next[0]?.id ?? null);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  const selectedJob = useMemo(
    () => jobs.find((item) => item.id === selectedId) ?? jobs[0] ?? null,
    [jobs, selectedId],
  );
  const result = selectedJob?.result ?? null;
  const runningCount = jobs.filter((item) => item.status === "running" || item.status === "cancelling").length;
  const queuedCount = jobs.filter((item) => item.status === "queued").length;

  const resultCounts = result ? literatureResultCounts(result) : { attention: 0, confirmed: 0 };
  const attentionCount = resultCounts.attention;
  const confirmedCount = resultCounts.confirmed;
  const visibleRows = useMemo(() => {
    if (!result) return [];
    if (filter === "attention") return result.rows.filter((row) => ATTENTION_STATUSES.has(row.status));
    if (filter === "confirmed") return result.rows.filter((row) => row.status === "OK");
    return result.rows;
  }, [filter, result]);

  function addFiles(input: FileList | File[]) {
    const incoming = Array.from(input);
    const pdfs = incoming.filter((file) => /\.pdf$/i.test(file.name));
    if (pdfs.length !== incoming.length) setError("Для проверки литературы поддерживаются только PDF.");
    else setError("");
    setFiles((current) => {
      const next = [...current];
      for (const file of pdfs) {
        if (!next.some((item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified)) next.push(file);
      }
      return next.slice(0, 30);
    });
  }

  async function enqueue() {
    if (!files.length || sending || !model) return;
    setSending(true);
    setError("");
    try {
      const body = new FormData();
      files.forEach((file) => body.append("files", file));
      body.append("model", model);
      const created = await api.createLiteratureJobs(body);
      setFiles([]);
      if (created[0]) setSelectedId(created[0].id);
      await loadJobs();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSending(false);
    }
  }

  async function stop(job: LiteratureJob) {
    try {
      await api.cancelLiteratureJob(job.id);
      await loadJobs();
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function retry(job: LiteratureJob) {
    try {
      await api.retryLiteratureJob(job.id);
      await loadJobs();
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function remove(job: LiteratureJob) {
    try {
      await api.deleteLiteratureJob(job.id);
      if (selectedId === job.id) setSelectedId(null);
      await loadJobs();
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  function chooseJob(job: LiteratureJob) {
    setSelectedId(job.id);
    const nextResult = job.result;
    if (nextResult) setFilter(nextResult.rows.some((row) => ATTENTION_STATUSES.has(row.status)) ? "attention" : "all");
  }

  function downloadTsv() {
    if (!result) return;
    const header = ["number", "source_type", "verdict", "status", "original_citation", "checker_found_citation", "evidence_url", "notes"];
    const clean = (value: unknown) => String(value ?? "").replace(/[\t\r\n]+/g, " ").trim();
    const lines = [
      header.join("\t"),
      ...result.rows.map((row) => [row.number, row.source_type, row.verdict, row.status, row.original_citation, row.checker_found_citation, row.evidence_url, row.notes].map(clean).join("\t")),
    ];
    const blob = new Blob(["\ufeff", lines.join("\n")], { type: "text/tab-separated-values;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${result.filename.replace(/\.pdf$/i, "")}-reference-review.tsv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function downloadHtml() {
    if (!result) return;
    try {
      const { downloadLiteratureReportHtml } = await import("../literatureReportExport");
      downloadLiteratureReportHtml(result);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function printPdf() {
    if (!result) return;
    const popup = window.open("", "_blank");
    if (!popup) {
      setError("Браузер заблокировал окно печати. Разрешите всплывающие окна для OSA.Edu.");
      return;
    }
    try {
      const { printLiteratureReport } = await import("../literatureReportExport");
      printLiteratureReport(result, popup);
    } catch (reason) {
      popup.close();
      setError((reason as Error).message);
    }
  }

  const selectedModel = models.find((item) => item.id === model);
  return (
    <section className="page literature-page polished-literature-page">
      <div className="literature-hero">
        <div>
          <span className="literature-eyebrow">Проверка источников</span>
          <h1>Проверка литературы</h1>
          <p>Загрузите PDF. Работы проверяются по очереди, а сомнительные источники дополнительно перепроверяются в сети.</p>
        </div>
      </div>

      <div className="panel literature-start-card literature-queue-start-card">
        <label className="literature-model-field">
          <span>Модель</span>
          <select
            value={model}
            onChange={(event) => {
              setModel(event.target.value);
              localStorage.setItem("osaLiteratureModel", event.target.value);
            }}
          >
            {productionModels.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
          <small>{selectedModel?.note || "Модель используется для сопоставления источников и углублённой веб-проверки."}</small>
        </label>

        <button type="button" className={`literature-file-picker ${files.length ? "has-file" : ""}`} onClick={() => inputRef.current?.click()}>
          <input
            ref={inputRef}
            type="file"
            accept="application/pdf,.pdf"
            multiple
            onChange={(event) => {
              if (event.target.files) addFiles(event.target.files);
              event.target.value = "";
            }}
          />
          <span className="literature-file-icon" aria-hidden="true">PDF</span>
          <span className="literature-file-copy">
            <strong>{files.length ? `Выбрано: ${files.length}` : "Выберите PDF"}</strong>
            <small>{files.length ? "Файлы будут добавлены в общую очередь" : "Можно выбрать несколько работ сразу"}</small>
          </span>
        </button>

        <div className="literature-start-actions">
          <button className="button primary literature-start-button" disabled={!files.length || sending || !model} onClick={() => void enqueue()}>
            {sending ? "Добавляем…" : files.length > 1 ? `Добавить в очередь · ${files.length}` : "Добавить в очередь"}
          </button>
          {files.length > 0 && <button className="button secondary" disabled={sending} onClick={() => setFiles([])}>Очистить</button>}
        </div>

        {(runningCount > 0 || queuedCount > 0) && (
          <div className="literature-running" role="status">
            <span className="literature-spinner" aria-hidden="true" />
            <div>
              <strong>{runningCount ? "Проверка выполняется" : "Работы ожидают запуска"}</strong>
              <small>{runningCount ? `Сейчас: ${runningCount}` : ""}{runningCount && queuedCount ? " · " : ""}{queuedCount ? `в очереди: ${queuedCount}` : ""}</small>
            </div>
          </div>
        )}
        {error && <div className="inline-error literature-error">{error}</div>}
      </div>

      {jobs.length > 0 && (
        <section className="panel literature-queue-panel">
          <div className="literature-queue-head">
            <div><strong>Очередь работ</strong><span>{jobs.length} {pluralJobs(jobs.length)}</span></div>
          </div>
          <div className="literature-job-list">
            {jobs.map((job, index) => (
              <div key={job.id} className={`literature-job-row ${selectedJob?.id === job.id ? "selected" : ""}`}>
                <button type="button" className="literature-job-main" onClick={() => chooseJob(job)}>
                  <span className="literature-job-index">{index + 1}</span>
                  <span className="literature-job-copy">
                    <strong>{job.originalName}</strong>
                    <small>{job.error || job.progressMessage || JOB_COPY[job.status]}</small>
                    {(job.status === "running" || job.status === "cancelling") && (
                      <span className="literature-progress-track"><span style={{ width: `${Math.max(2, job.progress)}%` }} /></span>
                    )}
                  </span>
                  <span className={`literature-queue-status ${job.status}`}>{JOB_COPY[job.status]}</span>
                </button>
                <div className="literature-job-actions">
                  {(job.status === "queued" || job.status === "running" || job.status === "cancelling") && (
                    <button className="text-button" disabled={job.status === "cancelling"} onClick={() => void stop(job)}>{job.status === "queued" ? "Отменить" : "Остановить"}</button>
                  )}
                  {(job.status === "failed" || job.status === "cancelled") && <button className="text-button" onClick={() => void retry(job)}>Повторить</button>}
                  {!(["running", "cancelling"] as LiteratureJob["status"][]).includes(job.status) && (
                    <button className="literature-job-remove" aria-label="Удалить" title="Удалить" onClick={() => void remove(job)}>×</button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {selectedJob?.status === "failed" && (
        <div className="inline-error literature-selected-error">{selectedJob.error || "Проверка не завершилась."}</div>
      )}

      {result && (
        <>
          <LiteratureReport
            result={result}
            rows={visibleRows}
            actions={(
              <>
                <button className="button secondary" onClick={downloadTsv}>TSV</button>
                <button className="button secondary" onClick={() => void downloadHtml()}>HTML</button>
                <button className="button primary" onClick={() => void printPdf()}>PDF</button>
              </>
            )}
            controls={(
              <div className="literature-segmented" role="tablist" aria-label="Фильтр результатов">
              <button className={filter === "attention" ? "active" : ""} onClick={() => setFilter("attention")}>Требуют внимания · {attentionCount}</button>
              <button className={filter === "confirmed" ? "active" : ""} onClick={() => setFilter("confirmed")}>Подтверждено · {confirmedCount}</button>
              <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>Все · {result.reference_count}</button>
              </div>
            )}
          />
        </>
      )}
    </section>
  );
}

function pluralJobs(value: number) {
  const mod100 = value % 100;
  const mod10 = value % 10;
  if (mod100 >= 11 && mod100 <= 14) return "работ";
  if (mod10 === 1) return "работа";
  if (mod10 >= 2 && mod10 <= 4) return "работы";
  return "работ";
}
