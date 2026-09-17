import type { ReactNode } from "react";
import type {
  LiteratureResult,
  LiteratureRow,
  LiteratureSourceType,
  LiteratureStatus,
} from "../types";

export const ATTENTION_STATUSES = new Set<LiteratureStatus>([
  "OK_MINOR_MISMATCH",
  "METADATA_MISMATCH",
  "SUSPICIOUS",
  "LIKELY_HALLUCINATED",
  "UNVERIFIED",
  "ERROR",
]);

const STATUS_COPY: Record<LiteratureStatus, { label: string; tone: string }> = {
  OK: { label: "Подтверждено", tone: "ok" },
  OK_MINOR_MISMATCH: { label: "Небольшая неточность", tone: "minor" },
  METADATA_MISMATCH: { label: "Есть ошибка", tone: "mismatch" },
  SUSPICIOUS: { label: "Нужно проверить", tone: "review" },
  LIKELY_HALLUCINATED: { label: "Источник не подтверждён", tone: "danger" },
  UNVERIFIED: { label: "Не удалось подтвердить", tone: "review" },
  ERROR: { label: "Ошибка проверки", tone: "neutral" },
  NOT_A_PAPER: { label: "Другой тип источника", tone: "neutral" },
};

const SOURCE_TYPE_COPY: Record<LiteratureSourceType, string> = {
  PAPER: "Статья",
  PREPRINT: "Препринт",
  BOOK: "Книга",
  STANDARD: "Стандарт",
  REPORT: "Отчёт",
  DATASET: "Набор данных",
  DOCUMENTATION: "Документация",
  REPOSITORY: "Репозиторий",
  WEB: "Веб-источник",
  OTHER: "Другой источник",
  UNKNOWN: "Тип не определён",
};

interface Props {
  result: LiteratureResult;
  rows?: LiteratureRow[];
  actions?: ReactNode;
  controls?: ReactNode;
  exportMode?: boolean;
  emptyMessage?: string;
}

export function literatureResultCounts(result: LiteratureResult) {
  return {
    attention: result.rows.filter((row) => ATTENTION_STATUSES.has(row.status)).length,
    confirmed: result.rows.filter((row) => row.status === "OK").length,
  };
}

export function LiteratureReport({
  result,
  rows = result.rows,
  actions,
  controls,
  exportMode = false,
  emptyMessage = "В выбранной категории источников нет.",
}: Props) {
  const counts = literatureResultCounts(result);
  const incomplete = Boolean(result.web_stage && result.web_stage.failed > 0);

  return (
    <section className={`literature-report ${exportMode ? "literature-report--export" : ""}`}>
      <header className="literature-result-head">
        <div>
          <span className="literature-result-kicker">Отчёт по источникам</span>
          <h2>{result.filename}</h2>
        </div>
        {actions && <div className="literature-report-actions">{actions}</div>}
      </header>

      <div className="literature-summary-simple">
        <Summary value={result.reference_count} label="Источников" />
        <Summary value={counts.confirmed} label="Подтверждено" tone="ok" />
        <Summary value={counts.attention} label="Требуют внимания" tone="attention" />
      </div>

      {incomplete && (
        <div className="literature-soft-notice">
          Несколько источников не удалось перепроверить полностью. Они оставлены в разделе «Требуют внимания».
        </div>
      )}

      {result.warnings && result.warnings.length > 0 && (
        <div className="literature-report-warnings">
          <strong>Предупреждения проверки</strong>
          <ul>{result.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul>
        </div>
      )}

      {controls && <div className="literature-result-toolbar">{controls}</div>}

      {rows.length > 0 ? (
        <div className="literature-review-list">
          {rows.map((row) => <ReferenceCard key={`${row.number}-${row.original_citation}`} row={row} />)}
        </div>
      ) : (
        <div className="literature-empty-state">
          <strong>Здесь всё чисто</strong>
          <span>{emptyMessage}</span>
        </div>
      )}
    </section>
  );
}

function Summary({ value, label, tone = "" }: { value: number; label: string; tone?: string }) {
  return <div className={`literature-summary-card ${tone}`}><strong>{value}</strong><span>{label}</span></div>;
}

function ReferenceCard({ row }: { row: LiteratureRow }) {
  const status = STATUS_COPY[row.status];
  const evidenceUrls = [...new Set([row.evidence_url, ...(row.evidence_urls ?? [])].filter(Boolean))];
  const evidence = evidenceUrls[0] ?? "";
  const found = row.checker_found_citation && row.checker_found_citation !== "No confirmed source found";

  return (
    <article className="literature-reference-card">
      <div className="literature-reference-top">
        <span className="literature-reference-number">{row.number}</span>
        <span className={`literature-user-status ${status.tone}`}>{status.label}</span>
        {row.source_type && <span className="literature-source-type">{SOURCE_TYPE_COPY[row.source_type]}</span>}
        {evidence && <a className="literature-source-link" href={evidence} target="_blank" rel="noreferrer">Открыть источник ↗</a>}
      </div>
      <div className="literature-reference-body">
        <div><span className="literature-field-label">В работе</span><p>{row.original_citation}</p></div>
        {found && <div className="literature-found-block"><span className="literature-field-label">Найдено</span><p>{row.checker_found_citation}</p></div>}
        {row.notes && <div className="literature-note"><span>Комментарий</span><p>{row.notes}</p></div>}
        {evidenceUrls.length > 0 && (
          <div className="literature-evidence-list">
            <span className="literature-field-label">Источники проверки</span>
            {evidenceUrls.map((url) => <a key={url} href={url} target="_blank" rel="noreferrer">{url}</a>)}
          </div>
        )}
      </div>
    </article>
  );
}
