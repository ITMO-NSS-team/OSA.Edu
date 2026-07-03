export type Provider = "gemini" | "openrouter";
export type Status = "queued" | "extracting" | "mapping" | "awaiting_review" | "queued_check" | "checking" | "completed" | "failed" | "cancelled";
export type Severity = "critical" | "major" | "minor" | "info";
export type RuleStatus = "pass" | "violation" | "uncertain" | "not_applicable" | "not_checked";
export type RuleMode = "deterministic" | "structural" | "semantic" | "manual";
export type RuleScope = "document" | "title" | "goal" | "defense_statements" | "chapter" | "list" | "figure_table" | "formula" | "bibliography" | "presentation" | "defense" | "process";
export type CheckProfile = "core" | "full";

export interface ModelInfo { id: string; label: string; provider: Provider; tier: "free" | "production"; contextTokens: number; note: string; recommended?: boolean; }
export interface Rule { id: string; category: string; title: string; requirement: string; correctExample?: string; incorrectExample?: string; sourceLabel: string; sourceLine: number; mode: RuleMode; scope: RuleScope; severity: Severity; }
export interface Evidence { quote: string; blockId: string; location: string; page?: number; verified?: boolean; }
export interface RuleCoverage { candidateCount: number; checkedCandidateCount: number; packetCount: number; checkedPacketCount: number; fraction: number; exhaustive: boolean; }
export interface ProviderDiagnostic { at: string; operation: "structure" | "check"; attempt: number; httpStatus?: number; providerCode?: string; message: string; retryable: boolean; retryAfterMs: number; backoffMs: number; quotaMetric?: string; quotaDescription?: string; provider?: Provider; model?: string; providerName?: string; requestId?: string; networkCode?: string; }
export interface LlmRequestTrace { at: string; operation: "structure" | "check"; provider: Provider; model: string; providerName?: string; requestId?: string; compatibilityMode?: boolean; httpStatus: number; }
export interface LlmUsageStats { requests: number; retries: number; packets: number; candidates: number; estimatedInputTokens: number; rateLimitWaitMs: number; requestDurationMs: number; diagnostics: ProviderDiagnostic[]; traces: LlmRequestTrace[]; }

export type DocumentElementType = "title" | "abstract" | "introduction" | "goal" | "tasks" | "defense_statements" | "chapter" | "chapter_conclusions" | "conclusion" | "bibliography" | "appendices" | "other";
export interface DocumentMapElement { id: string; type: DocumentElementType; label: string; startBlockId: string; endBlockId: string; blockIds: string[]; pages: number[]; text: string; quote: string; confidence: number; state: "confirmed" | "ambiguous"; source: "llm" | "user"; note?: string; }
export interface DocumentMapIssue { code: string; severity: "warning" | "info"; message: string; elementIds: string[]; }
export interface DocumentMap { version: 2; createdAt: string; provider: Provider; model: string; promptHash: string; status: "ready" | "partial"; elements: DocumentMapElement[]; relations: []; issues: DocumentMapIssue[]; warnings: string[]; usage: LlmUsageStats; extraction: { totalBlocks: number; processedBlocks: number; totalBatches: number; processedBatches: number; }; review: { required: true; confirmedAt?: string; confirmedByUser: boolean; }; }

export interface TermFinding { term: string; kind: string; status: string; requiresExpansion: boolean; requiresRussianExplanation: boolean; firstUse?: Evidence; expansion?: Evidence; }
export interface CoverageMatrixItem { name: string; status: "found" | "not_found" | "ambiguous"; evidence: Evidence[]; }
export interface CoverageMatrixRow { fragmentId: string; label: string; complete: boolean; checkedBlocks: number; totalBlocks: number; items: CoverageMatrixItem[]; }
export interface RuleResult { ruleId: string; status: RuleStatus; severity: Severity; explanation: string; fix?: string; confidence: number; evidence: Evidence[]; checkedBy: "llm" | "system" | "detector"; evidenceStatus?: "verified" | "coverage_verified" | "not_required" | "rejected"; coverage?: RuleCoverage; checkedFragments?: string[]; relatedRuleIds?: string[]; findingIds?: string[]; termFindings?: TermFinding[]; coverageMatrix?: CoverageMatrixRow[]; consistencyNotes?: string[]; }
export interface ReportTechnical { appVersion: string; provider: Provider; model: string; promptHash: string; mapPromptHash?: string; }
export interface Report { ruleResults: RuleResult[]; ruleCatalog: Rule[]; documentMap?: DocumentMap; summary: string; score: number | null; scoreIsProvisional: boolean; coverage: number; candidateCoverage: number; counts: { critical: number; major: number; minor: number; info: number; pass: number; violation: number; uncertain: number; notApplicable: number; notChecked: number; }; checkedRules: number; totalRules: number; warnings: string[]; ruleStats: Array<{ status: RuleStatus; count: number }>; llmUsage: LlmUsageStats; technical: ReportTechnical; routing: { strategy: "explicit-map" | "scope-fallback" | "mixed"; fragments: number; checkRequests: number; explicitRules: number; fallbackRules: number; }; }
export interface Job { id: string; originalName: string; size: number; createdAt: string; updatedAt: string; startedAt?: string; finishedAt?: string; status: Status; provider: Provider; model: string; profile: CheckProfile; prompt: string; mapPrompt: string; additionalCriteria: string; attempts: number; progress: number; error?: string; diagnostics?: ProviderDiagnostic[]; documentMap?: DocumentMap; report?: Report; retryRuleIds?: string[]; }
export interface Health { ok: boolean; models: ModelInfo[]; configured: { gemini: boolean; openrouter: boolean }; defaults: { prompt: string; mapPrompt: string; additionalCriteria: string; profile: CheckProfile }; knowledge: { coreCount: number; softCount: number; fullCount: number; retrieval: string }; rateLimits: { gemini: Record<string, number>; openrouter: Record<string, number> }; }
export interface StructureBlock { id: string; page?: number; location: string; type: string; text: string; }
export interface StructureDetails { map: DocumentMap; blocks: StructureBlock[]; }
