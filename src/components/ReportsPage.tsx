import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { useLanguage, type UiLanguage } from "../i18n";
import { uiLabels } from "../labels";
import type { DocumentElementType, DocumentMap, DocumentMapElement, Job, Rule, RuleResult, ProviderDiagnostic, RuleStatus, StructureBlock, StructureDetails } from "../types";

type StatusFilter = RuleStatus | "all";
interface Props {
  jobs: Job[];
  onAction: (id: string, action: "cancel" | "retry" | "retryFailed" | "restart" | "delete") => Promise<void>;
  onChanged: () => Promise<void>;
}
const STATUS_ORDER: RuleStatus[] = ["violation", "pass", "uncertain", "not_checked", "not_applicable"];
const ACTIVE = ["queued", "extracting", "mapping", "queued_check", "checking"];

export function ReportsPage({ jobs, onAction, onChanged }: Props) {
  const { language } = useLanguage();
  const t = reportCopy(language);
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
      <div className="row between"><h2>{t.jobs}</h2><span>{jobs.length}</span></div>
      <div className="jobs-list">
        {jobs.map((job) => <button key={job.id} className={`job-row ${selectedId === job.id ? "selected" : ""}`} onClick={() => setSelectedId(job.id)}>
          <strong>{job.originalName}</strong><span>{jobProgressSummary(job, language)}</span>
        </button>)}
        {!jobs.length && <div className="empty small">{t.noJobs}</div>}
      </div>
    </aside>
    <div className="report-area">
      {!selected && <div className="empty panel">{t.selectJob}</div>}
      {selected?.status === "awaiting_review" && <StructureReview job={selected} onChanged={onChanged} onAction={onAction} language={language} />}
      {selected && selected.status !== "awaiting_review" && !selected.report && <JobState job={selected} onAction={onAction} language={language} />}
      {selected?.report && <Report job={selected} status={status} query={query} onStatus={setStatus} onQuery={setQuery} onAction={onAction} language={language} />}
    </div>
  </section>;
}

function JobState({ job, onAction, language }: { job: Job; onAction: Props["onAction"]; language: UiLanguage }) {
  const t = reportCopy(language);
  const labels = uiLabels(language);
  const active = ACTIVE.includes(job.status);
  return <div className="panel job-state">
    <h1>{job.originalName}</h1><p><b>{labels.jobStatus[job.status]}</b>{active && !["queued", "queued_check"].includes(job.status) ? ` · ${job.progress} %` : ""}</p>{job.progressMessage && <p className="progress-message">{job.progressMessage}</p>}
    {active && <progress max="100" value={job.progress} />}
    {job.status === "mapping" && <p className="hint">{t.mappingHint}{job.developerMode ? ` ${t.mappingDeveloper}` : ` ${t.mappingManual}`}</p>}
    {job.error && <div className="inline-error">{job.error}</div>}
    {job.diagnostics?.length ? <ApiDiagnostics title={t.llmDiagnostics} items={job.diagnostics} language={language} /> : null}
    <div className="actions">
      {active && <button className="button secondary" onClick={() => void onAction(job.id, "cancel")}>{t.cancel}</button>}
      {job.status === "failed" && <button className="button secondary" onClick={() => void onAction(job.id, "retry")}>{t.retryCurrent}</button>}
      {!active && <button className="button primary" onClick={() => void onAction(job.id, "restart")}>{t.restart}</button>}
      {!active && <button className="button danger" onClick={() => void onAction(job.id, "delete")}>{t.delete}</button>}
    </div>
  </div>;
}

function StructureReview({ job, onChanged, onAction, language }: { job: Job; onChanged: Props["onChanged"]; onAction: Props["onAction"]; language: UiLanguage }) {
  const t = reportCopy(language);
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

  if (loading && !details) return <div className="panel job-state"><h1>{job.originalName}</h1><p>{t.loadingStructure}</p></div>;
  if (!details) return <div className="panel job-state"><h1>{job.originalName}</h1><div className="inline-error">{error}</div></div>;
  const map = details.map;
  return <div className="structure-review">
    <div className="page-title report-title">
      <div><h1>{t.reviewStructure}</h1><p>{job.originalName} · {t.rulesNotStarted}</p></div>
      <div className="score-box"><strong>{map.elements.length}</strong><span>{t.semanticFragments}</span></div>
    </div>

    <div className="panel review-intro">
      <div><strong>{t.whatToCheck}</strong><p>{t.structureHint}</p></div>
      <div className="actions"><button className="button secondary" disabled={saving} onClick={() => void mutate(() => api.addMapElement(job.id, {}))}>{t.addFragment}</button><button className="button primary" disabled={saving || !map.elements.length} onClick={() => void mutate(() => api.confirmStructure(job.id))}>{t.confirmStart}</button></div>
    </div>

    {error && <div className="inline-error">{error}</div>}
    <div className="map-stats panel">
      <span>{t.structureRequests}: <b>{map.usage.requests}</b></span>
      <span>{t.blocks}: <b>{map.extraction.processedBlocks}/{map.extraction.totalBlocks}</b></span>
      <span>{t.ambiguous}: <b>{map.elements.filter((item) => item.state === "ambiguous").length}</b></span>
      <span>{t.retries}: <b>{map.usage.retries}</b></span>
    </div>
    {map.issues.length > 0 && <div className="map-issues panel">{map.issues.map((issue, index) => <div key={`${issue.code}-${index}`} className={issue.severity}><b>{issue.code}</b><span>{issue.message}</span></div>)}</div>}
    {map.usage.diagnostics.length > 0 && <ApiDiagnostics title={t.mapDiagnostics} items={map.usage.diagnostics} language={language} />}

    <div className="review-sections">
      {map.elements.map((element) => <StructureCard key={element.id} jobId={job.id} element={element} blocks={details.blocks} disabled={saving} onMutate={mutate} language={language} />)}
    </div>
    <div className="actions between review-footer"><div className="actions"><button className="button secondary" onClick={() => void onAction(job.id, "restart")}>{t.restart}</button><button className="button danger" onClick={() => void onAction(job.id, "delete")}>{t.deleteJob}</button></div><button className="button primary" disabled={saving || !map.elements.length} onClick={() => void mutate(() => api.confirmStructure(job.id))}>{t.confirmStructure}</button></div>
  </div>;
}

function StructureCard({ jobId, element, blocks, disabled, onMutate, language }: { jobId: string; element: DocumentMapElement; blocks: StructureBlock[]; disabled: boolean; onMutate: (action: () => Promise<unknown>) => Promise<void>; language: UiLanguage }) {
  const t = reportCopy(language);
  const elementTypeLabel = elementTypeLabels(language);
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
    <summary><span className="structure-type">{elementTypeLabel[element.type]}</span><strong>{element.label}</strong><small>{element.pages.length ? `${t.pagesShort} ${element.pages[0]}–${element.pages.at(-1)}` : t.pagesUnknown}</small></summary>
    <div className="structure-card-body">
      <div className="structure-fields">
        <label><span>{t.type}</span><select value={type} onChange={(event) => setType(event.target.value as DocumentElementType)}>{Object.entries(elementTypeLabel).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
        <label><span>{t.name}</span><input value={label} onChange={(event) => setLabel(event.target.value)} /></label>
        <label><span>{t.firstBlock}</span><select value={startBlockId} onChange={(event) => setStart(event.target.value)}>{blocks.map((block) => <option key={block.id} value={block.id}>{blockOption(block, language)}</option>)}</select></label>
        <label><span>{t.lastBlock}</span><select value={endBlockId} onChange={(event) => setEnd(event.target.value)}>{blocks.map((block) => <option key={block.id} value={block.id}>{blockOption(block, language)}</option>)}</select></label>
        <label className="full-width"><span>{t.anchorQuote}</span><textarea rows={3} value={quote} onChange={(event) => setQuote(event.target.value)} placeholder={t.anchorPlaceholder} /></label>
      </div>
      <div className="fragment-preview"><b>{t.rangeStart}</b>{preview.map((block) => <blockquote key={block.id}><span>{block.id}{block.page ? ` · ${t.pagesShort} ${block.page}` : ""}</span>{block.text}</blockquote>)}</div>
      <p className="hint">{t.range}: {element.startBlockId} → {element.endBlockId} · {t.source}: {element.source === "user" ? t.changedByUser : "LLM"} · {t.status}: {element.state === "confirmed" ? t.confirmed : t.ambiguousState}</p>
      {element.note && <p className="hint">{element.note}</p>}
      <div className="actions between">
        <button className="button danger" disabled={disabled} onClick={() => void onMutate(() => api.deleteMapElement(jobId, element.id))}>{t.delete}</button>
        <div className="actions"><button className="button secondary" disabled={disabled || element.state === "ambiguous"} onClick={() => void onMutate(() => api.updateMapElement(jobId, element.id, { state: "ambiguous" }))}>{t.markAmbiguous}</button><button className="button primary" disabled={disabled || !changed} onClick={() => void onMutate(() => api.updateMapElement(jobId, element.id, { type, label, startBlockId, endBlockId, quote, state: "confirmed" }))}>{t.saveBounds}</button></div>
      </div>
    </div>
  </details>;
}

function Report({ job, status, query, onStatus, onQuery, onAction, language }: { job: Job; status: StatusFilter; query: string; onStatus: (value: StatusFilter) => void; onQuery: (value: string) => void; onAction: Props["onAction"]; language: UiLanguage }) {
  const t = reportCopy(language);
  const labels = uiLabels(language);
  const report = job.report!;
  const retryableCount = report.ruleResults.filter((item) => (item.status === "not_checked" && item.checkedBy.startsWith("llm")) || (item.status === "uncertain" && item.checkedBy.startsWith("llm") && (item.evidenceStatus === "rejected" || Boolean(item.coverage && item.coverage.checkedCandidateCount < item.coverage.candidateCount)))).length;
  const catalog = useMemo(() => new Map(report.ruleCatalog.map((rule) => [rule.id, rule])), [report]);
  const results = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase(language);
    return report.ruleResults.filter((result) => {
      if (status !== "all" && result.status !== status) return false;
      if (!normalized) return true;
      const rule = catalog.get(result.ruleId);
      return `${result.ruleId} ${rule?.title || ""} ${rule?.requirement || ""} ${result.explanation}`.toLocaleLowerCase(language).includes(normalized);
    });
  }, [report, catalog, status, query]);
  const reportHealth = report.reportHealth;
  return <div>
    <div className="page-title report-title"><div><h1>{job.originalName}</h1><p>{report.summary}</p></div><div className="score-box"><strong>{report.score === null ? "—" : report.score}</strong><span>{report.score === null ? t.scoreUnavailable : `/ 100 ${t.scoreSuffix}${report.scoreIsProvisional ? ` · ${t.provisional}` : ""}`}</span></div></div>
    {reportHealth && <div className={`report-health ${reportHealth.status}`}><strong>{reportHealth.label}</strong>{reportHealth.reasons.length > 0 && <span>{reportHealth.reasons.join(" ")}</span>}</div>}
    {job.error && <div className="inline-error">{t.lastAttemptFailed}: {job.error}</div>}
    {report.documentMap && <ReadOnlyMap map={report.documentMap} language={language} />}
    <div className="status-grid">{STATUS_ORDER.map((item) => <button key={item} className={`status-card ${item} ${status === item ? "active" : ""}`} onClick={() => onStatus(item)}><strong>{countForStatus(report, item)}</strong><span>{labels.status[item]}</span></button>)}</div>
    <div className="panel report-controls"><button className={status === "all" ? "text-button active" : "text-button"} onClick={() => onStatus("all")}>{t.allRules} ({report.ruleResults.length})</button><input value={query} onChange={(event) => onQuery(event.target.value)} placeholder={t.searchRules} /><span>{t.rulesCoverage}: {Math.round(report.coverage * 100)} % · {t.candidates}: {Math.round((report.automaticCandidateCoverage ?? report.candidateCoverage ?? 1) * 100)} % · {t.termMap}: {Math.round((report.abbreviationCoverage ?? 1) * 100)} % · {t.termRules}: {Math.round((report.abbreviationRuleCoverage ?? 1) * 100)} %</span></div>
    <div className="result-list">{results.map((result) => <RuleResultCard key={result.ruleId} result={result} rule={catalog.get(result.ruleId)} language={language} />)}{!results.length && <div className="empty panel">{t.noRules}</div>}</div>
    <details className="panel technical-details"><summary>{t.technical}</summary><div className="technical-body">
      <p><b>{t.model}:</b> {report.technical.model}</p><p><b>{t.version}:</b> {report.technical.appVersion}</p><p><b>{t.promptHash}:</b> {report.technical.promptHash}</p><p><b>{t.profile}:</b> {job.profile === "core" ? t.core : t.full}</p>
      <p><b>{t.routing}:</b> {report.routing.strategy}; {t.explicitRules}: {report.routing.explicitRules}; fallback: {report.routing.fallbackRules}; {t.fragments}: {report.routing.fragments}; {t.physicalRequests}: {report.routing.physicalRequests ?? report.routing.checkRequests}; {t.firstPassPlan}: {report.routing.plannedCheckRequests ?? report.routing.checkRequests}; candidate: {report.routing.candidateRequests ?? 0}; semantic first-pass: {report.routing.semanticRequests ?? 0}; {t.abbreviations}: Python; evidence verifier: {report.routing.evidenceVerifierRequests ?? 0}</p>
      <p><b>{t.llmRequests}:</b> {report.llmUsage.requests}; {t.retriesLower}: {report.llmUsage.retries}; {t.limiterWait}: {Math.round(report.llmUsage.rateLimitWaitMs / 1000)} s</p>
      {report.llmUsage.traces?.length > 0 && <div><b>{t.successfulCalls}:</b><ul>{report.llmUsage.traces.map((item, index) => <li key={`${item.at}-${index}`}>{item.operation}: {item.provider}/{item.model}; upstream: {item.providerName || "—"}; compatibility: {item.compatibilityMode ? t.yes : t.no}; request: {item.requestId || "—"}</li>)}</ul></div>}{report.llmUsage.diagnostics.length > 0 && <ApiDiagnostics title={t.providerDiagnostics} items={report.llmUsage.diagnostics} language={language} />}
      <label><span>{t.rulePrompt}</span><textarea readOnly value={job.prompt} rows={12} /></label><label><span>{t.mapPrompt}</span><textarea readOnly value={job.mapPrompt} rows={12} /></label>
      {report.warnings.length > 0 && <div><b>{t.warnings}:</b><ul>{report.warnings.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>}
    </div></details>
    <div className="actions end report-actions"><a className="button primary" href={api.reportPdfUrl(job.id)}>{t.userReport}</a><a className="button secondary" href={api.developerReportPdfUrl(job.id)}>{t.developerReport}</a><a className="button secondary" href={api.reportJsonUrl(job.id)}>JSON</a><a className="button secondary" href={api.reportMarkdownUrl(job.id)}>Markdown</a>{retryableCount > 0 && <button className="button secondary" onClick={() => void onAction(job.id, "retryFailed")}>{t.retryFailed} ({retryableCount})</button>}<button className="button secondary" onClick={() => void onAction(job.id, "retry")}>{t.retryCurrent}</button><button className="button primary" onClick={() => void onAction(job.id, "restart")}>{t.restart}</button><button className="button danger" onClick={() => void onAction(job.id, "delete")}>{t.delete}</button></div>
  </div>;
}

function ReadOnlyMap({ map, language }: { map: DocumentMap; language: UiLanguage }) {
  const t = reportCopy(language);
  const elementTypeLabel = elementTypeLabels(language);
  return <details className="panel map-panel"><summary><span><b>{t.confirmedStructure}</b></span><span>{map.elements.length} {t.ranges} · {t.oneLlmRequest}</span></summary><div className="map-body"><div className="map-elements">{map.elements.map((element) => <div className="map-element" key={element.id}><div className="readonly-map-row"><span>{elementTypeLabel[element.type]}</span><strong>{element.label}{element.canonicalRole === "secondary_copy" ? ` · ${t.secondaryCopy}` : ""}</strong><small>{element.startBlockId} → {element.endBlockId}</small></div></div>)}</div></div></details>;
}

function ApiDiagnostics({ title, items, language }: { title: string; items: ProviderDiagnostic[]; language: UiLanguage }) {
  const t = reportCopy(language);
  return <details className="api-diagnostics" open><summary>{title} ({items.length})</summary><div>{items.map((item, index) => <div className="diagnostic-row" key={`${item.at}-${index}`}><b>{item.operation} · {t.attemptLower} {item.attempt} · HTTP {item.httpStatus || "—"}</b><span>{item.message}</span><small>{item.provider ? `${item.provider}${item.model ? ` · ${item.model}` : ""}; ` : ""} {t.retryLabel}: {item.retryable ? t.yes : t.no}; Retry-After: {item.retryAfterMs} ms; backoff: {item.backoffMs} ms{item.networkCode ? `; network: ${item.networkCode}` : ""}{item.providerCode ? `; code: ${item.providerCode}` : ""}{item.providerName ? `; upstream: ${item.providerName}` : ""}{item.requestId ? `; request: ${item.requestId}` : ""}{item.quotaMetric ? `; quota: ${item.quotaMetric}` : ""}{item.quotaDescription ? `; ${item.quotaDescription}` : ""}</small></div>)}</div></details>;
}

function RuleResultCard({ result, rule, language }: { result: RuleResult; rule?: Rule; language: UiLanguage }) {
  const t = reportCopy(language);
  const labels = uiLabels(language);
  const [showAdvice, setShowAdvice] = useState(false);
  const title = rule?.userTitle || rule?.title || result.explanation;
  const advice = rule?.relatedAdvice;
  return <details className={`result-card ${result.status}`} open={result.status === "violation" && (result.severity === "critical" || result.severity === "major")}><summary><span className={`status-badge ${result.status}`}>{labels.status[result.status]}</span><span className="rule-title">{title}</span><span className="rule-id">{result.ruleId}</span></summary><div className="result-body">
    {rule && <><p className="requirement"><b>{t.rule}.</b> {rule.requirement}</p><div className="meta-grid"><span><b>{t.category}:</b> {rule.category}</span><span><b>{t.ruleType}:</b> {labels.mode[rule.mode]}</span><span><b>{t.source}:</b> {rule.sourceLabel}, {t.line} {rule.sourceLine}</span><span><b>{t.method}:</b> {result.checkedBy === "detector" ? t.codeStructure : result.checkedBy.includes("llm-candidate") ? t.candidatesLlm : result.checkedBy.startsWith("llm") ? t.llmEvidence : t.system}</span></div></>}
    <div className="explanation"><b>{result.status === "violation" ? t.violation : t.checkResult}</b><p>{result.explanation}</p></div>
    {result.coverage && <p className="coverage-line">{result.coverage.domain === "abbreviation_candidates" ? `${t.classifiedTerms}: ${result.coverage.checkedCandidateCount} ${t.of} ${result.coverage.candidateCount}; ${t.fullCoverage}: ${result.coverage.exhaustive ? t.yes : t.no}.` : `${t.checkedFragmentsCount}: ${result.coverage.checkedCandidateCount} ${t.of} ${result.coverage.candidateCount}; ${t.fullScope}: ${result.coverage.exhaustive ? t.yes : t.no}.`}</p>}
    {result.checkedFragments?.length ? <p className="hint">{t.fragmentsLabel}: {result.checkedFragments.join(", ")}</p> : null}{result.relatedRuleIds?.length ? <p className="hint">{t.relatedRules}: {result.relatedRuleIds.join(", ")}</p> : null}{result.consistencyNotes?.length ? <p className="hint">{t.consistency}: {result.consistencyNotes.join(" ")}</p> : null}{result.evidenceStatus === "rejected" ? <p className="inline-error">{t.evidenceRejected}</p> : null}{result.evidenceStatus === "coverage_verified" ? <p className="hint">{t.coverageVerified}</p> : null}
    {result.termFindings?.length ? <div className="evidence"><b>{t.termReview}</b><div className="matrix-table"><table><thead><tr><th>{t.term}</th><th>{t.type}</th><th>{t.status}</th></tr></thead><tbody>{result.termFindings.map((item) => <tr key={`${result.ruleId}-${item.term}`}><td>{item.term}</td><td>{item.kind}</td><td>{item.status}</td></tr>)}</tbody></table></div></div> : null}
    {result.coverageMatrix?.length ? <div className="evidence"><b>{t.fullFragmentCheck}</b><div className="matrix-table"><table><thead><tr><th>{t.fragment}</th><th>{t.blocks}</th><th>{t.completeness}</th><th>{t.items}</th></tr></thead><tbody>{result.coverageMatrix.map((row) => <tr key={`${result.ruleId}-${row.fragmentId}`}><td>{row.label}</td><td>{row.checkedBlocks}/{row.totalBlocks}</td><td>{row.complete ? t.complete : t.incomplete}</td><td>{row.items.map((item) => `${item.name}: ${item.status === "found" ? t.found : item.status === "not_found" ? t.notFound : t.ambiguousState}`).join("; ")}</td></tr>)}</tbody></table></div></div> : null}
    {result.evidence.length > 0 && <div className="evidence"><b>{t.confirmedEvidence}</b>{result.evidence.map((item, index) => <blockquote key={`${item.blockId}-${index}`}><span>{item.token ? `${t.designation}: ${item.token}${item.entityKind ? ` · ${t.typeLower}: ${item.entityKind}` : ""} · ` : ""}{item.location}{item.page ? ` · ${t.pagesShort} ${item.page}` : ""}{typeof item.start === "number" && typeof item.end === "number" ? ` · ${t.characters} ${item.start}–${item.end}` : ""} · {t.quoteVerified}</span><div className="evidence-quote">{item.quote}</div>{item.context && item.context !== item.quote ? <small className="candidate-context">{t.context}: {item.context}</small> : null}</blockquote>)}</div>}
    {result.fix && <div className="fix"><b>{t.howToFix}</b><p>{result.fix}</p></div>}
    {advice && ["violation", "uncertain"].includes(result.status) && <div className="advice-actions"><button type="button" className="button secondary" onClick={() => setShowAdvice((value) => !value)}>{showAdvice ? t.hideAdvice : t.relatedAdvice}</button>{showAdvice && <div className="related-advice"><strong>{advice.title}</strong><p>{advice.summary}</p><a href={advice.url} target="_blank" rel="noreferrer">{t.openSource} · {t.pagesShort} {advice.page}</a></div>}</div>}
  </div></details>;
}

function jobProgressSummary(job: Job, language: UiLanguage) {
  const label = uiLabels(language).jobStatus[job.status];
  if (["queued", "queued_check"].includes(job.status)) return job.progressMessage || label;
  if (ACTIVE.includes(job.status)) return `${label} · ${job.progress} %${job.progressMessage ? ` · ${job.progressMessage}` : ""}`;
  return label;
}

function blockOption(block: StructureBlock, language: UiLanguage) {
  const t = reportCopy(language);
  const text = block.text.replace(/\s+/g, " ").slice(0, 90);
  return `${block.id}${block.page ? ` · ${t.pagesShort} ${block.page}` : ""} · ${text}`;
}
function countForStatus(report: NonNullable<Job["report"]>, status: RuleStatus) { return report.ruleResults.filter((item) => item.status === status).length; }
function elementTypeLabels(language: UiLanguage): Record<DocumentElementType, string> {
  return language === "ru"
    ? { title: "Название", abstract: "Аннотация", introduction: "Введение", goal: "Цель", tasks: "Задачи", defense_statements: "Положения на защиту", chapter: "Глава", chapter_conclusions: "Выводы по главе", conclusion: "Заключение", bibliography: "Библиография", appendices: "Приложения", other: "Другой фрагмент" }
    : { title: "Title", abstract: "Abstract", introduction: "Introduction", goal: "Goal", tasks: "Tasks", defense_statements: "Defense statements", chapter: "Chapter", chapter_conclusions: "Chapter conclusions", conclusion: "Conclusion", bibliography: "Bibliography", appendices: "Appendices", other: "Other fragment" };
}

function reportCopy(language: UiLanguage) {
  return language === "ru" ? {
    jobs: "Задачи", noJobs: "Задач пока нет.", selectJob: "Выберите задачу.", mappingHint: "Система выделяет смысловые диапазоны документа и проверяет их границы.", mappingDeveloper: "В режиме разработчика корректная карта будет принята автоматически.", mappingManual: "После этого структура будет показана для подтверждения.", llmDiagnostics: "Диагностика LLM", cancel: "Отменить", retryCurrent: "Повторить по текущей структуре", restart: "Начать заново", delete: "Удалить", loadingStructure: "Загрузка структуры…", reviewStructure: "Проверьте структуру", rulesNotStarted: "проверка правил ещё не началась", semanticFragments: "смысловых фрагментов", whatToCheck: "Что нужно проверить", structureHint: "Убедитесь, что правильно выделены введение, цель, задачи, положения, все главы и заключение. При необходимости измените границы, тип или удалите лишний диапазон.", addFragment: "Добавить фрагмент", confirmStart: "Подтвердить и начать проверку", structureRequests: "Попытки запроса структуры", blocks: "Блоки", ambiguous: "Неоднозначно", retries: "Повторы после ошибок", mapDiagnostics: "Диагностика LLM при построении структуры", deleteJob: "Удалить задачу", confirmStructure: "Подтвердить структуру и начать", pagesShort: "стр.", pagesUnknown: "страницы не определены", type: "Тип", name: "Название", firstBlock: "Первый блок", lastBlock: "Последний блок", anchorQuote: "Точная опорная цитата", anchorPlaceholder: "Дословный текст названия, цели или заголовка внутри диапазона", rangeStart: "Начало диапазона", range: "Диапазон", source: "Источник", changedByUser: "изменено пользователем", status: "Статус", confirmed: "подтверждён", ambiguousState: "неоднозначен", markAmbiguous: "Пометить неоднозначным", saveBounds: "Сохранить границы", scoreUnavailable: "оценка не рассчитана", scoreSuffix: "по проверенным правилам", provisional: "предварительно", lastAttemptFailed: "Последняя попытка завершилась ошибкой", allRules: "Все правила", searchRules: "Поиск по правилам отчёта", rulesCoverage: "Правила", candidates: "кандидаты", termMap: "карта обозначений", termRules: "правила по обозначениям", noRules: "В этой категории правил нет.", technical: "Промпты, маршрутизация и API", model: "Модель", version: "Версия", promptHash: "Хеш промпта", profile: "Профиль", core: "Ядро", full: "Полный набор", routing: "Маршрутизация", explicitRules: "явно задано правил", fragments: "фрагментов", physicalRequests: "физических запросов проверки", firstPassPlan: "план первого прохода", abbreviations: "сокращения", llmRequests: "LLM-запросы", retriesLower: "повторы", limiterWait: "ожидание limiter", successfulCalls: "Успешные обращения", yes: "да", no: "нет", providerDiagnostics: "Диагностика OpenRouter и провайдеров", rulePrompt: "Промпт проверки правил", mapPrompt: "Промпт структуры", warnings: "Предупреждения", userReport: "Отчёт для пользователя", developerReport: "Отчёт для разработчика", retryFailed: "Повторить неудачные LLM-проверки", confirmedStructure: "Подтверждённая структура", ranges: "диапазонов", oneLlmRequest: "один запрос LLM", secondaryCopy: "вторичная копия", attemptLower: "попытка", retryLabel: "Повтор", rule: "Правило", category: "Категория", ruleType: "Тип исходного правила", line: "строка", method: "Метод", codeStructure: "код / структура", candidatesLlm: "кандидаты + LLM", llmEvidence: "LLM + проверка доказательств", system: "система", violation: "Нарушение", checkResult: "Результат проверки", classifiedTerms: "Классифицировано обозначений", of: "из", fullCoverage: "полное покрытие", checkedFragmentsCount: "Проверено назначенных фрагментов", fullScope: "полная область", fragmentsLabel: "Фрагменты", relatedRules: "То же замечание связано с правилами", consistency: "Проверка согласованности", evidenceRejected: "Заявленное нарушение не подтверждено допустимой цитатой или полной проверкой назначенной области.", coverageVerified: "Отсутствие проверено по полной назначенной области.", termReview: "Разбор обозначений", term: "Термин", fullFragmentCheck: "Полная проверка назначенных фрагментов", fragment: "Фрагмент", completeness: "Полнота", items: "Элементы", complete: "полная", incomplete: "неполная", found: "найдено", notFound: "не найдено", confirmedEvidence: "Подтверждённые доказательства", designation: "Обозначение", typeLower: "тип", characters: "символы", quoteVerified: "цитата найдена в исходном блоке", context: "Контекст", howToFix: "Как исправить", hideAdvice: "Скрыть связанные советы", relatedAdvice: "Связанные советы Шалыто", openSource: "Открыть источник"
  } : {
    jobs: "Jobs", noJobs: "No jobs yet.", selectJob: "Select a job.", mappingHint: "The system extracts semantic document ranges and verifies their boundaries.", mappingDeveloper: "In developer mode, a valid map is accepted automatically.", mappingManual: "The structure will then be shown for confirmation.", llmDiagnostics: "LLM diagnostics", cancel: "Cancel", retryCurrent: "Retry with current structure", restart: "Start over", delete: "Delete", loadingStructure: "Loading structure…", reviewStructure: "Review document structure", rulesNotStarted: "rule checking has not started yet", semanticFragments: "semantic fragments", whatToCheck: "What to review", structureHint: "Check that the introduction, goal, tasks, defense statements, all chapters, and conclusion are identified correctly. Adjust boundaries or types and remove extra ranges if needed.", addFragment: "Add fragment", confirmStart: "Confirm and start checking", structureRequests: "Structure requests", blocks: "Blocks", ambiguous: "Ambiguous", retries: "Retries after errors", mapDiagnostics: "Document-map LLM diagnostics", deleteJob: "Delete job", confirmStructure: "Confirm structure and start", pagesShort: "p.", pagesUnknown: "pages unknown", type: "Type", name: "Name", firstBlock: "First block", lastBlock: "Last block", anchorQuote: "Exact anchor quote", anchorPlaceholder: "Exact text of the title, goal, or heading inside the range", rangeStart: "Range start", range: "Range", source: "Source", changedByUser: "changed by user", status: "Status", confirmed: "confirmed", ambiguousState: "ambiguous", markAmbiguous: "Mark ambiguous", saveBounds: "Save boundaries", scoreUnavailable: "score unavailable", scoreSuffix: "for checked rules", provisional: "provisional", lastAttemptFailed: "Last attempt failed", allRules: "All rules", searchRules: "Search report rules", rulesCoverage: "Rules", candidates: "candidates", termMap: "term map", termRules: "term rules", noRules: "No rules in this category.", technical: "Prompts, routing and API", model: "Model", version: "Version", promptHash: "Prompt hash", profile: "Profile", core: "Core", full: "Full set", routing: "Routing", explicitRules: "explicit rules", fragments: "fragments", physicalRequests: "physical check requests", firstPassPlan: "first-pass plan", abbreviations: "abbreviations", llmRequests: "LLM requests", retriesLower: "retries", limiterWait: "rate limiter wait", successfulCalls: "Successful calls", yes: "yes", no: "no", providerDiagnostics: "OpenRouter and provider diagnostics", rulePrompt: "Rule-check prompt", mapPrompt: "Document-map prompt", warnings: "Warnings", userReport: "User report", developerReport: "Developer report", retryFailed: "Retry failed LLM checks", confirmedStructure: "Confirmed structure", ranges: "ranges", oneLlmRequest: "one LLM request", secondaryCopy: "secondary copy", attemptLower: "attempt", retryLabel: "Retry", rule: "Rule", category: "Category", ruleType: "Source rule type", line: "line", method: "Method", codeStructure: "code / structure", candidatesLlm: "candidates + LLM", llmEvidence: "LLM + evidence verification", system: "system", violation: "Violation", checkResult: "Check result", classifiedTerms: "Classified terms", of: "of", fullCoverage: "full coverage", checkedFragmentsCount: "Checked assigned fragments", fullScope: "full scope", fragmentsLabel: "Fragments", relatedRules: "Related rules", consistency: "Consistency check", evidenceRejected: "The reported violation was not supported by an admissible quote or full coverage of the assigned scope.", coverageVerified: "Absence was verified across the full assigned scope.", termReview: "Term review", term: "Term", fullFragmentCheck: "Full check of assigned fragments", fragment: "Fragment", completeness: "Completeness", items: "Items", complete: "complete", incomplete: "incomplete", found: "found", notFound: "not found", confirmedEvidence: "Confirmed evidence", designation: "Term", typeLower: "type", characters: "characters", quoteVerified: "quote verified in source block", context: "Context", howToFix: "How to fix", hideAdvice: "Hide related advice", relatedAdvice: "Related Shalyto advice", openSource: "Open source"
  };
}
