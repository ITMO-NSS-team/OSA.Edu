export type Provider = "gemini" | "openrouter";
export type Status = "queued" | "extracting" | "mapping" | "awaiting_review" | "queued_check" | "checking" | "completed" | "failed" | "cancelled";
export type NormControlStatus = "queued" | "queued_report" | "submitting" | "running" | "reporting" | "downloading" | "completed" | "failed" | "cancelled";
export type Severity = "critical" | "major" | "minor" | "info";
export type RuleStatus = "pass" | "violation" | "uncertain" | "not_applicable" | "not_checked";
export type RuleMode = "deterministic" | "candidate" | "structural" | "semantic" | "manual";
export type RuleScope = "document" | "title" | "goal" | "defense_statements" | "chapter" | "list" | "figure_table" | "formula" | "bibliography" | "presentation" | "defense" | "process";
export type CheckProfile = "core" | "full";

export interface ModelInfo { id: string; label: string; provider: Provider; tier: "free" | "production"; contextTokens: number; note: string; recommended?: boolean; }
export interface RelatedAdvice { title: string; summary: string; page: number; url: string; source: string; }
export interface Rule { id: string; category: string; title: string; userTitle?: string; relatedAdvice?: RelatedAdvice; requirement: string; correctExample?: string; incorrectExample?: string; sourceLabel: string; sourceLine: number; mode: RuleMode; scope: RuleScope; severity: Severity; dedupKey?: string; candidateFamily?: string; }
export interface Evidence { quote: string; context?: string; blockId: string; location: string; page?: number; start?: number; end?: number; verified?: boolean; token?: string; entityKind?: string; }
export interface RuleCoverage { domain?: string; candidateCount: number; checkedCandidateCount: number; packetCount?: number; checkedPacketCount?: number; respondedCandidateCount?: number; terminalCandidateCount?: number; ambiguousCandidateCount?: number; fraction?: number; exhaustive: boolean; }
export interface ProviderDiagnostic { at: string; operation: "structure" | "check" | "candidate"; attempt: number; httpStatus?: number; providerCode?: string; message: string; retryable: boolean; retryAfterMs: number; backoffMs: number; quotaMetric?: string; quotaDescription?: string; provider?: Provider; model?: string; providerName?: string; requestId?: string; networkCode?: string; }
export interface LlmRequestTrace { at: string; operation: "structure" | "check" | "candidate"; provider: Provider; model: string; providerName?: string; requestId?: string; compatibilityMode?: boolean; httpStatus: number; }
export interface LlmUsageStats { requests: number; retries: number; packets: number; candidates: number; estimatedInputTokens: number; rateLimitWaitMs: number; requestDurationMs: number; diagnostics: ProviderDiagnostic[]; traces: LlmRequestTrace[]; }

export type DocumentElementType = "title" | "abstract" | "introduction" | "goal" | "tasks" | "defense_statements" | "chapter" | "chapter_conclusions" | "conclusion" | "bibliography" | "appendices" | "other";
export interface DocumentMapElement { id: string; type: DocumentElementType; label: string; startBlockId: string; endBlockId: string; blockIds: string[]; pages: number[]; text: string; quote: string; confidence: number; state: "confirmed" | "ambiguous"; source: "llm" | "user" | "deterministic"; canonicalRole?: "canonical" | "secondary_copy" | "candidate"; documentUnit?: "main_work" | "secondary_front_matter"; note?: string; }
export interface DocumentMapIssue { code: string; severity: "warning" | "info"; message: string; elementIds: string[]; }
export interface DocumentMapRelation { type: string; statementIndex?: number; sourceSectionId?: string; targetSectionId?: string; targetStartBlockId?: string; role?: string; confidence: number; state: "confirmed" | "ambiguous"; reason?: string; source?: string; }
export interface DocumentMap { version: 2 | 3; createdAt: string; provider: Provider; model: string; promptHash: string; status: "ready" | "partial"; elements: DocumentMapElement[]; relations: DocumentMapRelation[]; issues: DocumentMapIssue[]; warnings: string[]; usage: LlmUsageStats; extraction: { totalBlocks: number; processedBlocks: number; totalBatches: number; processedBatches: number; }; review: { required: true; confirmedAt?: string; confirmedByUser: boolean; autoConfirmed?: boolean; confirmationMode?: "user" | "developer_auto"; }; }

export interface TermFinding { term: string; kind: string; status: string; requiresExpansion: boolean; requiresRussianExplanation: boolean; firstUse?: Evidence; expansion?: Evidence; }
export interface CoverageMatrixItem { name: string; status: "found" | "not_found" | "ambiguous"; evidence: Evidence[]; }
export interface CoverageMatrixRow { fragmentId: string; label: string; complete: boolean; checkedBlocks: number; totalBlocks: number; items: CoverageMatrixItem[]; }
export interface RuleResult { ruleId: string; status: RuleStatus; severity: Severity; explanation: string; fix?: string; confidence: number; evidence: Evidence[]; checkedBy: string; technicalIncomplete?: boolean; evidenceStatus?: "verified" | "coverage_verified" | "not_required" | "rejected"; coverage?: RuleCoverage; checkedFragments?: string[]; relatedRuleIds?: string[]; findingIds?: string[]; termFindings?: TermFinding[]; coverageMatrix?: CoverageMatrixRow[]; consistencyNotes?: string[]; }
export interface ReportTechnical { appVersion: string; provider: Provider; model: string; promptHash: string; mapPromptHash?: string; }
export interface IssueGroup { dedupKey: string; ruleIds: string[]; evidence: Evidence[]; }
export interface ReportHealth { status: "ready" | "review_required" | "technical_incomplete"; label: string; reasons: string[]; technicalIncompleteRuleIds: string[]; manualReviewRuleIds: string[]; }
export interface Report { ruleResults: RuleResult[]; ruleCatalog: Rule[]; issueGroups?: IssueGroup[]; documentMap?: DocumentMap; summary: string; reportHealth?: ReportHealth; score: number | null; scoreIsProvisional: boolean; coverage: number; candidateCoverage: number; automaticCandidateCoverage?: number; abbreviationCoverage?: number; abbreviationRuleCoverage?: number; counts: { critical: number; major: number; minor: number; info: number; pass: number; violation: number; uncertain: number; notApplicable: number; notChecked: number; }; checkedRules: number; totalRules: number; warnings: string[]; ruleStats: Array<{ status: RuleStatus; count: number }>; llmUsage: LlmUsageStats; technical: ReportTechnical; routing: { strategy: "manifest" | "fallback" | "manifest+fallback" | "explicit-map" | "scope-fallback" | "mixed"; fragments: number; checkRequests: number; physicalRequests?: number; plannedCheckRequests?: number; candidateRequests?: number; semanticRequests?: number; candidateFamilies?: number; abbreviationAuditRequests?: number; abbreviationPhysicalRequests?: number; abbreviationMode?: "deterministic" | "llm-inventory" | "llm-inventory-high-recall" | "llm-fact-map-high-recall"; abbreviationCandidateCount?: number; abbreviationResolvedCandidates?: number; abbreviationUnresolvedCandidates?: number; abbreviationRecoveryRequests?: number; evidenceVerifierRequests?: number; explicitRules: number; fallbackRules: number; }; }
export interface Job { id: string; originalName: string; size: number; createdAt: string; updatedAt: string; startedAt?: string; finishedAt?: string; status: Status; provider: Provider; model: string; profile: CheckProfile; prompt: string; mapPrompt: string; additionalCriteria: string; attempts: number; progress: number; progressMessage?: string; error?: string; diagnostics?: ProviderDiagnostic[]; documentMap?: DocumentMap; report?: Report; retryRuleIds?: string[]; developerMode?: boolean; }
export interface NormControlServerItem { name: string | null; title: string | null; description: string | null; }
export interface NormControlServerStatus { ok: boolean; diagnostics: Record<string, string>; prompts: NormControlServerItem[]; tools: NormControlServerItem[]; }
export interface NormControlAttemptLog { at: string; tool: string; attempt: number; maxAttempts: number; timeoutSeconds: number; elapsedMs: number; endpoint: string; dagId: string; progressSeen: boolean; status: "succeeded" | "remote_failed" | "timed_out" | "failed"; error?: string; diagnostics?: Record<string, string>; mcpStatus?: string; mcpMessage?: string; mcpErrorsCount?: number; mcpWarningsCount?: number; mcpErrors?: string[]; mcpWarnings?: string[]; runIdReceived?: boolean; reportPdfReceived?: boolean; outputPdfReceived?: boolean; }
export interface NormControlJob { id: string; originalName: string; size: number; createdAt: string; updatedAt: string; startedAt?: string; finishedAt?: string; status: NormControlStatus; progress: number; progressMessage?: string; attempts?: number; maxAttempts?: number; lastAttemptAt?: string | null; timeoutSeconds?: number; dagId: string; mcpUrl: string; taskId?: string; runId?: string; mcpStatus?: "queued" | "running" | "done" | "failed" | ""; message?: string; errors: string[]; warnings: string[]; attemptLogs?: NormControlAttemptLog[]; reportPath?: string; reportSize?: number; error?: string; }
export interface Health { ok: boolean; models: ModelInfo[]; configured: { gemini: boolean; openrouter: boolean }; defaults: { prompt: string; mapPrompt: string; additionalCriteria: string; profile: CheckProfile }; knowledge: { coreCount: number; softCount: number; fullCount: number; retrieval: string }; rateLimits: { gemini: Record<string, number>; openrouter: Record<string, number> }; normcontrol: { mcpUrl: string; dagId: string; mcpAttempts?: number; mcpAttemptTimeoutSeconds?: number; enabled: boolean }; }
export interface StructureBlock { id: string; page?: number; location: string; type: string; text: string; }
export interface StructureDetails { map: DocumentMap; blocks: StructureBlock[]; }

export type ReproducibilityJobStatus = "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled";
export interface ReproducibilityArtifacts {
  logAvailable?: boolean;
  sectionsAvailable?: boolean;
  sectionCount?: number | null;
  claimsAvailable?: boolean;
  claimsCount?: number | null;
  claimsPath?: string | null;
  verificationAvailable?: boolean;
  verifiedClaimsCount?: number | null;
  resultAvailable?: boolean;
}
export interface ReproducibilityAvailableActions {
  retryFull?: boolean;
  resumeVerification?: boolean;
  downloadLog?: boolean;
  openResult?: boolean;
  resumeStage?: "verification" | "claims" | null;
}
export interface ReproducibilityJob {
  id: string;
  originalName: string;
  repository: string;
  size: number;
  model: string;
  createdAt: string;
  updatedAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  status: ReproducibilityJobStatus;
  progress: number;
  progressMessage?: string;
  resultAvailable?: boolean;
  error?: string | null;
  runMode?: "full" | "verification-only";
  artifacts?: ReproducibilityArtifacts;
  availableActions?: ReproducibilityAvailableActions;
}
export interface ReproducibilityStatus {
  ok: boolean;
  osaInstalled: boolean;
  osaVersion?: string | null;
  llmConfigured: boolean;
  model: string;
  baseUrl?: string;
  pythonVersion?: string;
  pythonSupportedForPaperClaims?: boolean;
  gitInstalled?: boolean;
  gitVersion?: string | null;
  windowsReloadWarning?: boolean;
  markerLowVram?: boolean;
}
export interface ReproducibilityPreflightCheck { id: string; label: string; ok: boolean; blocking: boolean; detail: string; }
export interface ReproducibilityPreflight { ok: boolean; checks: ReproducibilityPreflightCheck[]; warnings: string[]; model: string; baseUrl: string; }
export interface ReproducibilityLog { text: string; available: boolean; size: number; truncated?: boolean; }

export type LiteratureStatus = "OK" | "OK_MINOR_MISMATCH" | "METADATA_MISMATCH" | "SUSPICIOUS" | "LIKELY_HALLUCINATED" | "UNVERIFIED" | "ERROR" | "NOT_A_PAPER";
export type LiteratureVerdict = "VERIFIED" | "SUSPICIOUS" | "UNVERIFIED" | "ERROR";
export type LiteratureSourceType = "PAPER" | "PREPRINT" | "BOOK" | "STANDARD" | "REPORT" | "DATASET" | "DOCUMENTATION" | "REPOSITORY" | "WEB" | "OTHER" | "UNKNOWN";
export type LiteratureJobStatus = "queued" | "running" | "cancelling" | "done" | "failed" | "cancelled";
export interface LiteratureRow { number: string; status: LiteratureStatus; verdict?: LiteratureVerdict; source_type?: LiteratureSourceType; original_citation: string; checker_found_citation: string; evidence_url: string; google_scholar_url: string; notes: string; evidence_urls?: string[]; verification_stage?: string; }
export interface LiteratureResult { filename: string; reference_count: number; rows: LiteratureRow[]; counts: Partial<Record<LiteratureStatus, number>>; verdict_counts?: Partial<Record<LiteratureVerdict, number>>; warnings?: string[]; web_stage?: { enabled: boolean; eligible: number; attempted: number; completed: number; failed: number; skipped_due_to_limit?: number; }; }
export interface LiteratureJob { id: string; originalName: string; size: number; createdAt: string; updatedAt: string; startedAt?: string; finishedAt?: string; status: LiteratureJobStatus; model: string; progress: number; progressMessage?: string; result?: LiteratureResult | null; error?: string | null; }

export type RepositoryQualityJobStatus = "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled";
export interface RepositoryQualityJob {
  id: string;
  repository: string;
  model: string;
  createdAt: string;
  updatedAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  status: RepositoryQualityJobStatus;
  progress: number;
  progressMessage?: string;
  attempts?: number;
  resultAvailable?: boolean;
  logAvailable?: boolean;
  error?: string | null;
}
export interface RepositoryQualityStatus {
  ok: boolean;
  osaInstalled: boolean;
  osaVersion?: string | null;
  llmConfigured: boolean;
  model: string;
  baseUrl?: string;
  pythonVersion?: string;
  gitInstalled?: boolean;
  gitVersion?: string | null;
}
export interface RepositoryQualityPreflightCheck { id: string; label: string; ok: boolean; blocking: boolean; detail: string; }
export interface RepositoryQualityPreflight { ok: boolean; checks: RepositoryQualityPreflightCheck[]; warnings: string[]; model: string; baseUrl: string; }
export interface RepositoryQualityLog { text: string; available: boolean; size: number; truncated?: boolean; }
