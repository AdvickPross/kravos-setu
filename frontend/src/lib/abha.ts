/**
 * Typed client for the /api/abha routes.
 *
 * Separate from `lib/api.ts` because the two have different obligations. The
 * clinical API is read-only and its failures are boring; these calls carry
 * Aadhaar numbers and OTPs to a government system, and the console that drives
 * them has to *show* what was sent and what came back — that is the whole
 * point of manual M1 testing. So every call here returns the status code and
 * the parsed body rather than throwing, and `AbhaCall` is the record a step
 * card renders.
 *
 * Nothing here stores an Aadhaar number or an OTP beyond the React state of
 * the step that collected it.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export type FieldError = { attribute?: string; message?: string };

/** One request/response pair, kept so the UI can display the evidence. */
export type AbhaCall<T = unknown> = {
  method: string;
  path: string;
  requestBody: unknown;
  status: number;
  ok: boolean;
  body: T;
  /** ABDM's own error code when the failure came from them, not from us. */
  abdmCode?: string;
  message?: string;
  /** ABDM's per-field complaints, when it sent any. This is usually the only
   *  actionable part of a 400 -- "txnId: Invalid Transaction Id" rather than
   *  "ABDM returned HTTP 400". */
  fields?: FieldError[];
  ms: number;
};

function describe(
  status: number,
  body: unknown,
): { message?: string; abdmCode?: string; fields?: FieldError[] } {
  if (typeof body === "string") return { message: body };
  if (body && typeof body === "object") {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return { message: detail };
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const d = detail as {
        code?: string;
        message?: string;
        fields?: FieldError[] | null;
      };
      return { message: d.message, abdmCode: d.code, fields: d.fields ?? undefined };
    }
    if (Array.isArray(detail)) {
      // FastAPI's validation-error shape.
      const first = detail[0] as { msg?: string; loc?: string[] } | undefined;
      return { message: first?.msg ? `${first.loc?.slice(1).join(".")}: ${first.msg}` : undefined };
    }
  }
  return status >= 400 ? { message: `HTTP ${status}` } : {};
}

async function call<T>(
  method: string,
  path: string,
  options: { body?: unknown; headers?: Record<string, string> } = {},
): Promise<AbhaCall<T>> {
  const started = performance.now();
  let status = 0;
  let parsed: unknown = null;
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers: { "Content-Type": "application/json", ...options.headers },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    status = response.status;
    const text = await response.text();
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = text;
    }
  } catch (err) {
    // A network failure is not an HTTP status. Reported as 0 so the UI can say
    // "could not reach the API" rather than inventing a code.
    parsed = { detail: err instanceof Error ? err.message : "Could not reach the API." };
  }
  return {
    method,
    path,
    requestBody: options.body ?? null,
    status,
    ok: status >= 200 && status < 300,
    body: parsed as T,
    ms: performance.now() - started,
    ...describe(status, parsed),
  };
}

// -- shapes we read back ----------------------------------------------------

export type AbhaStatus = { configured: boolean; environment: string | null };

export type OtpChallenge = {
  txn_id: string;
  message?: string | null;
  masked_identifier?: string | null;
};

export type AbhaAccount = {
  abha_number?: string | null;
  abha_address?: string | null;
  name?: string | null;
  gender?: string | null;
  date_of_birth?: string | null;
  mobile?: string | null;
  email?: string | null;
  state?: string | null;
  district?: string | null;
  kyc_verified?: boolean | null;
  status?: string | null;
  /** Only populated on a multi-account login response; it is what
   *  `selectAccount` sends back to disambiguate. */
  index?: string | null;
};

export type EnrolmentResult = {
  txn_id?: string | null;
  account: AbhaAccount;
  token?: string | null;
  is_new?: boolean | null;
  message?: string | null;
};

export type LoginResult = {
  txn_id?: string | null;
  token?: string | null;
  t_token?: string | null;
  accounts: AbhaAccount[];
  message?: string | null;
};

export type ResendState = {
  resends_used: number;
  resends_remaining: number;
  seconds_until_allowed: number;
};

export type AbhaLink = {
  abha_number: string;
  patient_id: string;
  abha_address: string | null;
  linked_at: string;
};

export type LoginMethod = "aadhaar" | "mobile" | "abha-number";
export type AddressChannel = "aadhaar" | "mobile";

// -- the calls --------------------------------------------------------------

export const abha = {
  status: () => call<AbhaStatus>("GET", "/api/abha/status"),

  // enrolment (§1 of the checklist)
  enrolRequestOtp: (aadhaar: string, txnId?: string) =>
    call<OtpChallenge>("POST", "/api/abha/enrol/aadhaar/otp", {
      body: txnId ? { aadhaar, txnId } : { aadhaar },
    }),
  enrolVerify: (txnId: string, otp: string, mobile?: string) =>
    call<EnrolmentResult>("POST", "/api/abha/enrol/aadhaar/verify", {
      body: { txnId, otp, ...(mobile ? { mobile } : {}) },
    }),
  mobileOtp: (txnId: string, mobile: string, token: string) =>
    call<OtpChallenge>("POST", "/api/abha/enrol/mobile/otp", {
      body: { txnId, mobile },
      headers: { "X-token": token },
    }),
  mobileVerify: (txnId: string, otp: string, token: string) =>
    call("POST", "/api/abha/enrol/mobile/verify", {
      body: { txnId, otp },
      headers: { "X-token": token },
    }),
  addressSuggestions: (txnId: string) =>
    call<string[]>("GET", `/api/abha/enrol/address/suggestions?txnId=${encodeURIComponent(txnId)}`),
  setAddress: (txnId: string, abhaAddress: string) =>
    call("POST", "/api/abha/enrol/address", { body: { txnId, abhaAddress, preferred: true } }),

  // verification on the ABHA host (§5-8)
  loginOtp: (identifier: string, method: LoginMethod, txnId?: string) =>
    call<OtpChallenge>("POST", "/api/abha/login/otp", {
      body: { identifier, method, ...(txnId ? { txnId } : {}) },
    }),
  loginVerify: (txnId: string, otp: string, method: LoginMethod) =>
    call<LoginResult>("POST", "/api/abha/login/verify", { body: { txnId, otp, method } }),
  selectAccount: (txnId: string, index: string, tToken: string) =>
    call<LoginResult>("POST", "/api/abha/login/select", {
      body: { txnId, index },
      headers: { "T-token": tToken },
    }),

  // verification by ABHA address, on the PHR host (§14; VRFY_ABHA_102/202)
  addressSearch: (abhaAddress: string) =>
    call<AbhaAccount | null>("POST", "/api/abha/address/search", { body: { abhaAddress } }),
  addressOtp: (abhaAddress: string, channel: AddressChannel) =>
    call<OtpChallenge>("POST", "/api/abha/address/otp", { body: { abhaAddress, channel } }),
  addressVerify: (txnId: string, otp: string, channel: AddressChannel) =>
    call<LoginResult>("POST", "/api/abha/address/verify", { body: { txnId, otp, channel } }),

  // profile reads (§9-11)
  profile: (token: string, viaAbhaAddress = false) =>
    call("GET", `/api/abha/profile?viaAbhaAddress=${viaAbhaAddress}`, {
      headers: { "X-token": token },
    }),
  qr: (token: string) => call("GET", "/api/abha/qr", { headers: { "X-token": token } }),

  // resend budget (CRT_ABHA_106)
  otpState: (txnId: string) =>
    call<ResendState>("GET", `/api/abha/otp/state?txnId=${encodeURIComponent(txnId)}`),

  // tagging (§16)
  link: (abhaNumber: string, patientId: string, abhaAddress?: string | null) =>
    call<AbhaLink>("POST", "/api/abha/link", {
      body: { abhaNumber, patientId, ...(abhaAddress ? { abhaAddress } : {}) },
    }),
  linkByAbha: (abhaNumber: string) =>
    call<{ abha_number: string; patient_id: string | null }>(
      "GET",
      `/api/abha/link/by-abha/${encodeURIComponent(abhaNumber)}`,
    ),
  unlink: (abhaNumber: string) =>
    call("DELETE", `/api/abha/link/${encodeURIComponent(abhaNumber)}`),
};

/** The ABHA card is an image, not JSON — fetched separately as a blob URL. */
export async function fetchCardUrl(token: string, viaAbhaAddress = false): Promise<string> {
  const response = await fetch(
    `${API_BASE_URL}/api/abha/card?viaAbhaAddress=${viaAbhaAddress}`,
    { headers: { "X-token": token } },
  );
  if (!response.ok) throw new Error(await response.text());
  return URL.createObjectURL(await response.blob());
}
