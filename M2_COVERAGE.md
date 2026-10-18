# M2 coverage against NHA's checklist

Every row of `M2_BUILDING_HIP_WITH_APIS_UPDATED_22_Aug_871c2f7fcd.xlsx`
("Building HIP"), mapped to what exists in this repo as of `ec9dd26`
(merge of `task/M2_v1`).

**Integrator class assumed: private**, as for M1. In this sheet it changes
less than in M1: the Demographic Auth rows (8.x / `HIP_INTI_LINK_50x`) are
mandatory for both Government and Private integrators.

Status values:

| | |
|---|---|
| **done** | implemented and tested |
| **partial** | some code exists, but it does not meet the test case as written |
| **not built** | no implementation |
| **n/a** | does not apply, or happens entirely on the ABDM / PHR-app side |

**Summary: no mandatory test case is met yet.** Of the 12 mandatory rows, 5 are
partial and 7 are not built. The code from `task/M2_v1` is a skeleton:
request models, a transaction log and pass-through proxies. The HIP-side
logic (patient matching, OTP, consent storage, FHIR, encryption and data
push) has not been built.

---

## Blocking defects in the existing M2 code

These have to be fixed before any row below can pass in the sandbox,
whatever else is built.

1. **Wrong host for every outbound call.** `abha/m2_client.py` posts every
   HIE-CM call with `host="abha"`, which resolves to
   `https://abhasbx.abdm.gov.in/abha/api/api/hiecm/...`. The HIE-CM APIs live
   on the gateway host, `https://dev.abdm.gov.in/api/hiecm/...`
   (`config.gateway`). `AbhaClient._url` only knows `abha` and `phr`. The
   unit tests mock the client, so they don't catch this.
2. **Outbound proxies are open relays.** Routes such as
   `POST /api/v3/hiecm/v3/token/generate-token` take `Authorization`,
   `X-HIP-ID` and `X-CM-ID` from whoever calls *our* server and forward them to
   ABDM. `extra_headers` overrides the session token that `AbhaClient` would
   otherwise send. The HIP should take its token from its own session and its
   HIP ID from config. These calls should be internal functions, not public
   routes.
3. **Wrong side of the protocol.** Three proxies call PHR/HIU-side APIs
   that a HIP never calls: `user-initiated-linking/v3/patient/care-context/discover`,
   `.../link/care-context/init` and `.../link/care-context/confirm`. Two
   inbound routes are under `/hiu/...`
   (`/api/v3/hiu/patient/care-context/on-discover`, `.../on-init`). In
   user-initiated linking the gateway calls the HIP, and the HIP replies with
   `on-discover`, `on-init` and `on-confirm`.
4. **The link token is thrown away.** `/api/v3/hip/token/on-generate-token`
   records `TOKEN_GENERATED` but never stores `linkToken`. A token is needed
   for `link/carecontext` and is valid for 6 months per ABHA address, so
   it has to be persisted.
5. **No gateway authentication on callbacks.** Inbound `/api/v3/hip/...`
   routes check only that an `Authorization` header is present. They don't
   verify it as a gateway-issued JWT.

---

## 1 · Health Record Creation

| Test case | M/O | Requirement | Status | Notes |
|---|---|---|---|---|
| Health_RECORD_CREATION_101 | Mandatory | Digital health records in ABDM FHIR format | **not built** | Records exist as `facts` rows in Postgres. Nothing builds ABDM FHIR bundles (OPConsultation, Prescription, DiagnosticReport, etc.). |

## 2–4 · HIP-initiated linking via Mobile OTP / Aadhaar OTP / Direct Auth

All rows optional. All use v0.5 APIs (`users/auth/fetch-modes`, `init`,
`confirm`, `links/link/add-contexts`); the V3 column reads "No APIs". The
sheet requires *any one* of the four HIP-initiated methods, and Demographic
Auth (section 8) is the mandatory one. These are therefore skipped.

| Test case | M/O | Status |
|---|---|---|
| HIP_INTI_LINK_201–206 (Mobile OTP) | Optional | not built |
| HIP_INTI_LINK_301–306 (Aadhaar OTP) | Optional | not built |
| HIP_INTI_LINK_401–405 (Direct Auth) | Optional | not built |

## 4.6–4.10 · Consent grant / revoke / expire

"Mandatory if the integrator is implementing HIP-initiated linking using
Direct Auth". Direct Auth isn't built, so these are **n/a** as test cases.
The consent-notify handler still has to exist, though: section 8 requires
the HIP to check a valid consent before sharing data.

| Test case | Status | Notes |
|---|---|---|
| HIP_INIT_GRANT_CONSENT_ | n/a (needed by 8.1 anyway) | No `/api/v3/consent/request/hip/notify` handler, no consent table. |
| HIP_INIT_REVOKE_CONSENT | n/a | Same handler, status `REVOKED` → delete. |
| HIP_INIT_EXPIRE_CONSENT | n/a | Same handler, status `EXPIRED` → delete. |

## 8 · HIP-initiated linking via Demographic Auth

V3 APIs: `token/generate-token`, `hip/v3/link/carecontext`,
`hip/v3/link/context/notify`, `hip/v3/link/patient/links/sms/notify2`.

| Test case | M/O | Requirement | Status | Notes |
|---|---|---|---|---|
| HIP_INTI_LINK_501 | Mandatory | Provision to link records to ABHA address | **partial** | Proxy route exists; blocking defects 1, 2 and 4 apply. |
| HIP_INTI_LINK_502 | Mandatory | HIP holds name, DOB, gender, mobile | **partial** | `patients` table has demographics. Nothing assembles them into a `generate-token` request from a patient ID. |
| HIP_INTI_LINK_503 | Mandatory | Gateway validates demographics, issues link token | **partial** | Callback route exists, but the token is not stored (defect 4). |
| HIP_INTI_LINK_504 | Mandatory | New linking token created | **partial** | As 503. |
| HIP_INTI_LINK_505 | Mandatory | Link care contexts with token; **pushed from backend as a batch process** | **partial** | Proxy exists. No care contexts are derived from patient records, no batch job, and no `on-carecontext` callback handler. |
| HIP_INTI_LINK_506 | Mandatory | Records pullable in PHR app | **not built** | Depends on section 8 (data transfer). `link/context/notify` is not built. |

## 6 · User-initiated linking

The gateway calls the HIP; the HIP replies. V3 replies: `on-discover`,
`link/care-context/on-init`, `link/care-context/on-confirm`.

| Test case | M/O | Requirement | Status | Notes |
|---|---|---|---|---|
| USER_INIT_LINK_601 | — | Log into PHR app | n/a | PHR side. |
| USER_INIT_LINK_602 | Mandatory | Facility searchable in PHR | n/a in code | Needs the HIP registered in HFR with a public bridge URL. Config, not code. |
| USER_INIT_LINK_603 | Mandatory | HIP matches patient: ABHA address first, then mobile + fuzzy demographic match | **partial** | `/api/v3/hip/patient/care-context/discover` exists but returns `SUCCESS` without searching patients and without calling `on-discover`. |
| USER_INIT_LINK_604 | Mandatory | Matched care contexts returned to PHR | **partial** | Outbound `on-discover` proxy exists (wrong host). Not wired to 603. |
| USER_INIT_LINK_605 | Mandatory | OTP to patient's mobile, then link | **not built** | `/api/v3/hip/link/care-context/init` only logs. No OTP generation or `on-init`. No `/hip/link/care-context/confirm` route, no OTP check, no `on-confirm`. |
| USER_INIT_LINK_606 | Mandatory | CM validates request | n/a | Gateway side. |
| USER_INIT_LINK_607 | Mandatory | Records pullable in PHR app | **not built** | Depends on section 8. |

## 7 · Deep-link SMS notification

| Test case | M/O | Requirement | Status | Notes |
|---|---|---|---|---|
| HIP_INIT_NOTIFY_HIECM | Mandatory | On a new record for a patient with mobile but no ABHA address, call `sms/notify2` | **not built** | No client method, no trigger. Also needs the `on-notify` callback. |

## 8 · Data transfer & share

| Test case | M/O | Requirement | Status | Notes |
|---|---|---|---|---|
| HIP_INIT_SHARE_CARECONTEXT | Mandatory | Receive HI request, verify consent, build FHIR, encrypt for HIU (ECDH / Fidelius), push to `dataPushUrl`, call `health-information/notify`, all within 2 h | **not built** | No consent store, no `/api/v3/hip/health-information/request` handler, no `on-request`, no FHIR, no encryption, no push, no notify. |

---

## What exists and is tested

`tests/test_m2_discovery_response.py` (8 tests, mocked client) covers the
`on-discover` proxy's header checks, error mapping and payload forwarding.
`abdm_transactions` and `abdm_care_contexts` tables plus `backend/abdm_store.py`
give a usable transaction log and care-context store.
