import type {
  CheckProfile,
  Health,
  Job,
  LiteratureJob,
  NormControlJob,
  NormControlServerStatus,
  ReproducibilityJob,
  ReproducibilityLog,
  ReproducibilityPreflight,
  ReproducibilityStatus,
  Rule,
  StructureDetails,
} from "./types";

const API = (
  import.meta.env.VITE_API_BASE_URL?.trim() || "http://127.0.0.1:8787"
).replace(/\/+$/, "");
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${url}`, options);
  const body =
    response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error((body as { error?: string })?.error || "Ошибка API.");
  return body as T;
}

export const api = {
  health: () => request<Health>("/api/health"),
  jobs: () => request<Job[]>("/api/jobs"),
  normControlJobs: () => request<NormControlJob[]>("/api/normcontrol/jobs"),
  normControlStatus: () =>
    request<NormControlServerStatus>("/api/normcontrol/status"),
  reproducibilityStatus: () =>
    request<ReproducibilityStatus>("/api/reproducibility/status"),
  reproducibilityJobs: () =>
    request<ReproducibilityJob[]>("/api/reproducibility/jobs"),
  reproducibilityPreflight: (repository: string) =>
    request<ReproducibilityPreflight>(`/api/reproducibility/preflight?repository=${encodeURIComponent(repository)}`),
  createReproducibilityJob: (body: FormData) =>
    request<ReproducibilityJob>("/api/reproducibility/jobs", { method: "POST", body }),
  reproducibilityJob: (id: string) =>
    request<ReproducibilityJob>(`/api/reproducibility/jobs/${id}`),
  reproducibilityResult: (id: string) =>
    request<unknown>(`/api/reproducibility/jobs/${id}/result`),
  reproducibilityLog: (id: string) =>
    request<ReproducibilityLog>(`/api/reproducibility/jobs/${id}/log`),
  reproducibilityLogDownloadUrl: (id: string) =>
    `${API}/api/reproducibility/jobs/${id}/log.txt`,
  cancelReproducibilityJob: (id: string) =>
    request<ReproducibilityJob>(`/api/reproducibility/jobs/${id}/cancel`, { method: "POST" }),
  retryReproducibilityJob: (id: string) =>
    request<ReproducibilityJob>(`/api/reproducibility/jobs/${id}/retry`, { method: "POST" }),
  resumeReproducibilityJob: (id: string) =>
    request<ReproducibilityJob>(`/api/reproducibility/jobs/${id}/resume`, { method: "POST" }),
  rules: (profile: CheckProfile) =>
    request<Rule[]>(`/api/rules?profile=${profile}`),
  createJobs: (body: FormData) =>
    request<Job[]>("/api/jobs", { method: "POST", body }),
  createNormControlJob: (body: FormData) =>
    request<NormControlJob>("/api/normcontrol/jobs", { method: "POST", body }),
  retryNormControlJob: (id: string) =>
    request<NormControlJob>(`/api/normcontrol/jobs/${id}/retry`, {
      method: "POST",
    }),
  structure: (id: string) =>
    request<StructureDetails>(`/api/jobs/${id}/structure`),
  updateMapElement: (
    jobId: string,
    elementId: string,
    patch: Record<string, unknown>,
  ) =>
    request<Job>(`/api/jobs/${jobId}/map/elements/${elementId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  addMapElement: (jobId: string, body: Record<string, unknown>) =>
    request<Job>(`/api/jobs/${jobId}/map/elements`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteMapElement: (jobId: string, elementId: string) =>
    request<void>(`/api/jobs/${jobId}/map/elements/${elementId}`, {
      method: "DELETE",
    }),
  confirmStructure: (id: string) =>
    request<Job>(`/api/jobs/${id}/confirm-structure`, { method: "POST" }),
  cancel: (id: string) =>
    request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  retry: (id: string) =>
    request<Job>(`/api/jobs/${id}/retry`, { method: "POST" }),
  restart: (id: string) =>
    request<Job>(`/api/jobs/${id}/restart`, { method: "POST" }),
  retryFailed: (id: string) =>
    request<Job>(`/api/jobs/${id}/retry-failed`, { method: "POST" }),
  delete: (id: string) =>
    request<void>(`/api/jobs/${id}`, { method: "DELETE" }),
  deleteNormControlJob: (id: string) =>
    request<void>(`/api/normcontrol/jobs/${id}`, { method: "DELETE" }),
  reportPdfUrl: (id: string) => `${API}/api/jobs/${id}/report.pdf`,
  developerReportPdfUrl: (id: string) =>
    `${API}/api/jobs/${id}/developer-report.pdf`,
  reportMarkdownUrl: (id: string) => `${API}/api/jobs/${id}/report.md`,
  reportJsonUrl: (id: string) => `${API}/api/jobs/${id}/report.json`,
  normControlReportPdfUrl: (id: string) =>
    `${API}/api/normcontrol/jobs/${id}/report.pdf`,
  literatureJobs: () => request<LiteratureJob[]>("/api/literature/jobs"),
  createLiteratureJobs: (body: FormData) =>
    request<LiteratureJob[]>("/api/literature/jobs", { method: "POST", body }),
  cancelLiteratureJob: (id: string) =>
    request<LiteratureJob>(`/api/literature/jobs/${id}/cancel`, {
      method: "POST",
    }),
  retryLiteratureJob: (id: string) =>
    request<LiteratureJob>(`/api/literature/jobs/${id}/retry`, {
      method: "POST",
    }),
  deleteLiteratureJob: (id: string) =>
    request<void>(`/api/literature/jobs/${id}`, { method: "DELETE" }),
};
