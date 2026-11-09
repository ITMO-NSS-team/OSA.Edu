export type PresentationType = "scientific" | "industrial" | "collaborative" | "technological";
export interface PresentationOptions {
  presentationType: PresentationType;
  language: "ru" | "en";
  criteria: string[];
}
export interface PresentationRuntime {
  ok: boolean;
  installed: boolean;
  llmConfigured: boolean;
  error?: string;
  model: string;
  version?: string;
  revision?: string;
  pythonVersion?: string;
  concurrentJobs: number;
  maxConcurrency: number;
  maxFileSizeMb: number;
  maxPages: number;
  maxBatch: number;
  timeoutSeconds: number;
  criteria: { id: string; labelRu: string; labelEn: string; target: "slide" | "deck"; presentationTypes: PresentationType[] }[];
}
export interface PresentationJob {
  id: string;
  originalName: string;
  size: number;
  pageCount: number;
  status: "queued" | "running" | "completed" | "partial" | "failed" | "cancelled";
  createdAt: string;
  updatedAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  attempts: number;
  progress: number;
  progressMessage: string;
  error: string | null;
  model: string;
  options: PresentationOptions;
  resultAvailable: boolean;
  reportAvailable: boolean;
  logAvailable: boolean;
}
export interface CriterionResult {
  score?: number;
  evaluation_results?: Record<string, unknown>[];
  reason?: string;
  error_message?: string;
  comments?: string;
  recommendations?: string;
  [key: string]: unknown;
}
export interface PresentationResult {
  schemaVersion: 1;
  outcome: "completed" | "partial" | "failed";
  options: PresentationOptions;
  engine: { name: string; version: string; revision: string | null };
  evaluation: {
    slide_deck_path: string;
    overall_score: number | null;
    summary: string | null;
    tldr: string | null;
    slide_evaluations: {
      slide_id: number;
      slide_type?: { slide_type: string[] } | null;
      slide_description?: { title: string; description: string; summary: string } | null;
      evaluations?: Record<string, CriterionResult> | null;
    }[];
    deck_evaluations?: { evaluations: Record<string, CriterionResult> } | null;
  };
  diagnostics: {
    failedCriteria: { slideId: number | null; criterion: string }[];
    successfulCriteria: number;
    incompleteSummary: boolean;
    reportError?: string;
  };
}
