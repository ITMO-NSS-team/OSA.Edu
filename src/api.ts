import type { CheckProfile, Health, Job, Rule, StructureDetails } from "./types";

const API = "http://127.0.0.1:8787";
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${url}`, options);
  const body = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error((body as { error?: string })?.error || "Ошибка API.");
  return body as T;
}

export const api = {
  health: () => request<Health>("/api/health"),
  jobs: () => request<Job[]>("/api/jobs"),
  rules: (profile: CheckProfile) => request<Rule[]>(`/api/rules?profile=${profile}`),
  createJobs: (body: FormData) => request<Job[]>("/api/jobs", { method: "POST", body }),
  structure: (id: string) => request<StructureDetails>(`/api/jobs/${id}/structure`),
  updateMapElement: (jobId: string, elementId: string, patch: Record<string, unknown>) => request<Job>(`/api/jobs/${jobId}/map/elements/${elementId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }),
  addMapElement: (jobId: string, body: Record<string, unknown>) => request<Job>(`/api/jobs/${jobId}/map/elements`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  deleteMapElement: (jobId: string, elementId: string) => request<void>(`/api/jobs/${jobId}/map/elements/${elementId}`, { method: "DELETE" }),
  confirmStructure: (id: string) => request<Job>(`/api/jobs/${id}/confirm-structure`, { method: "POST" }),
  cancel: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  retry: (id: string) => request<Job>(`/api/jobs/${id}/retry`, { method: "POST" }),
  retryFailed: (id: string) => request<Job>(`/api/jobs/${id}/retry-failed`, { method: "POST" }),
  delete: (id: string) => request<void>(`/api/jobs/${id}`, { method: "DELETE" }),
  reportMarkdownUrl: (id: string) => `${API}/api/jobs/${id}/report.md`,
  reportJsonUrl: (id: string) => `${API}/api/jobs/${id}/report.json`
};
