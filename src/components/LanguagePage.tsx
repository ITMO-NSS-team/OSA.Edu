import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useLanguage } from "../i18n";
import type { Health, LanguageCategory, LanguageJob } from "../types";
import "./language.css";
import { canResumeLanguage, languageFindings, languageProgress, languageStatus } from "../languageReview";

type Props = { models: Health["models"]; configured: Health["configured"] };
const CATEGORIES: LanguageCategory[] = ["spelling", "grammar", "syntax", "punctuation", "style"];
const LABELS = {
  ru: { spelling: "Орфография", grammar: "Грамматика", syntax: "Синтаксис", punctuation: "Пунктуация", style: "Стилистика" },
  en: { spelling: "Spelling", grammar: "Grammar", syntax: "Syntax", punctuation: "Punctuation", style: "Style" },
};

export function LanguagePage({ models, configured }: Props) {
  const { language } = useLanguage();
  const ru = language === "ru";
  const t = ru ? {
    title: "Проверка языка", subtitle: "Отдельная корректура текста ВКР: орфография, грамматика, синтаксис и пунктуация.",
    style: "Дополнительно проверять стилистику", styleHint: "Стилистика субъективнее правил русского языка — замечания выводятся отдельно.",
    model: "Модель", file: "Выберите PDF", run: "Запустить проверку", uploading: "Загружаем…",
    jobs: "Проверки", noJobs: "Проверок пока нет.", all: "Все замечания", noFindings: "В обработанном тексте не найдено уверенных замечаний.",
    partial: "Проверка неполная: есть непроверенные фрагменты. Отсутствие замечаний не означает отсутствия ошибок.",
    safe: "Замечания — предложения для ручной проверки. Исходный PDF не изменяется. Текст передаётся выбранной LLM; файл и отчёт сохраняются на сервере до удаления проверки.",
    original: "Было", proposed: "Предлагается", reason: "Пояснение", page: "Страница", remove: "Удалить", stop: "Остановить", retry: "Повторить", pdf: "PDF-отчёт", json: "JSON",
    finished: "Завершено", failure: "Ошибка", cancelled: "Остановлено", running: "Проверяется", queued: "В очереди", partialStatus: "Частично",
  } : {
    title: "Language review", subtitle: "Separate proofreading for thesis text: spelling, grammar, syntax, and punctuation.",
    style: "Include optional style review", styleHint: "Stylistic recommendations are more subjective and appear separately.",
    model: "Model", file: "Choose PDF", run: "Start language review", uploading: "Uploading…",
    jobs: "Reviews", noJobs: "No reviews yet.", all: "All suggestions", noFindings: "No high-confidence suggestions found in the processed text.",
    partial: "Incomplete review: some text was not processed. No suggestions does not mean no errors.",
    safe: "Findings require manual review. The PDF is never changed. Text is sent to the selected LLM provider; uploaded PDFs and reports remain on the server until you delete the review.",
    original: "Original", proposed: "Suggested", reason: "Explanation", page: "Page", remove: "Delete", stop: "Stop", retry: "Retry", pdf: "PDF report", json: "JSON",
    finished: "Completed", failure: "Failed", cancelled: "Stopped", running: "Running", queued: "Queued", partialStatus: "Partial",
  };
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const availableModels = useMemo(() => models.filter(item => item.provider === "host" ? configured.host : item.provider === "openrouter" ? configured.openrouter : configured.gemini), [models, configured]);
  const [model, setModel] = useState("");
  const chosenModel = availableModels.some(item => item.id === model) ? model : (availableModels.find(item => item.tier === "production")?.id || availableModels[0]?.id || "");
  const [style, setStyle] = useState(false);
  const [includeDisputed, setIncludeDisputed] = useState(false);
  const [jobs, setJobs] = useState<LanguageJob[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<LanguageCategory | "all">("all");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [actionBusy, setActionBusy] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const refresh = async () => {
      try {
        const value = await api.languageJobs();
        if (!mounted) return;
        setJobs(value);
        setSelectedId(prev => (prev && value.some(job => job.id === prev)) ? prev : (value[0]?.id || null));
      } catch (cause) { if (mounted) setError((cause as Error).message); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2200);
    return () => { mounted = false; window.clearInterval(timer); };
  }, []);
  const selected = jobs.find(job => job.id === selectedId) ?? null;
  const result = selected?.result;
  const issues = languageFindings(result?.issues || [], filter);
  const disputed = languageFindings(result?.disputed || [], filter);
  const selectedProgress = selected ? languageProgress(selected) : 0;

  async function run() {
    if (!file || busy || !chosenModel) return;
    setBusy(true); setError("");
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("model", chosenModel);
      body.append("style", String(style));
      body.append("includeDisputed", String(includeDisputed));
      const created = await api.createLanguageJob(body);
      setSelectedId(created.id); setJobs(current => [created, ...current.filter(job => job.id !== created.id)]); setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      setFilter("all");
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  async function action(job: LanguageJob, operation: "cancel" | "retry" | "delete") {
    if (actionBusy) return;
    setActionBusy(job.id); setError("");
    try {
      if (operation === "cancel") await api.cancelLanguageJob(job.id);
      if (operation === "retry") await api.retryLanguageJob(job.id);
      if (operation === "delete") await api.deleteLanguageJob(job.id);
      const next = await api.languageJobs();
      setJobs(next);
      if (operation === "delete" && selectedId === job.id) setSelectedId(next[0]?.id ?? null);
    } catch (cause) { setError((cause as Error).message); }
    finally { setActionBusy(null); }
  }
  const labelStatus = (status: LanguageJob["status"]) => ({
    done: t.finished, failed: t.failure, cancelled: t.cancelled,
    running: t.running, queued: t.queued, cancelling: t.running, partial: t.partialStatus,
  }[status]);

  return <section className="page language-review-page">
    <div className="page-title"><div><h1>{t.title}</h1><p>{t.subtitle}</p></div></div>
    <div className="panel language-start">
      <label className="language-field"><span>{t.model}</span><select value={chosenModel} onChange={e => setModel(e.target.value)} disabled={!availableModels.length}>
        {availableModels.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select></label>
      <label className="language-toggle-style"><input type="checkbox" checked={style} onChange={e => setStyle(e.target.checked)} />
        <span><strong>{t.style}</strong><small>{t.styleHint}</small></span></label>
      <label className="language-toggle-style"><input type="checkbox" checked={includeDisputed} onChange={e => setIncludeDisputed(e.target.checked)} />
        <span><strong>{ru ? "Показывать спорные рекомендации отдельно" : "Include disputed recommendations separately"}</strong>
          <small>{ru ? "Они не входят в число основных замечаний и требуют ручной оценки." : "They are excluded from main findings and require manual assessment."}</small></span></label>
      <div className="language-upload-row">
        <label className="language-upload"><input ref={fileRef} type="file" accept=".pdf,application/pdf" onChange={e => setFile(e.target.files?.[0] || null)} />
          <span>{file ? file.name : t.file}</span></label>
        <button type="button" className="button primary" disabled={!file || !chosenModel || busy} onClick={() => void run()}>{busy ? t.uploading : t.run}</button>
      </div>
      {!availableModels.length && <div className="inline-error">{ru ? "Не настроен ни один LLM-провайдер." : "No LLM provider is configured."}</div>}
      <p className="language-hint">{t.safe}</p>
    </div>
    {error && <div className="inline-error">{error}</div>}
    <div className="language-layout">
      <aside className="panel language-history"><h2>{t.jobs}</h2>
        {jobs.length === 0 && <p>{t.noJobs}</p>}
        {jobs.map(job => <button key={job.id} type="button" className={`language-history-item ${selectedId === job.id ? "active" : ""}`} onClick={() => { setSelectedId(job.id); setFilter("all"); }}>
          <strong>{job.originalName}</strong><span>{labelStatus(languageStatus(job))} · {languageProgress(job)}%</span>
        </button>)}
      </aside>
      <article className="panel language-results">
        {selected && <>
          <div className="language-result-head"><div><h2>{selected.originalName}</h2><p>{selected.progressMessage}</p></div><div className="language-actions">
            {(selected.status === "queued" || selected.status === "running") && <button className="button secondary" disabled={!!actionBusy} onClick={() => void action(selected, "cancel")}>{t.stop}</button>}
            {canResumeLanguage(selected) && <button className="button secondary" disabled={!!actionBusy} onClick={() => void action(selected, "retry")}>{ru ? "Проверить незавершённые части" : "Resume unfinished parts"}</button>}
            {(["done", "partial", "failed", "cancelled"] as string[]).includes(selected.status) && <button className="button secondary" disabled={!!actionBusy} onClick={() => void action(selected, "delete")}>{t.remove}</button>}
          </div></div>
          <div className="language-progress" role="progressbar" aria-valuenow={selectedProgress} aria-valuemin={0} aria-valuemax={100}><div style={{width: `${selectedProgress}%`}} /></div>
          {selected.error && <div className="inline-error">{selected.error}</div>}
          {result && <>
            {!result.complete && <p className="language-warning">{t.partial} {result.checkedChunks}/{result.totalChunks}</p>}
            {result.warnings.filter(w => !w.includes("Промежуточный")).map((warning, idx) => <p className="language-warning" key={idx}>{warning}</p>)}
            <div className="language-summary"><strong>{result.totalIssues}</strong><span>{ru ? "предложений" : "suggestions"}</span><span>{result.checkedChunks}/{result.totalChunks} {ru ? "пакетов обработано" : "batches processed"}</span></div>
            {result.coverage && <p className="language-hint">{ru ? "Проверено символов" : "Characters reviewed"}: {result.coverage.checkedCharacters.toLocaleString()} / {result.coverage.totalCharacters.toLocaleString()} ({selectedProgress}%).</p>}
            {selected.canResume === false && !result.complete && <p className="language-hint">{ru ? "Для этого запуска нет незавершённых пакетов с контрольными точками. История сохранена; непроверяемые страницы могут требовать OCR, а старый запуск — новой загрузки PDF." : "No unfinished checkpointed packets are available. History is preserved; image-only pages may require OCR, and a legacy review requires a new PDF upload."}</p>}
            <div className="language-downloads">
              {(["done", "partial", "failed", "cancelled"] as string[]).includes(selected.status) && <><a className="button secondary" href={api.languageReportPdfUrl(selected.id)}>{t.pdf}</a><a className="button secondary" href={api.languageReportJsonUrl(selected.id)} download>{t.json}</a></>}
            </div>
            <div className="language-filters"><button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>{t.all} ({result.issues.length})</button>
              {CATEGORIES.filter(k => k !== "style" || selected.styleEnabled).map(category => <button key={category} className={filter === category ? "active" : ""} onClick={() => setFilter(category)}>{LABELS[language][category]} ({result.counts?.[category] || 0})</button>)}
            </div>
            {issues.map(issue => <div className="language-issue" key={issue.id}>
              <div className="language-issue-top"><strong>{LABELS[language][issue.category]}</strong><span>{t.page} {issue.page}</span></div>
              <div className="language-side"><span>{t.original}</span><p>{issue.quote}</p></div>
              <div className="language-side language-replacement"><span>{t.proposed}</span><p>{issue.replacement}</p></div>
              <p className="language-explanation"><b>{t.reason}:</b> {issue.explanation}</p>
              {issue.validation?.reason && <p className="language-hint">{ru ? "Перепроверка" : "Validation"}: {issue.validation.reason}</p>}
              {issue.context && <details><summary>{ru ? "Исходный контекст" : "Source context"}</summary><p>{issue.contextBefore}</p><p><b>{issue.context}</b></p><p>{issue.contextAfter}</p><small>{issue.fragmentId}</small></details>}
            </div>)}
            {issues.length === 0 && <p className="language-no-issues">{t.noFindings}</p>}
            {disputed.length > 0 && <section className="language-disputed"><h3>{ru ? "Спорные рекомендации" : "Disputed recommendations"}</h3>
              <p className="language-hint">{ru ? "Обязательность этих исправлений не установлена. Они не входят в основные замечания." : "These corrections are not established as mandatory and are excluded from main findings."}</p>
              {disputed.map(issue => <div className="language-issue" key={issue.id}><strong>{LABELS[language][issue.category]} · {t.page} {issue.page}</strong><p>{issue.quote}</p><p>{issue.replacement}</p><p>{issue.disputeReason || issue.validation?.reason}</p></div>)}
            </section>}
            {(result.uncheckedFragments?.length || result.errors?.length) ? <details className="language-technical"><summary>{ru ? "Непроверенные участки и причины" : "Unchecked fragments and reasons"}</summary>
              {(result.uncheckedFragments || result.errors).map((item, idx) => <p key={idx}>{ru ? "Пакет" : "Packet"} {item.chunk}; {t.page} {(item.pages || []).join(", ") || "?"}: {item.kind ? `${item.kind}: ` : ""}{item.message}</p>)}
            </details> : null}
            <details className="language-technical"><summary>{ru ? "Расход и техническое состояние" : "Usage and technical status"}</summary>
              <p>{ru ? "Запросы / повторы" : "Requests / retries"}: {result.usage.requests || 0} / {result.usage.retries || 0}</p>
              <p>{ru ? "Оценочные входные токены" : "Estimated input tokens"}: {result.usage.estimatedInputTokens || 0}</p>
              {result.usage.actualInputTokens !== undefined && <p>{ru ? "Фактические входные / выходные токены" : "Actual input / output tokens"}: {result.usage.actualInputTokens} / {result.usage.actualOutputTokens || 0}</p>}
              {result.usage.actualCostUsd !== undefined && <p>{ru ? "Стоимость по данным провайдера" : "Provider-reported cost"}: ${result.usage.actualCostUsd.toFixed(4)}</p>}
              <p>{ru ? "Исключённые блоки" : "Excluded blocks"}: {Object.entries(result.extraction.omittedBlocks || {}).map(([key, value]) => `${key}: ${value}`).join(", ") || "0"}</p>
            </details>
          </>}
        </>}
      </article>
    </div>
  </section>;
}
