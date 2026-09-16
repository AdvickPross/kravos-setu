/**
 * Client for /api/ocr — upload a scanned record, review what was read, keep
 * the parts that are right.
 *
 * Two calls, not one, mirroring the server. `extractScan` proposes and
 * `commitScan` writes, and the file is sent to both. Re-uploading costs a
 * second OCR pass and buys statelessness: no half-approved extraction sits on
 * the server between the two steps, and no cache key can go wrong in a way
 * that files one patient's records against another's id.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export type OcrLine = {
  text: string;
  confidence: number;
  page: number;
  line_no: number;
  reliable: boolean;
};

export type OcrFact = {
  fact_id: string;
  category: string;
  display: string | null;
  code: string | null;
  code_system: string | null;
  value: string | null;
  unit: string | null;
  confidence: number;
  page: number;
  line_no: number;
  line_text: string;
  also_in: string[];
};

export type ExtractResult = {
  source_file: string;
  engine: string;
  patient_id: string;
  mean_confidence: number;
  sections_seen: string[];
  lines: OcrLine[];
  facts: OcrFact[];
  unmatched: string[];
  ambiguous: string[];
  low_confidence: string[];
};

export type CommitResult = {
  inserted: number;
  requested: number;
  already_present: number;
};

export class OcrError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function post<T>(path: string, form: FormData): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { method: "POST", body: form });
  } catch {
    throw new OcrError(0, "Could not reach the API. Is it running on port 8000?");
  }
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new OcrError(
      response.status,
      typeof detail === "string" ? detail : `HTTP ${response.status}`,
    );
  }
  return body as T;
}

export function extractScan(
  file: File,
  patientId: string,
  documentDate?: string,
): Promise<ExtractResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("patientId", patientId);
  if (documentDate) form.append("documentDate", documentDate);
  return post<ExtractResult>("/api/ocr/extract", form);
}

export function commitScan(
  file: File,
  patientId: string,
  factIds: string[],
  documentDate?: string,
): Promise<CommitResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("patientId", patientId);
  form.append("factIds", factIds.join(","));
  if (documentDate) form.append("documentDate", documentDate);
  return post<CommitResult>("/api/ocr/commit", form);
}
