/**
 * Typed client for the M2 routes (`/api/m2/*`).
 *
 * Built on the same `call` as the M1 console: every call returns its status
 * and body rather than throwing, so a step card can show what crossed the
 * wire. M2 is mostly ABDM talking to our server in the background; these
 * routes are how a tester sees it.
 */

import { call } from "./abha";

export type M2Status = {
  abdmConfigured: boolean;
  environment: string | null;
  hipId: string;
  hipConfigured: boolean;
  hipName: string | null;
  verifyGatewayTokens: boolean;
  otpDelivery: string;
};

export type BridgeInfo = {
  bridge?: { id?: string; name?: string; url?: string; active?: boolean; blocklisted?: boolean };
  services?: { id?: string; name?: string; types?: string[]; active?: boolean; [k: string]: unknown }[];
};

export type CareContextStatus = "UNLINKED" | "LINKED" | "LINK_REQUESTED" | "LINK_FAILED";

export type CareContext = {
  referenceNumber: string;
  display: string;
  hiType: string;
  status: CareContextStatus;
  linkedAt: string | null;
};

export type PatientState = {
  patientId: string;
  name: string | null;
  gender: string | null;
  birthDate: string | null;
  tokenDemographics: { name: string; gender: string; yearOfBirth: number | null };
  abha: { abhaNumber: string | null; abhaAddress: string | null; taggedAt: string } | null;
  mobile: string | null;
  linkTokenExpiresAt: string | null;
  careContexts: CareContext[];
  sms: { status: string; notifiedAt: string; recordsNotified: number } | null;
};

export type Transaction = {
  id: number;
  request_id: string;
  patient_id: string | null;
  transaction_id: string | null;
  operation: string;
  status: string;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type Consent = {
  consent_id: string;
  abha_address: string | null;
  hip_id: string;
  hiu_id: string | null;
  hi_types: string[];
  care_contexts: { patientReference?: string; careContextReference?: string }[];
  date_from: string;
  date_to: string;
  data_erase_at: string | null;
  updated_at: string;
};

export type Transfer = {
  transaction_id: string;
  consent_id: string;
  status: string;
  statuses: { careContextReference: string; hiStatus: string; description?: string }[] | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
};

export type LinkOutcome = { patientId: string; outcome: string };

const enc = encodeURIComponent;

export const m2 = {
  status: () => call<M2Status>("GET", "/api/m2/status"),
  bridge: () => call<BridgeInfo>("GET", "/api/m2/bridge"),
  updateBridgeUrl: (url: string) => call("PATCH", "/api/m2/bridge/url", { body: { url } }),

  patientState: (patientId: string) =>
    call<PatientState>("GET", `/api/m2/patients/${enc(patientId)}/state`),
  setMobile: (patientId: string, mobile: string) =>
    call<{ patientId: string; mobile: string }>("PUT", `/api/m2/patients/${enc(patientId)}/mobile`, {
      body: { mobile },
    }),
  fhirPreview: (patientId: string, reference: string) =>
    call<Record<string, unknown>>(
      "GET",
      `/api/m2/patients/${enc(patientId)}/care-contexts/${enc(reference)}/fhir`,
    ),

  linkPatient: (patientId: string) =>
    call<LinkOutcome>("POST", `/api/m2/patients/${enc(patientId)}/link`),
  runBatch: () => call<{ status: string }>("POST", "/api/m2/link-batch"),
  notifyUpdate: (patientId: string, careContextReference: string) =>
    call<{ status: string; requestId: string }>("POST", "/api/m2/care-contexts/notify", {
      body: { patientId, careContextReference, hiTypes: ["OPConsultation"] },
    }),

  transactions: (patientId?: string, limit = 40) =>
    call<Transaction[]>(
      "GET",
      `/api/m2/transactions?limit=${limit}${patientId ? `&patient_id=${enc(patientId)}` : ""}`,
    ),
  consents: () => call<Consent[]>("GET", "/api/m2/consents"),
  transfers: () => call<Transfer[]>("GET", "/api/m2/transfers"),
};

/** What a link attempt's outcome means, in the tester's words. */
export const LINK_OUTCOMES: Record<string, { tone: "info" | "warn" | "danger"; text: string }> = {
  TOKEN_REQUESTED: {
    tone: "info",
    text: "Link token requested from ABDM. When it arrives on the callback, linking continues on its own — watch the activity log for TOKEN_GENERATED, then LINKED.",
  },
  TOKEN_PENDING: {
    tone: "info",
    text: "A token request is already in flight. ABDM refuses a repeat as a duplicate (ABDM-1092), so nothing new was sent.",
  },
  LINK_REQUESTED: {
    tone: "info",
    text: "Care contexts sent with the stored link token. They show as LINK_REQUESTED until ABDM's on_carecontext callback settles them.",
  },
  NOTHING_TO_LINK: { tone: "info", text: "Every care context is already linked or in flight." },
  NO_ABHA_ADDRESS: {
    tone: "warn",
    text: "This patient is not tagged to an ABHA address. Tag them in the M1 console first.",
  },
  INCOMPLETE_DEMOGRAPHICS: {
    tone: "warn",
    text: "The patient has no birth date or usable name, which generate-token requires.",
  },
  TOKEN_REQUEST_FAILED: {
    tone: "danger",
    text: "ABDM refused the token request. The activity log has its error code.",
  },
  LINK_REQUEST_FAILED: {
    tone: "danger",
    text: "ABDM refused the link call. The care contexts are free to retry; the activity log has the cause.",
  },
};

/** Hints for statuses and ABDM codes seen in the activity log. */
export const STATUS_HINTS: Record<string, string> = {
  "ABDM-1035": "Invalid HIP ID — check ABDM_HIP_ID matches the facility linked to the bridge.",
  "ABDM-2402": "Invalid timestamp — this machine's clock has drifted; resync Windows time.",
  "ABDM-2404": "Invalid request id — a REQUEST-ID was reused.",
  "ABDM-2500": "Authorization header missing — the session token was not sent.",
  "ABDM-1092": "Duplicate token request. The real cause is on the EARLIER failed row for this patient.",
  "ABDM-1038": "The ABHA address does not match the link token.",
  "ABDM-1037": "count does not match the number of care contexts sent.",
  "ABDM-1006": "Bad request body — check the payload against the schema.",
  PATIENT_NOT_FOUND: "No patient matched: the ABHA isn't tagged here, or the mobile + name/gender/year didn't single anyone out.",
  MULTIPLE_PATIENTS_FOUND: "The mobile matched more than one patient with similar demographics.",
  NO_CARE_CONTEXTS: "The patient has no unlinked visits left to offer.",
  OTP_INVALID: "Wrong OTP. The patient has 3 attempts.",
  OTP_EXPIRED: "The OTP is older than 10 minutes.",
  OTP_ATTEMPTS_EXCEEDED: "Three wrong OTPs — the link request is locked; start linking again from the PHR app.",
  REPLY_FAILED: "Our reply to ABDM failed — usually a network problem or a header ABDM rejected.",
  DATA_REQUEST_REFUSED: "We refused to share: no valid consent, or the request is outside its range.",
};
