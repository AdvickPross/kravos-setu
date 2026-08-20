import type { components } from "./api-types";

export type PatientSummary = components["schemas"]["PatientSummary"];
export type ParsedQuery = components["schemas"]["ParsedQuery"];
export type ResolvedEntity = components["schemas"]["ResolvedEntity"];
export type MatchQuality = components["schemas"]["MatchQuality"];
export type Brief = components["schemas"]["Brief"];
export type BriefSection = components["schemas"]["BriefSection"];
export type BriefLine = components["schemas"]["BriefLine"];
export type RetrievalResult = components["schemas"]["RetrievalResult"];
export type EntityFacts = components["schemas"]["EntityFacts"];
export type Fact = components["schemas"]["Fact"];
export type MissingItem = components["schemas"]["MissingItem"];
export type StageTimings = components["schemas"]["StageTimings"];
export type QueryResponse = components["schemas"]["QueryResponse"];

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body || response.statusText);
  }
  return response.json() as Promise<T>;
}

export function listPatients(q?: string): Promise<PatientSummary[]> {
  const search = q ? `?q=${encodeURIComponent(q)}` : "";
  return request(`/api/patients${search}`);
}

export function getPatient(patientId: string): Promise<PatientSummary> {
  return request(`/api/patients/${encodeURIComponent(patientId)}`);
}

export function postQuery(patientId: string, query: string): Promise<QueryResponse> {
  return request("/api/query", {
    method: "POST",
    body: JSON.stringify({ patient_id: patientId, query }),
  });
}

export { ApiError };
