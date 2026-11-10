import { useEffect, useState } from "react";
import { api } from "../api";
import { useLanguage, type UiLanguage } from "../i18n";
import type { LiteratureJob, ModelInfo, ReproducibilityJob, RepositoryQualityJob } from "../types";
import { ATTENTION_STATUSES, literatureResultCounts } from "./LiteratureReport";

export type NirCheckId = "literature" | "repository-quality" | "reproducibility";
type CheckJob = LiteratureJob | RepositoryQualityJob | ReproducibilityJob;
const STORAGE = "osaNirLinkedChecksV1";

function readSelection(id: NirCheckId): string {
  try { return (JSON.parse(localStorage.getItem(STORAGE) || "{}") as Record<string, string>)[id] || ""; }
  catch { return ""; }
}
function storeSelection(id: NirCheckId, jobId: string) {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE) || "{}") as Record<string, string>;
    saved[id] = jobId;
    localStorage.setItem(STORAGE, JSON.stringify(saved));
  } catch { /* Report selection still works for this session. */ }
}
function asRecord(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function str(value: unknown): string { return typeof value === "string" ? value : ""; }
function num(value: unknown): number | null { return typeof value === "number" && Number.isFinite(value) ? value : null; }
function short(value: string): string { return value.length > 180 ? `${value.slice(0, 179)}…` : value; }
function repoKey(value: string): string { return value.trim().replace(/\/+$/, "").replace(/\.git$/i, "").toLowerCase(); }
function jobStatus(status: string, language: UiLanguage): string {
  if (["queued", "running", "cancelling"].includes(status)) return language === "ru" ? "Выполняется" : "Running";
  if (["done", "completed"].includes(status)) return language === "ru" ? "Готово" : "Done";
  if (status === "failed") return language === "ru" ? "Ошибка" : "Failed";
  return language === "ru" ? "Остановлено" : "Cancelled";
}

export function NirCheckPanel({ id, repository, file, models, onOpenCheck }: {
  id: NirCheckId;
  repository: string;
  file: File | null;
  models: ModelInfo[];
  onOpenCheck: (page: NirCheckId, jobId?: string, repository?: string) => void;
}) {
  const { language } = useLanguage();
  const [jobs, setJobs] = useState<CheckJob[]>([]);
  const [selectedId, setSelectedId] = useState(() => readSelection(id));
  const [rawResult, setRawResult] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selected = jobs.find((job) => job.id === selectedId);
  const matching = id === "literature" || !repository.trim() || !selected || repoKey((selected as RepositoryQualityJob | ReproducibilityJob).repository) === repoKey(repository);
  const completed = selected?.status === "done" || selected?.status === "completed";
  const active = Boolean(selected && matching && ["queued", "running", "cancelling"].includes(selected.status));
  const savedLiteratureModel = (() => { try { return localStorage.getItem("osaLiteratureModel") || ""; } catch { return ""; } })();
  const literatureModel = models.find((item) => item.id === savedLiteratureModel && item.tier === "production")?.id
    ?? models.filter((item) => item.tier === "production").find((item) => item.recommended)?.id
    ?? models.find((item) => item.tier === "production")?.id ?? models[0]?.id ?? "";
  const disabled = busy || active || (id !== "repository-quality" && !file) || (id !== "literature" && !repository.trim()) || (id === "literature" && !literatureModel);
  const label = id === "literature" ? (language === "ru" ? "Проверка источников в PDF" : "PDF reference check")
    : id === "reproducibility" ? (language === "ru" ? "Сверка отчёта и кода" : "Report and code comparison")
    : (language === "ru" ? "Качество репозитория" : "Repository quality");

  useEffect(() => {
    let disposed = false;
    const load = async () => {
      try {
        const next = id === "literature" ? await api.literatureJobs()
          : id === "repository-quality" ? await api.repositoryQualityJobs() : await api.reproducibilityJobs();
        if (!disposed) setJobs(next);
      } catch (reason) { if (!disposed) setError((reason as Error).message); }
    };
    void load();
    const timer = window.setInterval(() => void load(), 4500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [id]);

  useEffect(() => {
    let disposed = false;
    setRawResult(null);
    if (completed && matching && selected && id !== "literature") {
      const request = id === "repository-quality" ? api.repositoryQualityResult(selected.id) : api.reproducibilityResult(selected.id);
      void request.then((result) => { if (!disposed) setRawResult(result); })
        .catch((reason) => { if (!disposed) setError((reason as Error).message); });
    }
    return () => { disposed = true; };
  }, [id, selected?.id, selected?.status, matching]);

  function select(jobId: string) { setSelectedId(jobId); storeSelection(id, jobId); setError(""); }

  async function run() {
    if (disabled) return;
    setBusy(true); setError("");
    try {
      const repo = repository.trim();
      if (id === "literature") {
        const body = new FormData(); body.append("files", file!); body.append("model", literatureModel);
        const created = await api.createLiteratureJobs(body);
        if (!created[0]) throw new Error(language === "ru" ? "Проверка не была создана." : "The check was not created.");
        setJobs((current) => [created[0], ...current]); select(created[0].id);
      } else if (id === "repository-quality") {
        const preflight = await api.repositoryQualityPreflight(repo);
        if (!preflight.ok) throw new Error(preflight.checks.filter((check) => check.blocking && !check.ok).map((check) => check.label).join(", ") || (language === "ru" ? "Сервис не готов." : "The service is not ready."));
        const body = new FormData(); body.append("repository", repo);
        const created = await api.createRepositoryQualityJob(body);
        setJobs((current) => [created, ...current]); select(created.id);
      } else {
        const preflight = await api.reproducibilityPreflight(repo);
        if (!preflight.ok) throw new Error(preflight.checks.filter((check) => check.blocking && !check.ok).map((check) => check.label).join(", ") || (language === "ru" ? "Сервис не готов." : "The service is not ready."));
        const body = new FormData(); body.append("repository", repo); body.append("file", file!);
        const created = await api.createReproducibilityJob(body);
        setJobs((current) => [created, ...current]); select(created.id);
      }
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  const runLabel = language === "ru" ? "Запустить проверку" : "Run check";
  return <div className="nir-check" id={`nir-check-${id}`}>
    <div className="nir-check-heading"><strong>{label}</strong><span>{selected ? jobStatus(selected.status, language) : (language === "ru" ? "Не запускалась" : "Not run")}</span></div>
    {selected && !matching && <p className="nir-check-warning">{language === "ru" ? "Выбранный отчёт относится к другому репозиторию. Запустите новую проверку или выберите подходящий отчёт." : "The selected report belongs to another repository. Run a new check or select a matching report."}</p>}
    {selected && matching && <div className="nir-check-summary"><p className="nir-check-job">{id === "literature" ? (selected as LiteratureJob).originalName : (selected as RepositoryQualityJob | ReproducibilityJob).repository}</p>
      {selected.status === "failed" ? <p>{selected.error || (language === "ru" ? "Проверка завершилась с ошибкой." : "The check failed.")}</p>
        : completed ? <ResultSummary id={id} selected={selected} raw={rawResult} language={language} />
          : <p>{language === "ru" ? "Результат появится после завершения." : "The result will appear when the check finishes."} {Math.round(selected.progress || 0)}%</p>}
    </div>}
    <div className="nir-check-actions"><button className="button primary" type="button" disabled={disabled} onClick={() => void run()}>{busy ? (language === "ru" ? "Запускается…" : "Starting…") : active ? (language === "ru" ? "Проверка выполняется" : "Check running") : runLabel}</button>
      {selected && matching && <button className="text-button" type="button" onClick={() => onOpenCheck(id, selected.id, repository)}>{completed ? (language === "ru" ? "Открыть полный отчёт →" : "Open full report →") : (language === "ru" ? "Открыть проверку →" : "Open check →")}</button>}
    </div>
    {id === "literature" && literatureModel && <small className="nir-check-model">{language === "ru" ? "Модель проверки источников:" : "Reference check model:"} {literatureModel}</small>}
    {jobs.length > 0 && <label className="nir-report-select"><span>{language === "ru" ? "Привязать готовый отчёт" : "Link an existing report"}</span><select value={selectedId} onChange={(event) => select(event.target.value)}><option value="">{language === "ru" ? "Отчёт не выбран" : "No report selected"}</option>{jobs.map((job) => <option key={job.id} value={job.id}>{id === "literature" ? (job as LiteratureJob).originalName : (job as RepositoryQualityJob | ReproducibilityJob).repository} · {new Date(job.createdAt).toLocaleDateString(language === "ru" ? "ru-RU" : "en-US")} · {jobStatus(job.status, language)}</option>)}</select></label>}
    {error && <p className="nir-check-error" role="alert">{error}</p>}
  </div>;
}

function ResultSummary({ id, selected, raw, language }: { id: NirCheckId; selected: CheckJob; raw: unknown; language: UiLanguage }) {
  if (id === "literature") {
    const result = (selected as LiteratureJob).result;
    if (!result) return <p>{language === "ru" ? "Результат загружается." : "Loading result."}</p>;
    const counts = literatureResultCounts(result);
    const attention = result.rows.filter((row) => ATTENTION_STATUSES.has(row.status)).slice(0, 3);
    return <><p><strong>{result.reference_count}</strong> {language === "ru" ? "источников; требуют внимания:" : "references; need attention:"} <strong>{counts.attention}</strong>.</p>
      {attention.length > 0 && <ul>{attention.map((row, index) => <li key={`${row.number}-${index}`}>{short(row.original_citation || row.notes)}</li>)}</ul>}</>;
  }
  if (!raw) return <p>{language === "ru" ? "Загружается результат…" : "Loading result…"}</p>;
  if (id === "repository-quality") {
    const root = asRecord(raw);
    const report = Object.keys(asRecord(root.result)).length ? asRecord(root.result) : root;
    const summary = asRecord(report.summary);
    const breakdown = asRecord(summary.score_breakdown);
    const labels: Record<string, [string, string]> = {
      readme: ["README", "README"], execution_files: ["точка запуска", "entry point"], requirements: ["зависимости", "dependencies"], tests: ["тесты", "tests"],
      data_files: ["данные", "data"], experiment_scripts: ["сценарии эксперимента", "experiment scripts"], license: ["лицензия", "license"],
    };
    const failed = Object.entries(breakdown).filter(([, value]) => asRecord(value).applicable !== false && asRecord(value).passed === false);
    const uncertain = Object.entries(breakdown).filter(([, value]) => asRecord(value).applicable !== false && typeof asRecord(value).passed !== "boolean");
    return <><p>{language === "ru" ? "Оценка репозитория:" : "Repository score:"} <strong>{num(summary.score) ?? "—"}/100</strong>. <small>{language === "ru" ? "Это не оценка всего раздела «Вывод и артефакты»." : "This is not a score for the entire conclusion and artifacts stage."}</small></p>
      <p>{failed.length ? (language === "ru" ? "Требуют внимания:" : "Need attention:") : (language === "ru" ? "Явных нарушений в оценённых критериях нет." : "No failed scored criteria.")} {failed.slice(0, 5).map(([key]) => labels[key]?.[language === "ru" ? 0 : 1] ?? key).join(", ")}</p>
      {uncertain.length > 0 && <small>{language === "ru" ? "Есть критерии без однозначного результата; откройте полный отчёт." : "Some criteria have no clear result; open the full report."}</small>}
      <small>{language === "ru" ? "В том числе проверяются наличие данных и сценариев эксперимента; качество протокола эта проверка не оценивает." : "This includes data and experiment scripts; protocol quality is not assessed."}</small></>;
  }
  const root = asRecord(raw);
  const verification = asRecord(root.claim_verification);
  const stats = asRecord(verification.stats);
  const claims = Array.isArray(verification.claims) ? verification.claims.map(asRecord) : [];
  const rejected = claims.filter((claim) => claim.contradiction === true || asRecord(claim.implementation).implemented === false).slice(0, 3);
  const scored = num(stats.scored_total) ?? claims.length;
  const implemented = num(stats.implemented) ?? claims.filter((claim) => asRecord(claim.implementation).implemented === true).length;
  return <><p>{language === "ru" ? "Технических утверждений с подтверждением в коде:" : "Technical claims supported by code:"} <strong>{implemented} / {scored}</strong>.</p>
    {rejected.length > 0 && <ul>{rejected.map((claim, index) => <li key={`${str(claim.claim_id)}-${index}`}>{short(str(claim.claim) || str(claim.original_text))}</li>)}</ul>}
    <small>{language === "ru" ? "Отсутствие подтверждения в коде не доказывает ошибку в научном выводе." : "Lack of code evidence does not prove a scientific conclusion is wrong."}</small></>;
}
