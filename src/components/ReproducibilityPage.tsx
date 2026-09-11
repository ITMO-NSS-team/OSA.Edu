import { useMemo, useRef, useState } from "react";

type UiLang = "ru" | "en";
type ClaimTone = "confirmed" | "partial" | "rejected" | "uncertain";
type ClaimFilter = "checked" | "confirmed" | "rejected" | "excluded" | "extracted";

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
  const [jsonName, setJsonName] = useState("");
  const [raw, setRaw] = useState<unknown>(null);
  const [error, setError] = useState("");
  const [printLang, setPrintLang] = useState<UiLang>("ru");
  const [claimFilter, setClaimFilter] = useState<ClaimFilter>("checked");
  const pdfInput = useRef<HTMLInputElement>(null);
  const jsonInput = useRef<HTMLInputElement>(null);

  const analysis = useMemo(() => raw ? normalizeAnalysis(raw, paperName) : null, [raw, paperName]);
  const t = COPY[printLang];
  const filteredClaims = useMemo(() => {
    if (!analysis) return [];
    if (claimFilter === "confirmed") return analysis.claims.filter((claim) => claim.tone === "confirmed");
    if (claimFilter === "rejected") return analysis.claims.filter((claim) => claim.tone === "rejected");
    if (claimFilter === "excluded") return [];
    return analysis.claims;
  }, [analysis, claimFilter]);

  function selectClaimFilter(filter: ClaimFilter) {
    setClaimFilter(filter);
    window.setTimeout(() => {
      document.querySelector(".repro-claims-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
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

  return <section className="page reproducibility-page">
    <div className="repro-hero repro-no-print">
      <h1>Воспроизводимость</h1>
    </div>

    <div className="panel repro-input-card repro-no-print">
      <label>
        <span>Репозиторий</span>
        <input value={repo} onChange={(event) => setRepo(event.target.value)} placeholder="https://github.com/owner/project" />
      </label>

      <div className="repro-file-field">
        <span>PDF работы</span>
        <button className="repro-file-button" type="button" onClick={() => pdfInput.current?.click()}>
          <span className="repro-file-icon">PDF</span>
          <span><strong>{paperName || "Выбрать PDF"}</strong></span>
        </button>
        <input ref={pdfInput} hidden type="file" accept=".pdf,application/pdf" onChange={(event) => setPaperName(event.target.files?.[0]?.name || "")} />
      </div>

      <div className="repro-run-row">
        <button
          className="button primary repro-run-button"
          type="button"
          onClick={() => jsonInput.current?.click()}
        >
          Запустить
        </button>
        <input
          ref={jsonInput}
          hidden
          type="file"
          accept=".json,application/json"
          onChange={(event) => void loadJson(event.target.files?.[0])}
        />
      </div>
      {error && <div className="inline-error repro-full">{error}</div>}
    </div>

    {!analysis ? null : <>
      <div className="repro-print-heading">
        <div>
          <span className="repro-eyebrow">OSA.Edu</span>
          <h1>{t.title}</h1>
          <p>{t.subtitle}</p>
        </div>
        <div className="repro-print-meta">
          {analysis.repository && <span><b>{t.repo}:</b> {analysis.repository}</span>}
          {analysis.paperPath && <span><b>{t.paper}:</b> {basename(analysis.paperPath)}</span>}
        </div>
      </div>

      <div className="panel repro-export-card repro-no-print">
        <strong>{analysis.title}</strong>
        <div className="actions">
          <button className="button secondary" onClick={downloadJson}>↓ JSON</button>
          <button className="button secondary" onClick={() => printPdf("ru")}>↓ PDF (RU)</button>
          <button className="button primary" onClick={() => printPdf("en")}>↓ PDF (EN)</button>
        </div>
      </div>

      <section className="repro-stats-section">
        <div className="repro-section-title">
          <h2>{t.stats}</h2>
          <span>{analysis.stats.implementationRatePct}% подтверждено</span>
        </div>
        <div className="repro-stats repro-no-print">
          <Stat
            value={analysis.stats.sourceTotal}
            label={t.allClaims}
            active={claimFilter === "extracted"}
            onClick={() => selectClaimFilter("extracted")}
          />
          <Stat
            value={analysis.stats.scoredTotal}
            label={t.eligible}
            active={claimFilter === "checked"}
            onClick={() => selectClaimFilter("checked")}
          />
          <Stat
            value={analysis.stats.implemented}
            label={t.confirmed}
            tone="confirmed"
            active={claimFilter === "confirmed"}
            onClick={() => selectClaimFilter("confirmed")}
          />
          <Stat
            value={analysis.stats.notImplemented}
            label={t.rejected}
            tone="rejected"
            active={claimFilter === "rejected"}
            onClick={() => selectClaimFilter("rejected")}
          />
          <Stat
            value={analysis.stats.excluded}
            label={t.excluded}
            tone="uncertain"
            active={claimFilter === "excluded"}
            onClick={() => selectClaimFilter("excluded")}
          />
        </div>

        <div className="repro-stats repro-print-only">
          <Stat value={analysis.stats.sourceTotal} label={t.allClaims} />
          <Stat value={analysis.stats.scoredTotal} label={t.eligible} />
          <Stat value={analysis.stats.implemented} label={t.confirmed} tone="confirmed" />
          <Stat value={analysis.stats.notImplemented} label={t.rejected} tone="rejected" />
          <Stat value={analysis.stats.excluded} label={t.excluded} tone="uncertain" />
        </div>

      </section>

      <section className="repro-claims-section">
        <div className="repro-section-title">
          <h2>{
            claimFilter === "confirmed" ? "Подтверждённые утверждения" :
            claimFilter === "rejected" ? "Не подтверждённые утверждения" :
            claimFilter === "excluded" ? "Исключённые утверждения" :
            claimFilter === "extracted" ? "Выделенные утверждения" :
            t.claims
          }</h2>
          <span>{
            claimFilter === "extracted" ? `${analysis.claims.length} из ${analysis.stats.sourceTotal}` :
            claimFilter === "excluded" ? analysis.stats.excluded :
            filteredClaims.length
          }</span>
        </div>

        {claimFilter === "excluded" && <div className="panel repro-filter-note">
          <strong>Исключено: {analysis.stats.excluded}</strong>
          <span>Эти утверждения не прошли отбор по проверяемости. В результате анализа сохранено их количество, но сами тексты исключённых утверждений не включены.</span>
        </div>}

        {claimFilter === "extracted" && analysis.stats.sourceTotal > analysis.claims.length && <div className="panel repro-filter-note">
          <strong>Выделено всего: {analysis.stats.sourceTotal}</strong>
          <span>В этом отчёте доступны {analysis.claims.length} утверждений, прошедших отбор. Ещё {analysis.stats.sourceTotal - analysis.claims.length} были исключены до проверки и представлены только в статистике.</span>
        </div>}

        <div className="repro-claims-list">
          {filteredClaims.map((claim, index) => <article className="panel repro-claim-card" key={claim.id}>
            <div className="repro-claim-top">
              <span className={`repro-verdict ${claim.tone}`}>{verdictLabel(claim, printLang)}</span>
              <span className="repro-claim-number">{claim.id || `CLAIM ${index + 1}`}</span>
            </div>
            <h3>{claim.text}</h3>
            <div className="repro-claim-meta">
              {claim.section && <span><b>{t.section}</b>{claim.section}</span>}
              {claim.category && <span><b>{t.category}</b>{humanize(claim.category)}</span>}
              {claim.verifiability && <span><b>{t.verifiability}</b>{humanize(claim.verifiability)}</span>}
              {claim.implementationConfidence && <span><b>{t.confidence}</b>{humanize(claim.implementationConfidence)}</span>}
            </div>
            {claim.originalText && claim.originalText !== claim.text && <div className="repro-source-text">
              <strong>{t.sourceText}</strong>
              <blockquote>{claim.originalText}</blockquote>
            </div>}
            {claim.explanation && <div className="repro-explanation"><strong>{t.details}</strong><p>{claim.explanation}</p></div>}
            {claim.evidence.length > 0 && <div className="repro-evidence">
              <strong>{t.evidence}</strong>
              {claim.evidence.map((item, evidenceIndex) => <div className="repro-evidence-item" key={evidenceIndex}>
                {item.path && <code>{item.path}</code>}
                {item.details && <p>{item.details}</p>}
              </div>)}
            </div>}
          </article>)}
        </div>
      </section>
    </>}
  </section>;
}

function Stat({
  value,
  label,
  tone,
  active = false,
  onClick,
}: {
  value: number;
  label: string;
  tone?: ClaimTone;
  active?: boolean;
  onClick?: () => void;
}) {
  if (!onClick) {
    return <div className={`repro-stat ${tone || ""}`}><strong>{value}</strong><span>{label}</span></div>;
  }

  return <button
    type="button"
    className={`repro-stat repro-stat-button ${tone || ""} ${active ? "active" : ""}`}
    onClick={onClick}
    aria-pressed={active}
  >
    <strong>{value}</strong>
    <span>{label}</span>
  </button>;
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

  // Canonical OSA paper_analysis.json schema (schema_version 1.0):
  // claim_verification.claims[] + claim_verification.stats.
  const claimsSource = Array.isArray(claimVerification.claims)
    ? claimVerification.claims
    : [];
  const claims = claimsSource.map((item, index) => normalizeClaim(item, index));

  const sourceTotal = pickNumber(verificationStats, ["source_total"]) ??
    pickNumber(paperClaims, ["claim_count"]) ?? claims.length;
  const eligibleTotal = pickNumber(verificationStats, ["eligible_total", "total"]) ?? claims.length;
  const scoredTotal = pickNumber(verificationStats, ["scored_total"]) ?? claims.length;
  const implemented = pickNumber(verificationStats, ["implemented"]) ?? claims.filter((claim) => claim.tone === "confirmed").length;
  const notImplemented = pickNumber(verificationStats, ["not_implemented"]) ?? claims.filter((claim) => claim.tone === "rejected").length;
  const excluded = pickNumber(verificationStats, ["excluded_low_verifiability"]) ?? Math.max(0, sourceTotal - eligibleTotal);
  const hiddenLowConfidence = pickNumber(verificationStats, ["hidden_low_confidence"]) ?? 0;
  const implementationRatePct = pickNumber(verificationStats, ["implementation_rate_pct"]) ??
    (scoredTotal ? Math.round(implemented / scoredTotal * 100) : 0);
  const uncertain = Math.max(0, scoredTotal - implemented - notImplemented);

  const paperPath = pickString(sourcePaper, ["path"]) || pickString(paperClaims, ["source_path"]) || selectedPaperName;
  const repository = pickString(source, ["repository"]);

  return {
    title: paperPath ? basename(paperPath) : "paper_analysis.json",
    repository,
    paperPath,
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
