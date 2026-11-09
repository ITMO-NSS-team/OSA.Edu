import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { useLanguage } from "../i18n";
import type { CriterionResult, PresentationJob, PresentationResult, PresentationRuntime, PresentationType } from "./types";
import "./presentations.css";

export function PresentationsPage() {
  const { language } = useLanguage();
  const t = (ru: string, en: string) => language === "ru" ? ru : en;
  const [runtime, setRuntime] = useState<PresentationRuntime | null>(null);
  const [jobs, setJobs] = useState<PresentationJob[]>([]);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<PresentationJob | null>(null);
  const selectedId = useRef<string | null>(null);
  const [result, setResult] = useState<PresentationResult | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [presentationType, setPresentationType] = useState<PresentationType>("scientific");
  const [outputLanguage, setOutputLanguage] = useState<"ru" | "en">(language);
  const [criteria, setCriteria] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [resultError, setResultError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const mutation = useRef(false);

  useEffect(() => {
    const abort = new AbortController();
    void api.presentationStatus(abort.signal).then(setRuntime).catch(e => {
      if (!abort.signal.aborted) setError((e as Error).message);
    });
    return () => abort.abort();
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const currentId = selectedId.current;
        const [items, current] = await Promise.all([
          api.presentationJobs(offset, abort.signal),
          currentId ? api.presentationJob(currentId, abort.signal) : Promise.resolve(null),
        ]);
        if (abort.signal.aborted) return;
        setJobs(items);
        if (current && selectedId.current === current.id) setSelected(current);
        setPollError("");
      } catch (e) {
        if (!abort.signal.aborted) setPollError((e as Error).message);
      }
      if (!abort.signal.aborted) timer = setTimeout(tick, 2000);
    }
    void tick();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [offset]);

  useEffect(() => {
    const abort = new AbortController();
    setResult(null); setResultError("");
    if (selected?.resultAvailable) {
      void api.presentationResult(selected.id, abort.signal).then(value => {
        if (!abort.signal.aborted) setResult(value);
      }).catch(e => { if (!abort.signal.aborted) setResultError((e as Error).message); });
    }
    return () => abort.abort();
  }, [selected?.id, selected?.attempts, selected?.resultAvailable]);

  function open(job: PresentationJob) {
    selectedId.current = job.id;
    setSelected(job);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!files.length || mutation.current || !runtime?.ok) return;
    setError("");
    if (files.length > runtime.maxBatch) {
      setError(t(`За один раз можно загрузить до ${runtime.maxBatch} PDF.`, `Upload up to ${runtime.maxBatch} PDFs at once.`));
      return;
    }
    if (files.some(file => !file.name.toLowerCase().endsWith(".pdf") || !file.size || file.size > runtime.maxFileSizeMb * 1024 * 1024)) {
      setError(t(`Нужны непустые PDF размером до ${runtime.maxFileSizeMb} МБ.`, `Use non-empty PDFs up to ${runtime.maxFileSizeMb} MB.`));
      return;
    }
    mutation.current = true; setBusy(true);
    try {
      const body = new FormData();
      files.forEach(file => body.append("files", file));
      body.append("presentationType", presentationType);
      body.append("language", outputLanguage);
      body.append("criteria", JSON.stringify(criteria));
      const created = await api.createPresentationJobs(body);
      setOffset(0);
      setJobs(old => [...created, ...old.filter(job => !created.some(item => item.id === job.id))].slice(0, 50));
      if (created[0]) open(created[0]);
      setFiles([]);
      if (fileInput.current) fileInput.current.value = "";
    } catch (e) { setError((e as Error).message); }
    finally { mutation.current = false; setBusy(false); }
  }

  async function retry(job: PresentationJob) {
    if (mutation.current) return;
    mutation.current = true; setBusy(true); setError("");
    try {
      const updated = await api.retryPresentation(job.id);
      setJobs(old => old.map(item => item.id === updated.id ? updated : item));
      open(updated);
    } catch (e) { setError((e as Error).message); }
    finally { mutation.current = false; setBusy(false); }
  }

  const labels: Record<PresentationJob["status"], string> = {
    queued: t("В очереди", "Queued"), running: t("Проверяется", "Running"),
    completed: t("Завершена", "Completed"), partial: t("Частичный результат", "Partial result"),
    failed: t("Ошибка", "Failed"), cancelled: t("Отменена", "Cancelled"),
  };
  const typeLabels: Record<PresentationType, string> = {
    scientific: t("Научная", "Scientific"), industrial: t("Индустриальная", "Industrial"),
    collaborative: t("Коллаборативная", "Collaborative"), technological: t("Технологическая", "Technological"),
  };
  const allowedCriteria = runtime?.criteria.filter(item => item.presentationTypes.includes(presentationType)) ?? [];
  function criterionLabel(id: string) {
    const info = runtime?.criteria.find(item => item.id === id);
    return info ? (language === "ru" ? info.labelRu : info.labelEn) : id.replaceAll("_", " ");
  }

  return <section className="page sg-page">
    <div className="page-title"><div>
      <h1>{t("Проверка презентаций", "Presentation check")}</h1>
      <p>{t("SlideGuard: оформление, содержание слайдов и структура презентации.", "SlideGuard: slide design, content, and presentation structure.")}</p>
    </div><button type="button" className="button secondary" onClick={async () => {
      try { setRuntime(await api.presentationStatus()); setError(""); }
      catch (e) { setError((e as Error).message); }
    }}>{t("Проверить подключение", "Check connection")}</button></div>

    {error && <p role="alert" className="sg-error">{error}</p>}
    {!runtime && !error && <p role="status">{t("Проверяем подключение SlideGuard…", "Checking SlideGuard connection…")}</p>}
    {runtime && !runtime.ok && <div role="status" className="panel sg-notice">
      <strong>{t("SlideGuard не готов к запуску", "SlideGuard is not ready")}</strong>
      <p>{runtime.error || t("Настройте API-ключ и endpoint модели с поддержкой изображений.", "Configure an API key and an image-capable model endpoint.")}</p>
      <p className="hint">{t("Настройка описана в docs/slideguard_integration.md. Сохранённые результаты доступны ниже.", "Setup is described in docs/slideguard_integration.md. Saved results remain available below.")}</p>
    </div>}

    <form className="panel sg-upload" onSubmit={submit}>
      <h2>{t("Новая проверка", "New check")}</h2>
      <label><span>{t("Презентации PDF", "PDF presentations")}</span>
        <input ref={fileInput} type="file" accept=".pdf,application/pdf" multiple disabled={busy}
          onChange={event => setFiles(Array.from(event.target.files ?? []))} />
      </label>
      {files.length > 0 && <p>{t("Выбрано файлов", "Selected files")}: {files.length} · {files.map(file => file.name).join(", ")}</p>}
      <div className="form-grid">
        <label><span>{t("Тип презентации", "Presentation type")}</span>
          <select value={presentationType} disabled={busy} onChange={event => { setPresentationType(event.target.value as PresentationType); setCriteria([]); }}>
            {Object.entries(typeLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select></label>
        <label><span>{t("Язык результатов", "Result language")}</span>
          <select value={outputLanguage} disabled={busy} onChange={event => setOutputLanguage(event.target.value as "ru" | "en")}>
            <option value="ru">Русский</option><option value="en">English</option>
          </select></label>
      </div>
      {allowedCriteria.length > 0 && <details className="sg-criteria"><summary>{t("Выбрать критерии", "Select criteria")} · {criteria.length || t("все", "all")}</summary>
        <p className="hint">{t("Без выбора будут применены все критерии SlideGuard для этого типа презентации.", "With no selection, all SlideGuard criteria for this presentation type are applied.")}</p>
        {allowedCriteria.map(item => <label key={item.id} className="sg-check"><input type="checkbox" checked={criteria.includes(item.id)} disabled={busy}
          onChange={event => setCriteria(old => event.target.checked ? [...old, item.id] : old.filter(id => id !== item.id))} />
          <span>{language === "ru" ? item.labelRu : item.labelEn}</span></label>)}
      </details>}
      {runtime && <p className="hint">PDF ≤ {runtime.maxFileSizeMb} {t("МБ", "MB")} · ≤ {runtime.maxPages} {t("слайдов", "slides")} · {t("до", "up to")} {runtime.maxBatch} {t("файлов за загрузку", "files per upload")}<br />
        {t("Модель", "Model")}: {runtime.model}. {t("Изображения слайдов отправляются настроенному LLM-провайдеру. Запуск может быть платным.", "Slide images are sent to the configured LLM provider. Evaluation may incur charges.")}</p>}
      <button className="button primary" type="submit" disabled={busy || !files.length || !runtime?.ok}>
        {busy ? t("Отправляем…", "Submitting…") : t("Проверить презентации", "Check presentations")}
      </button>
    </form>

    <div className="sg-workspace">
      <aside className="panel sg-history"><h2>{t("История проверок", "Check history")}</h2>
        {pollError && <p role="alert" className="sg-error">{pollError}</p>}
        {!jobs.length && <p className="hint">{t("На этой странице пока нет проверок.", "No checks on this page yet.")}</p>}
        {jobs.map(job => <button type="button" className={`sg-job ${selected?.id === job.id ? "selected" : ""}`} key={job.id} onClick={() => open(job)}>
          <strong>{job.originalName}</strong><span className={`sg-status ${job.status}`}>{labels[job.status]}</span>
          <small>{new Date(job.createdAt).toLocaleString(language)} · {job.pageCount} {t("слайдов", "slides")}</small>
        </button>)}
        <div className="actions"><button type="button" className="button secondary" disabled={!offset} onClick={() => setOffset(old => Math.max(0, old - 50))}>{t("Назад", "Previous")}</button>
          <button type="button" className="button secondary" disabled={jobs.length < 50} onClick={() => setOffset(old => old + 50)}>{t("Далее", "Next")}</button></div>
      </aside>
      <div className="sg-detail">
        {selected ? <>
          <section className="panel"><div className="row between"><h2>{selected.originalName}</h2><span className={`sg-status ${selected.status}`}>{labels[selected.status]}</span></div>
            <p>{t("Тип", "Type")}: {typeLabels[selected.options.presentationType]} · {t("Попытка", "Attempt")}: {selected.attempts} · {selected.model}</p>
            {(selected.status === "queued" || selected.status === "running") && <>
              <progress max={100} value={selected.progress} aria-label={t("Состояние проверки", "Check progress")} />
              <p role="status">{selected.progressMessage}</p>
            </>}
            {selected.error && <p role="alert" className="sg-error">{selected.error}</p>}
            <div className="actions">
              {(["failed", "partial", "cancelled"] as string[]).includes(selected.status) && <button type="button" className="button secondary" disabled={busy} onClick={() => void retry(selected)}>{t("Повторить проверку", "Retry check")}</button>}
              {selected.resultAvailable && <a className="button secondary" href={api.presentationDownloadUrl(selected.id, "json")}>{t("Скачать JSON", "Download JSON")}</a>}
              {selected.reportAvailable && <a className="button secondary" href={api.presentationDownloadUrl(selected.id, "pdf")}>{t("Скачать PDF-отчёт", "Download PDF report")}</a>}
              {selected.logAvailable && <a className="button secondary" href={api.presentationDownloadUrl(selected.id, "log")}>{t("Технический лог", "Technical log")}</a>}
            </div>
          </section>
          {resultError && <p role="alert" className="sg-error">{resultError}</p>}
          {result && <PresentationReport value={result} label={criterionLabel} />}
        </> : <div className="panel"><p>{t("Выберите проверку из истории или загрузите презентации.", "Select a saved check or upload presentations.")}</p></div>}
      </div>
    </div>
  </section>;
}

function PresentationReport({ value, label }: { value: PresentationResult; label: (id: string) => string }) {
  const { language } = useLanguage();
  const t = (ru: string, en: string) => language === "ru" ? ru : en;
  const report = value.evaluation;
  const failed = new Set(value.diagnostics.failedCriteria.map(item => `${item.slideId}:${item.criterion}`));
  return <div className="sg-report">
    <section className="panel"><h2>{t("Результаты SlideGuard", "SlideGuard results")}</h2>
      {value.outcome !== "completed" && <p className="sg-notice">{t("Результат неполный: часть анализа не выполнена. Итоговая оценка не показана.", "The result is incomplete: part of the analysis failed. The overall score is withheld.")}</p>}
      {value.outcome === "completed" && report.overall_score !== null && <p className="sg-score">{report.overall_score} <span>/ 5</span></p>}
      {report.tldr && <p className="sg-prewrap">{report.tldr}</p>}
      {report.summary && <details><summary>{t("Полное резюме", "Full summary")}</summary><p className="sg-prewrap">{report.summary}</p></details>}
      {value.diagnostics.reportError && <p className="hint">{value.diagnostics.reportError}</p>}
    </section>
    {report.deck_evaluations && <section className="panel"><h3>{t("Презентация в целом", "Whole presentation")}</h3>
      {Object.entries(report.deck_evaluations.evaluations).map(([key, item]) => <CriterionView key={key} title={label(key)} value={item} failed={failed.has(`null:${key}`)} />)}
    </section>}
    <section className="panel"><h3>{t("По слайдам", "By slide")}</h3>
      {report.slide_evaluations.map(slide => <details key={slide.slide_id} className="sg-slide">
        <summary>{t("Слайд", "Slide")} {slide.slide_id} · {slide.slide_description?.title || t("Без заголовка", "Untitled")}</summary>
        {slide.slide_description?.summary && <p className="sg-prewrap">{slide.slide_description.summary}</p>}
        {Object.entries(slide.evaluations ?? {}).map(([key, item]) => <CriterionView key={key} title={label(key)} value={item} failed={failed.has(`${slide.slide_id}:${key}`)} />)}
      </details>)}
    </section>
  </div>;
}

function CriterionView({ title, value, failed }: { title: string; value: CriterionResult; failed: boolean }) {
  const { language } = useLanguage();
  return <details className="sg-criterion"><summary>{title} <span>{failed || value.error_message ? (language === "ru" ? "Не выполнено" : "Not evaluated") : value.reason ? (language === "ru" ? "Не применяется" : "Not applicable") : value.score == null ? "" : `${value.score} / 5`}</span></summary>
    {Object.entries(value).filter(([key]) => key !== "score").map(([key, item]) => <div key={key} className="sg-field"><DataValue value={item} depth={0} /></div>)}
  </details>;
}

function DataValue({ value, depth }: { value: unknown; depth: number }) {
  const { language } = useLanguage();
  const labels: Record<string, string> = language === "ru" ? {
    severity: "Серьёзность", evaluation_element: "Замечание", evaluation_suggestion: "Рекомендация",
    comments: "Комментарии", recommendations: "Рекомендации", reason: "Причина", error_message: "Ошибка",
    title: "Заголовок", description: "Описание", summary: "Резюме",
  } : {};
  if (value == null) return null;
  if (Array.isArray(value)) return <div>{value.map((item, i) => <div className="sg-finding" key={i}><DataValue value={item} depth={depth + 1} /></div>)}</div>;
  if (typeof value === "object") {
    if (depth > 6) return <pre>{JSON.stringify(value, null, 2)}</pre>;
    return <dl>{Object.entries(value).map(([key, item]) => <div key={key}><dt>{labels[key] || key.replaceAll("_", " ")}</dt><dd><DataValue value={item} depth={depth + 1} /></dd></div>)}</dl>;
  }
  return <span className="sg-prewrap">{String(value)}</span>;
}
