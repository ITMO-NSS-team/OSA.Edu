import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { jobStatusLabel, modeLabel, statusLabel } from "../labels";
import type { DocumentElementType, DocumentMap, DocumentMapElement, Job, Rule, RuleResult, ProviderDiagnostic, RuleStatus, StructureBlock, StructureDetails } from "../types";

type StatusFilter = RuleStatus | "all";
interface Props {
  jobs: Job[];
  onAction: (id: string, action: "cancel" | "retry" | "retryFailed" | "delete") => Promise<void>;
  onChanged: () => Promise<void>;
}
const STATUS_ORDER: RuleStatus[] = ["violation", "pass", "uncertain", "not_checked", "not_applicable"];
const ACTIVE = ["queued", "extracting", "mapping", "queued_check", "checking"];

export function ReportsPage({ jobs, onAction, onChanged }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(jobs[0]?.id ?? null);
  const [status, setStatus] = useState<StatusFilter>("violation");
  const [query, setQuery] = useState("");
  useEffect(() => {
    if (!selectedId && jobs[0]) setSelectedId(jobs[0].id);
    if (selectedId && !jobs.some((job) => job.id === selectedId)) setSelectedId(jobs[0]?.id ?? null);
  }, [jobs, selectedId]);
  const selected = jobs.find((job) => job.id === selectedId) ?? null;

  return <section className="page reports-layout">
    <aside className="jobs-panel panel">
      <div className="row between"><h2>Задачи</h2><span>{jobs.length}</span></div>
      <div className="jobs-list">
        {jobs.map((job) => <button key={job.id} className={`job-row ${selectedId === job.id ? "selected" : ""}`} onClick={() => setSelectedId(job.id)}>
          <strong>{job.originalName}</strong><span>{jobStatusLabel[job.status]}{ACTIVE.includes(job.status) ? ` · ${job.progress} %` : ""}</span>
        </button>)}
        {!jobs.length && <div className="empty small">Задач пока нет.</div>}
      </div>
    </aside>
    <div className="report-area">
      {!selected && <div className="empty panel">Выберите задачу.</div>}
      {selected?.status === "awaiting_review" && <StructureReview job={selected} onChanged={onChanged} onAction={onAction} />}
      {selected && selected.status !== "awaiting_review" && !selected.report && <JobState job={selected} onAction={onAction} />}
      {selected?.report && <Report job={selected} status={status} query={query} onStatus={setStatus} onQuery={setQuery} onAction={onAction} />}
    </div>
  </section>;
}

function JobState({ job, onAction }: { job: Job; onAction: Props["onAction"] }) {
  const active = ACTIVE.includes(job.status);
  return <div className="panel job-state">
    <h1>{job.originalName}</h1><p>{jobStatusLabel[job.status]}{active ? ` · ${job.progress} %` : ""}</p>
    {active && <progress max="100" value={job.progress} />}
    {job.status === "mapping" && <p className="hint">Вся работа передана выбранной модели OpenRouter одним запросом. После построения диапазонов проверка остановится для вашего подтверждения.</p>}
    {job.error && <div className="inline-error">{job.error}</div>}
    {job.diagnostics?.length ? <ApiDiagnostics title="Диагностика LLM" items={job.diagnostics} /> : null}
    <div className="actions">
      {active && <button className="button secondary" onClick={() => void onAction(job.id, "cancel")}>Отменить</button>}
      {job.status === "failed" && <button className="button primary" onClick={() => void onAction(job.id, "retry")}>Повторить</button>}
      {!active && <button className="button danger" onClick={() => void onAction(job.id, "delete")}>Удалить</button>}
    </div>
  </div>;
}

function StructureReview({ job, onChanged, onAction }: { job: Job; onChanged: Props["onChanged"]; onAction: Props["onAction"] }) {
  const [details, setDetails] = useState<StructureDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    try { setDetails(await api.structure(job.id)); setError(""); }
    catch (err) { setError((err as Error).message); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, [job.id, job.updatedAt]);

  async function mutate(action: () => Promise<unknown>) {
    setSaving(true);
    try { await action(); await Promise.all([load(), onChanged()]); }
    catch (err) { setError((err as Error).message); }
    finally { setSaving(false); }
  }

  if (loading && !details) return <div className="panel job-state"><h1>{job.originalName}</h1><p>Загрузка структуры…</p></div>;
  if (!details) return <div className="panel job-state"><h1>{job.originalName}</h1><div className="inline-error">{error}</div></div>;
  const map = details.map;
  return <div className="structure-review">
    <div className="page-title report-title">
      <div><h1>Проверьте структуру</h1><p>{job.originalName} · проверка правил ещё не началась</p></div>
      <div className="score-box"><strong>{map.elements.length}</strong><span>смысловых фрагментов</span></div>
    </div>

    <div className="panel review-intro">
      <div><strong>Что нужно проверить</strong><p>Убедитесь, что правильно выделены введение, цель, задачи, положения, все главы и заключение. При необходимости измените границы, тип или удалите лишний диапазон.</p></div>
      <div className="actions"><button className="button secondary" disabled={saving} onClick={() => void mutate(() => api.addMapElement(job.id, {}))}>Добавить фрагмент</button><button className="button primary" disabled={saving || !map.elements.length} onClick={() => void mutate(() => api.confirmStructure(job.id))}>Подтвердить и начать проверку</button></div>
    </div>

    {error && <div className="inline-error">{error}</div>}
    <div className="map-stats panel">
      <span>Попытки запроса структуры: <b>{map.usage.requests}</b></span>
      <span>Блоки: <b>{map.extraction.processedBlocks}/{map.extraction.totalBlocks}</b></span>
      <span>Неоднозначно: <b>{map.elements.filter((item) => item.state === "ambiguous").length}</b></span>
      <span>Повторы после ошибок: <b>{map.usage.retries}</b></span>
    </div>
    {map.issues.length > 0 && <div className="map-issues panel">{map.issues.map((issue, index) => <div key={`${issue.code}-${index}`} className={issue.severity}><b>{issue.code}</b><span>{issue.message}</span></div>)}</div>}
    {map.usage.diagnostics.length > 0 && <ApiDiagnostics title="Диагностика LLM при построении структуры" items={map.usage.diagnostics} />}

    <div className="review-sections">
      {map.elements.map((element) => <StructureCard key={element.id} jobId={job.id} element={element} blocks={details.blocks} disabled={saving} onMutate={mutate} />)}
    </div>
    <div className="actions between review-footer"><button className="button danger" onClick={() => void onAction(job.id, "delete")}>Удалить задачу</button><button className="button primary" disabled={saving || !map.elements.length} onClick={() => void mutate(() => api.confirmStructure(job.id))}>Подтвердить структуру и начать</button></div>
  </div>;
}

function StructureCard({ jobId, element, blocks, disabled, onMutate }: { jobId: string; element: DocumentMapElement; blocks: StructureBlock[]; disabled: boolean; onMutate: (action: () => Promise<unknown>) => Promise<void> }) {
  const [type, setType] = useState(element.type);
  const [label, setLabel] = useState(element.label);
  const [startBlockId, setStart] = useState(element.startBlockId);
  const [endBlockId, setEnd] = useState(element.endBlockId);
  const [quote, setQuote] = useState(element.quote || "");
  useEffect(() => { setType(element.type); setLabel(element.label); setStart(element.startBlockId); setEnd(element.endBlockId); setQuote(element.quote || ""); }, [element]);
  const startIndex = blocks.findIndex((item) => item.id === startBlockId);
  const endIndex = blocks.findIndex((item) => item.id === endBlockId);
  const preview = startIndex >= 0 && endIndex >= startIndex ? blocks.slice(startIndex, Math.min(endIndex + 1, startIndex + 5)) : [];
  const changed = type !== element.type || label !== element.label || startBlockId !== element.startBlockId || endBlockId !== element.endBlockId || quote !== (element.quote || "");
  return <details className={`panel structure-card ${element.state}`} open={element.state === "ambiguous"}>
    <summary><span className="structure-type">{elementTypeLabel[element.type]}</span><strong>{element.label}</strong><small>{element.pages.length ? `стр. ${element.pages[0]}–${element.pages.at(-1)}` : "страницы не определены"}</small></summary>
    <div className="structure-card-body">
      <div className="structure-fields">
        <label><span>Тип</span><select value={type} onChange={(event) => setType(event.target.value as DocumentElementType)}>{Object.entries(elementTypeLabel).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
        <label><span>Название</span><input value={label} onChange={(event) => setLabel(event.target.value)} /></label>
        <label><span>Первый блок</span><select value={startBlockId} onChange={(event) => setStart(event.target.value)}>{blocks.map((block) => <option key={block.id} value={block.id}>{blockOption(block)}</option>)}</select></label>
        <label><span>Последний блок</span><select value={endBlockId} onChange={(event) => setEnd(event.target.value)}>{blocks.map((block) => <option key={block.id} value={block.id}>{blockOption(block)}</option>)}</select></label>
        <label className="full-width"><span>Точная опорная цитата</span><textarea rows={3} value={quote} onChange={(event) => setQuote(event.target.value)} placeholder="Дословный текст названия, цели или заголовка внутри диапазона" /></label>
      </div>
      <div className="fragment-preview"><b>Начало диапазона</b>{preview.map((block) => <blockquote key={block.id}><span>{block.id}{block.page ? ` · стр. ${block.page}` : ""}</span>{block.text}</blockquote>)}</div>
      <p className="hint">Диапазон: {element.startBlockId} → {element.endBlockId} · источник: {element.source === "user" ? "изменено пользователем" : "LLM"} · статус: {element.state === "confirmed" ? "подтверждён" : "неоднозначен"}</p>
      {element.note && <p className="hint">{element.note}</p>}
      <div className="actions between">
        <button className="button danger" disabled={disabled} onClick={() => void onMutate(() => api.deleteMapElement(jobId, element.id))}>Удалить</button>
        <div className="actions"><button className="button secondary" disabled={disabled || element.state === "ambiguous"} onClick={() => void onMutate(() => api.updateMapElement(jobId, element.id, { state: "ambiguous" }))}>Пометить неоднозначным</button><button className="button primary" disabled={disabled || !changed} onClick={() => void onMutate(() => api.updateMapElement(jobId, element.id, { type, label, startBlockId, endBlockId, quote, state: "confirmed" }))}>Сохранить границы</button></div>
      </div>
    </div>
  </details>;
}

function Report({ job, status, query, onStatus, onQuery, onAction }: { job: Job; status: StatusFilter; query: string; onStatus: (value: StatusFilter) => void; onQuery: (value: string) => void; onAction: Props["onAction"] }) {
  const report = job.report!;
  const retryableCount = report.ruleResults.filter((item) => (item.status === "not_checked" && item.checkedBy === "llm") || (item.status === "uncertain" && item.checkedBy === "llm" && (item.evidenceStatus === "rejected" || Boolean(item.coverage && item.coverage.checkedCandidateCount < item.coverage.candidateCount)))).length;
  const catalog = useMemo(() => new Map(report.ruleCatalog.map((rule) => [rule.id, rule])), [report]);
  const results = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("ru");
    return report.ruleResults.filter((result) => {
      if (status !== "all" && result.status !== status) return false;
      if (!normalized) return true;
      const rule = catalog.get(result.ruleId);
      return `${result.ruleId} ${rule?.title || ""} ${rule?.requirement || ""} ${result.explanation}`.toLocaleLowerCase("ru").includes(normalized);
    });
  }, [report, catalog, status, query]);
  return <div>
    <div className="page-title report-title"><div><h1>{job.originalName}</h1><p>{report.summary}</p></div><div className="score-box"><strong>{report.score === null ? "—" : report.score}</strong><span>{report.score === null ? "оценка не рассчитана" : `/ 100 по проверенным правилам${report.scoreIsProvisional ? " · предварительно" : ""}`}</span></div></div>
    {job.error && <div className="inline-error">Последняя попытка завершилась ошибкой: {job.error}</div>}
    {report.documentMap && <ReadOnlyMap map={report.documentMap} />}
    <div className="status-grid">{STATUS_ORDER.map((item) => <button key={item} className={`status-card ${item} ${status === item ? "active" : ""}`} onClick={() => onStatus(item)}><strong>{countForStatus(report, item)}</strong><span>{statusLabel[item]}</span></button>)}</div>
    <div className="panel report-controls"><button className={status === "all" ? "text-button active" : "text-button"} onClick={() => onStatus("all")}>Все правила ({report.ruleResults.length})</button><input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Поиск по правилам отчёта" /><span>Покрытие: {Math.round(report.coverage * 100)} %</span></div>
    <div className="result-list">{results.map((result) => <RuleResultCard key={result.ruleId} result={result} rule={catalog.get(result.ruleId)} />)}{!results.length && <div className="empty panel">В этой категории правил нет.</div>}</div>
    <details className="panel technical-details"><summary>Промпты, маршрутизация и API</summary><div className="technical-body">
      <p><b>Модель:</b> {report.technical.model}</p><p><b>Версия:</b> {report.technical.appVersion}</p><p><b>Хеш промпта:</b> {report.technical.promptHash}</p><p><b>Профиль:</b> {job.profile === "core" ? "Ядро" : "Полный набор"}</p>
      <p><b>Маршрутизация:</b> {report.routing.strategy}; явно задано правил: {report.routing.explicitRules}; fallback: {report.routing.fallbackRules}; фрагментов: {report.routing.fragments}; запросов проверки: {report.routing.checkRequests}</p>
      <p><b>LLM-запросы:</b> {report.llmUsage.requests}; повторы: {report.llmUsage.retries}; ожидание limiter: {Math.round(report.llmUsage.rateLimitWaitMs / 1000)} с</p>
      {report.llmUsage.traces?.length > 0 && <div><b>Успешные обращения:</b><ul>{report.llmUsage.traces.map((item, index) => <li key={`${item.at}-${index}`}>{item.operation}: {item.provider}/{item.model}; upstream: {item.providerName || "—"}; compatibility: {item.compatibilityMode ? "да" : "нет"}; request: {item.requestId || "—"}</li>)}</ul></div>}{report.llmUsage.diagnostics.length > 0 && <ApiDiagnostics title="Диагностика OpenRouter и провайдеров" items={report.llmUsage.diagnostics} />}
      <label><span>Промпт проверки правил</span><textarea readOnly value={job.prompt} rows={12} /></label><label><span>Промпт структуры</span><textarea readOnly value={job.mapPrompt} rows={12} /></label>
      {report.warnings.length > 0 && <div><b>Предупреждения:</b><ul>{report.warnings.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>}
    </div></details>
    <div className="actions end report-actions"><a className="button secondary" href={api.reportJsonUrl(job.id)}>JSON</a><a className="button secondary" href={api.reportMarkdownUrl(job.id)}>Markdown</a>{retryableCount > 0 && <button className="button secondary" onClick={() => void onAction(job.id, "retryFailed")}>Повторить неудачные LLM-проверки ({retryableCount})</button>}<button className="button secondary" onClick={() => void onAction(job.id, "retry")}>Повторить всю проверку</button><button className="button danger" onClick={() => void onAction(job.id, "delete")}>Удалить</button></div>
  </div>;
}

function ReadOnlyMap({ map }: { map: DocumentMap }) {
  return <details className="panel map-panel"><summary><span><b>Подтверждённая структура</b></span><span>{map.elements.length} диапазонов · один запрос LLM</span></summary><div className="map-body"><div className="map-elements">{map.elements.map((element) => <div className="map-element" key={element.id}><div className="readonly-map-row"><span>{elementTypeLabel[element.type]}</span><strong>{element.label}</strong><small>{element.startBlockId} → {element.endBlockId}</small></div></div>)}</div></div></details>;
}

function ApiDiagnostics({ title, items }: { title: string; items: ProviderDiagnostic[] }) {
  return <details className="api-diagnostics" open><summary>{title} ({items.length})</summary><div>{items.map((item, index) => <div className="diagnostic-row" key={`${item.at}-${index}`}><b>{item.operation} · попытка {item.attempt} · HTTP {item.httpStatus || "—"}</b><span>{item.message}</span><small>{item.provider ? `${item.provider}${item.model ? ` · ${item.model}` : ""}; ` : ""}Повтор: {item.retryable ? "да" : "нет"}; Retry-After: {item.retryAfterMs} мс; backoff: {item.backoffMs} мс{item.networkCode ? `; network: ${item.networkCode}` : ""}{item.providerCode ? `; code: ${item.providerCode}` : ""}{item.providerName ? `; upstream: ${item.providerName}` : ""}{item.requestId ? `; request: ${item.requestId}` : ""}{item.quotaMetric ? `; quota: ${item.quotaMetric}` : ""}{item.quotaDescription ? `; ${item.quotaDescription}` : ""}</small></div>)}</div></details>;
}

function RuleResultCard({ result, rule }: { result: RuleResult; rule?: Rule }) {
  return <details className={`result-card ${result.status}`} open={result.status === "violation" && (result.severity === "critical" || result.severity === "major")}><summary><span className={`status-badge ${result.status}`}>{statusLabel[result.status]}</span><span className="rule-id">{result.ruleId}</span><span className="rule-title">{rule?.title || result.explanation}</span></summary><div className="result-body">
    {rule && <><p className="requirement">{rule.requirement}</p><div className="meta-grid"><span><b>Категория:</b> {rule.category}</span><span><b>Тип исходного правила:</b> {modeLabel[rule.mode]}</span><span><b>Источник:</b> {rule.sourceLabel}, строка {rule.sourceLine}</span><span><b>Метод:</b> {result.checkedBy === "detector" ? "код / структура" : result.checkedBy === "llm" ? "LLM" : "система"}</span></div></>}
    <div className="explanation"><b>Результат проверки</b><p>{result.explanation}</p></div>
    {result.coverage && <p className="coverage-line">Проверено назначенных фрагментов: {result.coverage.checkedCandidateCount} из {result.coverage.candidateCount}; полная область: {result.coverage.exhaustive ? "да" : "нет"}.</p>}
    {result.checkedFragments?.length ? <p className="hint">Фрагменты: {result.checkedFragments.join(", ")}</p> : null}{result.relatedRuleIds?.length ? <p className="hint">То же замечание связано с правилами: {result.relatedRuleIds.join(", ")}</p> : null}{result.consistencyNotes?.length ? <p className="hint">Проверка согласованности: {result.consistencyNotes.join(" ")}</p> : null}{result.evidenceStatus === "rejected" ? <p className="inline-error">Заявленное нарушение не подтверждено допустимой цитатой или полной матрицей.</p> : null}{result.evidenceStatus === "coverage_verified" ? <p className="hint">Отсутствие проверено по полной назначенной области.</p> : null}
    {result.termFindings?.length ? <div className="evidence"><b>Разбор обозначений</b><div className="matrix-table"><table><thead><tr><th>Термин</th><th>Тип</th><th>Статус</th></tr></thead><tbody>{result.termFindings.map((item) => <tr key={`${result.ruleId}-${item.term}`}><td>{item.term}</td><td>{item.kind}</td><td>{item.status}</td></tr>)}</tbody></table></div></div> : null}
    {result.coverageMatrix?.length ? <div className="evidence"><b>Матрица полного покрытия</b><div className="matrix-table"><table><thead><tr><th>Фрагмент</th><th>Блоки</th><th>Полнота</th><th>Элементы</th></tr></thead><tbody>{result.coverageMatrix.map((row) => <tr key={`${result.ruleId}-${row.fragmentId}`}><td>{row.label}</td><td>{row.checkedBlocks}/{row.totalBlocks}</td><td>{row.complete ? "полная" : "неполная"}</td><td>{row.items.map((item) => `${item.name}: ${item.status === "found" ? "найдено" : item.status === "not_found" ? "не найдено" : "неоднозначно"}`).join("; ")}</td></tr>)}</tbody></table></div></div> : null}
    {result.evidence.length > 0 && <div className="evidence"><b>Подтверждённые доказательства</b>{result.evidence.map((item, index) => <blockquote key={`${item.blockId}-${index}`}><span>{item.location}{item.page ? ` · стр. ${item.page}` : ""} · цитата найдена в исходном блоке</span>{item.quote}</blockquote>)}</div>}
    {result.fix && <div className="fix"><b>Исправление</b><p>{result.fix}</p></div>}
  </div></details>;
}

function blockOption(block: StructureBlock) { const text = block.text.replace(/\s+/g, " ").slice(0, 90); return `${block.id}${block.page ? ` · стр. ${block.page}` : ""} · ${text}`; }
function countForStatus(report: NonNullable<Job["report"]>, status: RuleStatus) { return report.ruleResults.filter((item) => item.status === status).length; }
const elementTypeLabel: Record<DocumentElementType, string> = { title: "Название", abstract: "Аннотация", introduction: "Введение", goal: "Цель", tasks: "Задачи", defense_statements: "Положения на защиту", chapter: "Глава", chapter_conclusions: "Выводы по главе", conclusion: "Заключение", bibliography: "Библиография", appendices: "Приложения", other: "Другой фрагмент" };
