import type { LanguageCategory, LanguageFinding, LanguageJob } from "./types";

export function languageStatus(job: LanguageJob): LanguageJob["status"] {
  return job.status === "done" && job.result && !job.result.complete ? "partial" : job.status;
}

export function languageProgress(job: LanguageJob): number {
  const ratio = job.result?.coverage?.ratio;
  const progress = typeof ratio === "number" ? ratio * 100 : job.progress;
  return Number.isFinite(progress) ? Math.max(0, Math.min(100, Math.round(progress))) : 0;
}

export function canResumeLanguage(job: LanguageJob): boolean {
  return ["partial", "failed", "cancelled"].includes(languageStatus(job))
    && !job.result?.complete && (job.canResume ?? !job.result);
}

export function languageFindings(findings: LanguageFinding[], category: LanguageCategory | "all"): LanguageFinding[] {
  return findings.filter(item => category === "all" || item.category === category)
    .slice().sort((a, b) => a.page - b.page || (a.source?.order ?? 0) - (b.source?.order ?? 0)
      || (a.source?.start ?? 0) - (b.source?.start ?? 0) || a.id.localeCompare(b.id));
}
