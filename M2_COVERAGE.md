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

**Status after stage 4 (branch `m2/hip-fixes`): every mandatory test case
has an implementation, tested end to end against a simulated gateway and HIU.**
None has run against the real ABDM sandbox yet. That needs a HIP ID, which we've
requested from NHA (see `M2_SANDBOX_ONBOARDING.local.md`, not in git), and a public
bridge URL. Open items before certification:

1. **Live sandbox run** of every row below, once the HIP ID and bridge URL exist.
2. **OTP over SMS.** It's written to the server log today (`ABDM_OTP_DELIVERY=log`).
   Needs DLT registration plus an SMS provider.
3. **NRCeS FHIR validator pass** on the generated bundles.
4. **The `gateway` role check** on callback JWTs, to be confirmed on the first real
   callback. A refusal logs the token's roles.

**Original summary (at merge): no mandatory test case was met.** Of the 12
mandatory rows, 5 were partial and 7 were not built. The code from `task/M2_v1` is a skeleton:
request models, a transaction log and pass-through proxies. The HIP-side
logic (patient matching, OTP, consent storage, FHIR, encryption and data
push) has not been built.

---

## Blocking defects in the existing M2 code

**All five fixed on branch `m2/hip-fixes` (stage 1).** What changed:

| # | Fix |
|---|---|
| 1 | `AbhaClient` gained a `gateway` host (`config.gateway_url`). Every M2 call now goes to `https://dev.abdm.gov.in/api/hiecm/...`. |
| 2 | Relay routes removed. The app now calls `POST /api/m2/link-token` and `POST /api/m2/link-care-contexts`, which take only the body. Session token, `X-HIP-ID` (`ABDM_HIP_ID`), `X-CM-ID` and `REQUEST-ID` all come from the server. |
| 3 | The PHR/HIU-side routes, their client methods and their models are removed. The client now has the HIP's replies: `on_discover`, `on_init` and `on_confirm`. |
| 4 | `on-generate-token` correlates on `response.requestId` (the id we sent), not the gateway's own `REQUEST-ID` header. It stores the token in `abdm_link_tokens`, which expires after 6 months. A token for a request we never made is refused. `link-care-contexts` sends it as `X-LINK-TOKEN`. |
| 5 | Every `/api/v3/hip/*` route requires an RS256 JWT verified against the gateway JWKS (`/gateway/v0.5/certs`) with issuer `central-registry`, plus the `gateway` realm role, and `X-HIP-ID` must be ours. Verified live: a real sandbox integrator token passes the signature check but is refused for lacking the role. **The role name has not yet been confirmed on a real gateway callback**; a refusal logs the token's roles. |

The original findings, for the record:

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
| Health_RECORD_CREATION_101 | Mandatory | Digital health records in ABDM FHIR format | **done (mock)** | `backend/m2_fhir.py`: each encounter becomes an OPConsultRecord document Bundle (NRCeS profiles) holding Condition, MedicationRequest, Procedure, Observation, Immunization, AllergyIntolerance and DocumentReference resources, with the corpus' own SNOMED/LOINC/RxNorm/CVX codes. **Not yet run through the NRCeS validator.** |

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
| HIP_INIT_GRANT_CONSENT_ | **done (mock)** | `/api/v3/consent/request/hip/notify` stores the artefact in `abdm_consents` (ours only) and acknowledges on `consent/v3/request/hip/on-notify`. |
| HIP_INIT_REVOKE_CONSENT | **done (mock)** | `REVOKED` deletes the artefact, so nothing is shared under it afterwards (tested). |
| HIP_INIT_EXPIRE_CONSENT | **done (mock)** | `EXPIRED`/`DENIED` delete too, and a consent past `dataEraseAt` is refused at request time. |

## 8 · HIP-initiated linking via Demographic Auth

V3 APIs: `token/generate-token`, `hip/v3/link/carecontext`,
`hip/v3/link/context/notify`, `hip/v3/link/patient/links/sms/notify2`.

| Test case | M/O | Requirement | Status | Notes |
|---|---|---|---|---|
| HIP_INTI_LINK_501 | Mandatory | Provision to link records to ABHA address | **done (mock)** | `backend/m2_hip_linking.py`. `POST /api/m2/patients/{id}/link` for one patient, `POST /api/m2/link-batch` or `scripts/m2_link_batch.py` for everyone tagged to an ABHA address. |
| HIP_INTI_LINK_502 | Mandatory | HIP holds name, DOB, gender, mobile | **done (mock)** | Built from `patients`: Synthea's digits are stripped from the name (the schema allows letters only), gender is mapped to `M/F/O/U`, year of birth comes from `birth_date`, and `abhaNumber` is sent as a 14-digit number. |
| HIP_INTI_LINK_503 | Mandatory | Gateway validates demographics, issues link token | **done (mock)** | The token request is recorded against the patient *before* sending. A pending request isn't repeated for 10 min, to avoid ABDM's `ABDM-1092` duplicate refusal hiding the first refusal's real cause. |
| HIP_INTI_LINK_504 | Mandatory | New linking token created | **done (mock)** | `on-generate-token` correlates on `response.requestId` and stores the token for 6 months. |
| HIP_INTI_LINK_505 | Mandatory | Link care contexts with token; **pushed from backend as a batch process** | **done (mock)** | The token callback links that patient immediately, with no click. Unlinked care contexts are sent 50 per call with `X-LINK-TOKEN` and marked `LINK_REQUESTED` before the call. `/api/v3/link/on_carecontext` settles them as `LINKED`/`LINK_FAILED`, and failures are retried by the next batch. |
| HIP_INTI_LINK_506 | Mandatory | Records pullable in PHR app | **done (mock)** | Linking plus section 8 data transfer. `link/context/notify` (+ `/api/v3/links/context/on-notify`) is also built: `POST /api/m2/care-contexts/notify`. |

## 6 · User-initiated linking

The gateway calls the HIP; the HIP replies. V3 replies: `on-discover`,
`link/care-context/on-init`, `link/care-context/on-confirm`.

| Test case | M/O | Requirement | Status | Notes |
|---|---|---|---|---|
| USER_INIT_LINK_601 | — | Log into PHR app | n/a | PHR side. |
| USER_INIT_LINK_602 | Mandatory | Facility searchable in PHR | n/a in code | Needs the HIP registered in HFR with a public bridge URL. Config, not code. |
| USER_INIT_LINK_603 | Mandatory | HIP matches patient: ABHA address first, then mobile + fuzzy demographic match | **done (mock)** | `m2_service.match_patient`: ABHA address, then ABHA number (M1 link store), then verified mobile (`patient_mobiles`, set via `PUT /api/m2/patients/{id}/mobile`) plus exact gender and year of birth and name similarity ≥ 0.8. Exactly one patient must match. |
| USER_INIT_LINK_604 | Mandatory | Matched care contexts returned to PHR | **done (mock)** | One care context per encounter, newest first, already-linked ones excluded, all `OPConsultation`. `on-discover` is sent as a background task. The largest corpus patient has 394 care contexts in one reply, which may need capping if the PHR app struggles. |
| USER_INIT_LINK_605 | Mandatory | OTP to patient's mobile, then link | **partial** | init checks the care contexts were offered, issues a 6-digit OTP (hashed, 10 min, 3 attempts, single use) and replies with `on-init`. confirm verifies the OTP, records `LINKED` and replies with `on-confirm`. **The OTP is written to the server log** (`ABDM_OTP_DELIVERY=log`). A real SMS provider is needed before certification. |
| USER_INIT_LINK_606 | Mandatory | CM validates request | n/a | Gateway side. |
| USER_INIT_LINK_607 | Mandatory | Records pullable in PHR app | **done (mock)** | Linking plus section 8 data transfer. |

Stage 2 is tested end to end against `kravos_test` with a fake gateway
(`tests/test_m2_user_linking.py`). It hasn't run against the real sandbox,
which needs a registered HIP and a bridge URL pointing at this server.

**Checked against NHA's own M2 OpenAPI spec and catalogue**
([nha-in/docs](https://github.com/nha-in/docs), `catalogue/hiecm/openapi/v3/hiecm-m2.yaml`):
- A refusal goes to the gateway as `ABDM-9999` with a readable message. NHA publishes no
  code for "no match" or "wrong OTP", and the sandbox accepts 9999. The specific reason
  (`PATIENT_NOT_FOUND`, `OTP_INVALID`, …) stays on our transaction log.
- `on-discover` carries `X-HIU-ID` (our facility id), as the spec requires.
- confirm sends the OTP as a JSON *number*, so it's read as text and zero-padded back to 6
  digits (an OTP of `012345` arrives as `12345`).
- Callbacks answer 202 (discover) or 200 (everything else), per the spec.

## 7 · Deep-link SMS notification

| Test case | M/O | Requirement | Status | Notes |
|---|---|---|---|---|
| HIP_INIT_NOTIFY_HIECM | Mandatory | On a new record for a patient with mobile but no ABHA address, call `sms/notify2` | **done (mock)** | The batch texts every patient with a mobile (`patient_mobiles`) and no ABHA tag, once per new set of care contexts (`abdm_sms_notifications`). A failed send is retried. `/api/v3/patients/sms/on-notify` records ABDM's acceptance, which means accepted, not delivered. |

## 8 · Data transfer & share

| Test case | M/O | Requirement | Status | Notes |
|---|---|---|---|---|
| HIP_INIT_SHARE_CARECONTEXT | Mandatory | Receive HI request, verify consent, build FHIR, encrypt for HIU (ECDH / Fidelius), push to `dataPushUrl`, call `health-information/notify`, all within 2 h | **done (mock)** | `backend/m2_data_flow.py`. Checks the consent is ours, granted and not erased; clips to the consent's date range and HI types and to linked contexts; acknowledges on `on-request`; one bundle per page with fresh key material, MD5 checksum of the plaintext and our gateway token; reports per care context on `health-information/notify`. Runs immediately as a background task. |

**Encryption** (`abha/fidelius.py`): BouncyCastle's Weierstrass `curve25519`
ECDH, nonce XOR → HKDF-SHA256 salt and AES-256-GCM IV. Implemented directly,
because `cryptography`'s X25519 is a different curve form and doesn't
interoperate. **Proven byte for byte** against the Fidelius reference CLI's
published key pairs and ciphertext (`tests/test_fidelius.py`). Our public key
goes back in whichever encoding (raw or X.509) the HIU sent, and off-curve
keys are refused.

---

## Testing console (frontend `/m2`)

The NHA rows are executed by a person comparing what happens against the
sheet's "Expected Result" column, as with M1 (`/m1`). The M2 console groups
the rows into six screens: setup, records, HIP-initiated linking, PHR
linking, SMS, and consent and sharing. Each screen names the rows it's
evidence for, and verdicts are kept per tester in localStorage.

Most of M2 is ABDM calling *us*, so every screen ends with a live **ABDM
activity** log (`abdm_transactions`, refreshed every 4 s), with a hint
attached to every known error code. Sections are linkable (`/m2#hip`).
Read routes: `backend/m2_console_routes.py`. They never return a link
token, OTP or unmasked ABHA number/mobile.

Works today without a HIP ID: the setup checks (including a live read of
our bridge registration), the FHIR preview for any visit, the readiness
checks, and mobile capture.

## Tests

| File | Covers |
|---|---|
| `tests/test_gateway_auth.py` | Callback JWT: RS256/RS512, alg bound to key, issuer, `gateway` role, expiry |
| `tests/test_m2_routes.py` | Every route: server-side identity, auth on every callback, correlation, status codes |
| `tests/test_m2_user_linking.py` | Stage 2 end to end on `kravos_test`: matching, OTP rules, linking |
| `tests/test_m2_hip_linking.py` | Stage 3 on `kravos_test`: token → link, chunking, retry, notify, SMS, batch |
| `tests/test_fidelius.py` | Encryption against the reference CLI's vectors; round trips; bad keys |
| `tests/test_m2_console_routes.py` | Console reads: masking, status merging, FHIR preview, bridge-URL validation |
| `tests/test_m2_data_flow.py` | Stage 4 on `kravos_test`: consent store/revoke; a fake HIU decrypts each pushed page and checks checksum and FHIR; refusal paths |
