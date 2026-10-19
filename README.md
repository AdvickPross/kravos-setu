# Kravos Setu

An AI clinical-context layer over Indian health records (ABDM/NDHM, FHIR
R4). It answers a doctor's natural-language question about a patient from
that patient's records, and — the actual differentiator — flags what
*should* be on file for a pre-operative assessment but isn't. Not "AI
summarizes records"; "AI tells you what's missing."

Wedge: pre-operative assessment — what an anaesthetist needs to know before
surgery.

## Status

The full pipeline works end to end and has been verified in a real browser,
not just by its test suite: `parse_query()` → `retrieve()` → `build_brief()`
→ FastAPI → Next.js UI. **684 tests passing** (3 OCR regression tests
currently fail; they predate the M2 work). An optional LLM prose-polish
pass and Pinecone vector search over free-text notes are built and wired in
behind feature flags, both off by default.

**Fact storage is Postgres now, not per-patient JSON files.**
`backend/data_layer.py` queries Postgres directly (`facts`, `patients`
tables); `scripts/load_facts.py` loads the corpus with a `COPY`-based
streamed loader (never reads the 701 MB file into memory), verified against
the real corpus: 1,083,231 facts, 952 patients, exact counts. See
[**Setup**](#setup) below. `facts_by_patient/` still exists on disk (used by
`scripts/index_reports.py`'s default input and the eval-set builder) but is
no longer what the running app reads.

**A conversational RAG endpoint exists**: `POST /api/chat` layers
conversation memory and a Groq-generated reply on top of the same NER and
retrieval `/api/query` uses — see
[**Conversational RAG**](#conversational-rag-apichat) below. No frontend
thread UI yet; this is API-only for now.

**Milestone 1 (ABDM identity) is built and verified against the live
sandbox.** Every Mandatory item on NHA's M1 checklist that applies to a
private integrator is implemented at the API layer; 23/23 endpoints are
reachable and all five scope tables were confirmed against
`abhasbx.abdm.gov.in`. Row-by-row status, including what is *not* done, is in
[`M1_COVERAGE.md`](M1_COVERAGE.md). Code lives in `abha/`, exposed at
`/api/abha/*` — see [**ABHA / ABDM (M1)**](#abha--abdm-m1).

**Milestone 2 (Building HIP) is built and tested end to end against a
simulated gateway and HIU, but not yet against the live sandbox.** It covers
HIP-initiated and user-initiated linking, the deep-link SMS, consent, FHIR
records and encrypted data transfer. Live testing is blocked on a HIP ID
from NHA. Row-by-row status is in [`M2_COVERAGE.md`](M2_COVERAGE.md), and the
testing console is at `/m2` — see [**ABDM M2 (Building HIP)**](#abdm-m2-building-hip).

A **gold evaluation set** now exists (`data/eval_set.jsonl`, 100 queries
with gold `fact_id` answers), so precision and recall are quotable for the
first time — see [**Evaluation**](#evaluation) below.

Full build history and every design decision behind it:
[`kravos_hackathon_progress.md`](kravos_hackathon_progress.md). Path B
(a separate, more ambitious Postgres/router/intent-classifier track) is
paused, not abandoned — see [`PATH_B_STATUS.md`](PATH_B_STATUS.md).

## Repo layout

```
abha/         ABDM integration: M1 identity (session, crypto, enrolment, login, profile) and the
              M2 HIE-CM client, gateway-callback auth and Fidelius encryption
pipeline/     Modular 5-model medical document intelligence pipeline & edge orchestration
models/       Base adapter, ModelManager (LRU/dynamic eviction), and 5 model adapters
config/       Pipeline stage configs (pipeline.json) and Jetson edge policy (policy.json)
retrieval/    Query-side NER (parse_query() -> ParsedQuery) and the Postgres schema (db.py)
backend/      FastAPI app: data layer (Postgres), retrieval, missingness, brief templating,
              LLM polish, vector search, conversational RAG, document intelligence endpoints,
              and the ABDM M2 HIP (linking, consent, FHIR, data transfer)
frontend/     Next.js 16 UI
scripts/      One-off/regenerable data pipeline scripts & ocr_ingest.py CLI
data/         Lexicon synonyms, query/category cues, the pre-op missingness checklist, gold eval set
tests/        pytest suite for retrieval/, backend/, abha/, and 5-model pipeline/models
demo/         Curated demo patients, rehearsed queries, and document scans
eval/         Generated evaluation reports, pipeline benchmarks, and ocr_eval.py
```

## Prerequisites

- Python 3.11+ (developed against 3.14)
- Node.js 22+ / npm
- Docker (Postgres runs in it — see Setup)
- **The corpus data — not in this repo.** `facts.jsonl` (~700 MB) and
  `patients.jsonl` are gitignored: too large for GitHub, and regenerable
  from Synthea FHIR bundles via `extract.py`/`batch.py` if you have the
  bundles. **If you don't have the pipeline output already, get
  `facts.jsonl` from whoever set this repo up** — it isn't fetchable any
  other way from a fresh clone. `patients.jsonl` is small enough that it
  **is** committed, so you only need to source `facts.jsonl` itself.

## Setup

```pwsh
# Python deps
pip install -r requirements.txt

# Postgres, in Docker -- port 5434 (see docker-compose.yml for why not
# the usual 5432/5433).
docker compose up -d
docker compose ps   # wait for "healthy"

# Place facts.jsonl at the repo root (see Prerequisites above), then load
# both it and patients.jsonl into Postgres. Idempotent by default
# (truncates and reloads); pass --keep-existing to upsert instead.
python scripts/load_facts.py
# expect: facts: 1,083,231, patients: 952

# Optional, only if you also want scripts/index_reports.py's default input
# or the eval-set builder's facts_by_patient/*.jsonl files on disk -- the
# running app itself reads Postgres, not these.
python scripts/split_facts.py

# Frontend deps
cd frontend
npm install
cd ..
```

Optional: copy `.env.example` to `.env` if you want the LLM polish pass,
Pinecone vector search, or the conversational `/api/chat` endpoint live (all
work correctly, and the templated/single-shot app works fully, with none of
them configured — see **Optional features** below).

## Running it

Two terminals, from the repo root:

```pwsh
# Terminal 1 -- backend, port 8000
python -m uvicorn backend.main:app --port 8000
```

```pwsh
# If the UI loads but no patients appear, the browser is resolving
# "localhost" to IPv6 ::1 while uvicorn listens on IPv4 only. Pin it:
#   frontend/.env.local  ->  NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
# (.env.local is gitignored, so each machine sets its own.)

# Terminal 2 -- frontend, port 3000
cd frontend
npm run dev
```

Open http://localhost:3000. Try the queries in
[`demo/queries.md`](demo/queries.md) — each is paired with a real patient
and a note on what it demonstrates (a real allergy on file, a genuine
missingness gap, medication status history, comorbid conditions with a
clean lab trend).

## Testing

Requires the Postgres container running (`docker compose up -d`) --
`tests/conftest.py` creates and schema-migrates a separate `kravos_test`
database on first run, and every isolated test writes there, never to the
real corpus in `kravos`. `test_eval.py` and `test_abha_routes.py`
deliberately read the real `kravos` database instead, so a full pass also
exercises retrieval against the actual loaded corpus.

```pwsh
python -m pytest tests/ -q
```

Frontend: `cd frontend; npm run build` (also runs the TypeScript check)
and `npm run lint`.

## Evaluation

```pwsh
python scripts/build_eval_set.py   # regenerate data/eval_set.jsonl from the corpus
python scripts/run_eval.py         # score the pipeline -> eval/report.md
```

`data/eval_set.jsonl` is 100 queries across 16 archetypes, each carrying the
`fact_id`s a correct answer must contain. **Gold is derived on a path that
never touches `parse_query()` or `retrieve()`** — clinical intent is
hand-specified as `(categories, codes, window)` clauses and resolved against
the corpus, because scoring gold produced by the code under test would return
1.0 by construction and measure nothing. A test enforces this structurally by
walking the builder's AST for banned imports and calls.

Two-tier relevance: `required` drives recall, `allowed` is neither rewarded
nor penalised (asking "is he allergic to penicillin" legitimately surfaces
"prescribed penicillin V in 2017"; scoring that as a false positive would
score a judgement call as a bug).

**Read recall, not precision.** Retrieval is a strict `category` + `code IN
(...)` filter, so it can only return facts whose codes the parser already
produced — a false positive needs the parser to resolve a span to a
confidently *wrong* code set, which is far rarer than resolving it to nothing.

The set currently scores 1.000/1.000, which means **it is saturated and has
stopped discriminating** — a regression would show as a drop, but no
improvement can show at all. That is a signal to add harder queries, not a
number to quote, and `run_eval.py` prints the warning into the report itself.

## ABHA / ABDM (M1)

Milestone 1 of the ABDM integration is **Identities**: creating an ABHA and
verifying one. (M2, the HIP side of record linking and sharing, sits on top of
this — see the next section. M3, the HIU side, is not built.) Implemented from the *ABHA V3 APIs
Integrator Guide v1.4* against the sandbox.

```
abha/config.py      three hosts per environment, credentials from env
abha/errors.py      typed failures; annotates K-547 with its real cause
abha/transport.py   REQUEST-ID / TIMESTAMP, retry policy, PII redaction
abha/crypto.py      RSA-OAEP-SHA1 + the cached public certificate
abha/session.py     client_credentials token, cached and refreshed
abha/client.py      composes the above; the object every flow takes
abha/endpoints.py   every ABDM path M1 touches, in one table
abha/enrollment.py  Aadhaar OTP enrolment, mobile attach, ABHA address
abha/login.py       three login credentials, multi-account selection, Find ABHA
abha/phr.py         ABHA-*address* verification -- a separate family, separate host
abha/otp_policy.py  resend limits (max 2, 60s), enforced server-side
abha/faceauth.py    QR + capturePID polling
abha/biometrics.py  the wadh digest
backend/abha_link.py     one ABHA <-> one patient ID (the mandatory non-API item)
backend/abha_routes.py   25 routes under /api/abha
```

### Signing in

`http://localhost:3000` opens on **Sign in with ABHA** — ABHA number, mobile,
Aadhaar, or ABHA address (which runs against the PHR host, so it is a separate
tab rather than a fourth option). After the OTP it shows the ABHA profile, then
resolves the ABHA to a patient record through `backend/abha_link.py` and opens
the clinical view on it. A returning patient whose ABHA is already tagged goes
straight through.

The identity lives in `sessionStorage` and dies with the tab — this is meant to
run on a shared registration terminal, where `localStorage` would leave one
patient's identity waiting for the next person. The transaction token is held
in memory only and never written to storage at all.

**Sign-in is skippable, visibly.** The corpus is 952 synthetic Synthea
patients and none of them holds a real ABHA, so a hard gate would make the
product impossible to demonstrate. The escape hatch marks the session
`demo: true` and every screen after it carries a "demo — no ABHA verified"
badge.

### The M1 test console

`http://localhost:3000/m1` walks NHA's checklist by hand. Each step names the
test-case IDs it is evidence for, shows the request and response that produced
it, and records a pass/fail next to the row — which is what functional testing
against the sheet actually is. Aadhaar numbers and OTPs are masked in the
evidence panel so a screenshot is safe to put in a certification pack.

The tagging section works with no ABDM credentials at all; the rest needs them.

### Running it

Put sandbox credentials in `.env` (see `.env.example`; they are never
committed), then:

```pwsh
python scripts/abha_smoke.py                 # session + certificate + encrypt
python scripts/abha_probe.py                 # every M1 endpoint: exists? auth ok?
python scripts/abha_probe.py --contract      # scope/otpSystem matrices, live
python scripts/abha_smoke.py --enrol 999999999999 --mobile 9999999999
```

`abha_probe.py` sends only non-existent identifiers, so a well-formed request
reaches the account lookup and comes back "User not found" — which is the
*pass* signal. It spends no creation quota and sends no OTP to anyone.

The no-argument run consumes no quota. The sandbox allows **100 ABHA
creations per client id**, deleted one at a time through a web portal, so
`--enrol` is opt-in and interactive — ABDM sends a real OTP to a real phone.

With no credentials set, `/api/abha/*` answers **503 with a message naming
`.env`**, and the rest of the API is unaffected. `GET /api/abha/status` says
whether the flows are available without calling ABDM at all, so the frontend
can ask before offering them.

### What the tests do and don't prove

There is no ABDM sandbox in CI and there will not be one: credentials are
per-integrator, creations are rate-limited, and the OTPs go to a real phone.
So **the 121 ABHA tests do not prove ABDM accepts these calls.** They prove
the half that is ours — every assertion is made against the bytes that would
have gone out, with the real client, transport, session and crypto running
and only the socket replaced (`tests/abha_support.py`). The fake holds the
RSA private key, so "was this field encrypted, and with what" is checked by
decrypting it rather than assumed.

`scripts/abha_smoke.py` is the other half. Run it before claiming M1 works.

### Traps this implementation handles

Each of these is a documented ABDM behaviour that fails silently or reports
the wrong cause, and each has a test pinning it:

- **A successful enrolment can save `mobile` as null.** If the supplied
  number is not the Aadhaar-linked one, ABDM returns HTTP 200 with a
  complete-looking profile and no mobile. The ABHA is real but has no contact
  route, so every later OTP login on it fails with a message about the
  account. `EnrolmentResult.mobile_is_linked` makes it checkable and the flow
  logs a warning.
- **`K-547` means the biometric `lr` flag was not `'Y'`** — nothing in ABDM's
  message says so. `abha/errors.py` appends the explanation.
- **The padding is OAEP with SHA-1, not SHA-256.** SHA-256 produces
  ciphertext of identical length that ABDM cannot decrypt, with no local
  symptom.
- **Three hosts, not one.** Gateway, ABHA and PHR differ by environment *and*
  from each other; production swaps the ABHA path segments rather than
  changing a prefix.
- **Login is not always one step.** Six ABHA numbers per mobile are allowed,
  so `login/verify` can return a choice instead of a session. Branching on
  `len(accounts)` is wrong — a single-account response still lists one.
- **ABHA *address* is not a fourth login credential.** It looks like one and
  the first implementation here treated it as one; the sandbox answers
  `{"scope": "Invalid Scope", "loginHint": "Invalid Login Hint"}`. It is a
  separate family on the PHR host (`abha/phr.py`), and NHA's checklist says
  so — VRFY_ABHA_102/202 list `/v3/phr/web/login/…` paths where the
  ABHA-number rows list `/v3/profile/login/…`.
- **Two patient records against one ABHA splits a history in half.** Nothing
  in ABDM prevents it; the checklist puts the uniqueness guarantee on the
  integrator (`backend/abha_link.py`).
- **`otpSystem` flips between `aadhaar` and `abdm`** in a way that does not
  follow from the credential. ABHA-number login uses the UIDAI OTP system.
- **Two time formats in one request**: ISO-8601 with `Z` in the header,
  `YYYY-MM-DD HH:MM:SS` inside the OTP auth block.

## ABDM M2 (Building HIP)

Milestone 2 makes Kravos Setu a **Health Information Provider**. A patient's
visits can be linked to their ABHA, by us or by the patient from ABDM's PHR
app, and then shared with a requester under a consent the patient granted.
The spec is NHA's *Building HIP* checklist
(`M2_BUILDING_HIP_WITH_APIS_UPDATED_22_Aug`). Paths, bodies and status codes
were checked against NHA's own M2 OpenAPI spec and catalogue
([nha-in/docs](https://github.com/nha-in/docs)).

```
abha/m2_client.py             every HIP -> gateway call; identity from server config only
abha/m2_models.py             request/callback models (abhaNumber as a 14-digit number, OTP as text)
abha/gateway_auth.py          verifies the gateway JWT on every callback (RS256/RS512, `gateway` role)
abha/fidelius.py              ABDM's ECDH on BouncyCastle's Weierstrass curve25519 + HKDF + AES-GCM
backend/m2_service.py         user-initiated linking: patient matching, OTP, on-discover/init/confirm
backend/m2_hip_linking.py     HIP-initiated linking, the linking batch, context notify, deep-link SMS
backend/m2_fhir.py            one OPConsultRecord FHIR document bundle per visit (NRCeS profiles)
backend/m2_data_flow.py       consent store/delete; health-information request -> encrypt -> push -> notify
backend/m2_routes.py          /api/m2/* (our app) and /api/v3/* (ABDM's callbacks)
backend/m2_console_routes.py  read routes for the /m2 console (no secrets returned)
backend/abdm_store.py         transactions, care contexts, link tokens, consents, transfers
```

**Two kinds of route.** `/api/m2/*` is called by our own app, and every ABDM
header (session token, `X-HIP-ID`, `X-CM-ID`, `REQUEST-ID`) comes from server
config, so a caller can never act as the HIP. `/api/v3/hip/*` and the other
callback paths are called by ABDM, relative to the bridge URL. Each one
requires a JWT signed by the gateway, addressed to our HIP ID. ABDM is
asynchronous: a call returns 202 and the answer arrives on a callback
carrying our `REQUEST-ID` as `response.requestId`, which keys
`abdm_transactions`.

**A care context is a visit** (an `encounter_id`), shared as `OPConsultation`.

### The M2 test console

`http://localhost:3000/m2` follows the shape of the M1 console. It has six
screens mapped to NHA's rows: sandbox setup, health records, HIP-initiated
linking, PHR linking, deep-link SMS, and consent and data transfer. Each
step names its test-case IDs and keeps a pass/fail verdict. Most of M2 is
ABDM calling *us*, so every screen ends with a **live ABDM activity log**
that gives a hint for each known error code. That log is the evidence for
most rows. Sections are linkable (`/m2#hip`).

These work today without a HIP ID: the setup checks (including a live read
of our bridge registration), the FHIR preview of any visit, mobile capture,
and the readiness checks.

### Running it

Sandbox setup needs three things beyond the M1 credentials: a **HIP ID**
linked to our bridge (`ABDM_HIP_ID`), a **public URL** for the bridge
(e.g. an ngrok static domain to port 8000, set from the console's setup
screen), and, for certification, a **DLT-registered SMS sender** for the
linking OTP. Until then `ABDM_OTP_DELIVERY=log` writes the OTP to the API
console. See `.env.example` for `ABDM_HIP_ID`, `ABDM_HIP_NAME`,
`ABDM_VERIFY_GATEWAY_TOKENS` and `ABDM_OTP_DELIVERY`.

```pwsh
python scripts/m2_link_batch.py   # link every ABHA-tagged patient's visits; deep-link SMS the rest
```

The batch is what HIP_INTI_LINK_505 means by "pushed from the backend". It
requests a link token where none is stored, and the token callback then
links that patient with no further action. Schedule it to run unattended.

On test day, the console's activity log (or `abdm_transactions`) shows each
exchange. Expect `DISCOVERY_REQUEST → DISCOVERY_RESPONSE_SENT`,
`LINK_TOKEN_GENERATION → TOKEN_GENERATED`, `LINK_CARE_CONTEXT → LINKED`,
`CONSENT_GRANTED → CONSENT_STORED`, `HEALTH_INFORMATION_REQUEST → TRANSFERRED`.
If nothing arrives at all, ABDM can't reach the bridge URL.

### What the tests do and don't prove

The M2 suites (`tests/test_m2_*.py`, `tests/test_gateway_auth.py`,
`tests/test_fidelius.py`) run every flow end to end against Postgres with a
fake gateway, and in the data-transfer tests a fake HIU decrypts every
pushed page. Encryption matches the Fidelius reference CLI's published
keys and ciphertext **byte for byte**. They don't prove ABDM accepts the
calls. That needs the live run, and `M2_COVERAGE.md` lists what is still
open: the live run, SMS delivery, an NRCeS validator pass on the FHIR, and
confirming the gateway-role check on a real callback.

### Traps this implementation handles

- **Callback tokens are RS512 as well as RS256**, and any integrator's own
  session token verifies against the same realm. Signature and issuer
  aren't enough; the `gateway` role is required.
- **ABDM's "curve25519" is BouncyCastle's Weierstrass form**, not X25519.
  Python's X25519 produces keys and secrets ABDM can't use.
- **The confirm OTP arrives as a JSON number**, so `012345` becomes `12345`.
- **`abhaNumber` is a number and `name` is letters only** in generate-token.
  Synthea's digit-suffixed names are refused, so they are stripped.
- **A refused token request is remembered.** An identical retry within
  roughly 70 s gets `ABDM-1092` "duplicate", which hides the real cause, so
  token requests aren't repeated while one is pending.
- **No published error code for "no match" or "wrong OTP".** The gateway
  gets `ABDM-9999` plus a message, and the specific reason stays in our log.

## Conversational RAG (`/api/chat`)

`backend/conversation.py`, behind `POST /api/chat`. Same NER
(`parse_query()`) and retrieval (`retrieve()`) as `/api/query` — nothing
about how facts get found is different — plus two things that endpoint
doesn't have:

- **Conversation memory, persisted to Postgres.** `conversations` and
  `conversation_turns` (schema in `retrieval/db.py`). Pass no
  `conversation_id` to start a thread; the response returns one to pass
  back on the next turn. Threads survive process restarts and are keyed by
  `(patient_id, conversation_id)` — there is no clinician login in this
  system (ABHA sign-in identifies the *patient*, not the doctor asking
  questions), so a conversation is not attributed to a clinician.
- **A model that can actually converse about what was found**, instead of
  the fixed template. Groq (`backend/groq_client.py`), not Anthropic — this
  is a separate path from `llm_polish.py`'s single-shot brief rewrite,
  which stays as it was. Same graceful-degradation contract as every other
  optional feature here: no key, a timeout, a bad response, and the
  endpoint still answers, using the grounded template text instead of
  failing the request.

  **Model: `qwen/qwen3.8-27b`, checked against a real `/openai/v1/models`
  call, not assumed.** `llama-3.1-8b-instant` — the obvious pick, and Groq's
  long-standing default in most examples — no longer exists on Groq's
  catalog; asking for it 404s. The other candidate, `openai/gpt-oss-20b`, is
  a reasoning model that spends part of `max_tokens` on a hidden reasoning
  pass before the visible answer: a 20-token budget came back completely
  empty (`finish_reason: "length"`, the whole budget spent on reasoning).
  Usable with `reasoning_effort: "low"` and a larger budget, but
  `qwen3.8-27b` answers directly with no such tuning, which fits a
  conversational endpoint meant to reply quickly. Override with `GROQ_MODEL`
  if a different model fits better later — check it against
  `/openai/v1/models` first, this catalog moves.

**The vector store stops being a one-shot fallback that gets discarded.**
`/api/query`'s `retrieve()` only attempts Pinecone when *zero* entities
resolve, and then only for that one request. In a conversation, a
narrative-shaped follow-up — "why was that started", "any notes on that" —
very often comes *after* something already resolved structurally, so
`backend/conversation.py` also searches Pinecone whenever the message reads
as narrative-shaped, merging the hits into the same `NarrativeMatch` list
the model is shown. Verified live: a resolved MEDICATION entity plus "why
is she on lisinopril?" pulls 5 narrative chunks and the model answers from
them, honestly saying the indication isn't documented rather than guessing.

Request: `{"patient_id": ..., "conversation_id": null, "message": "..."}`.
Response includes `answer` (the conversational reply), `cited_fact_ids`
(every fact and narrative chunk actually shown to the model this turn —
provenance attached to what was surfaced, not parsed from the model's
output), and the same `result` shape `/api/query` returns.

Missingness (`compute_missing`) is included in the model's context only on
a conversation's first turn — it's a property of the patient record at the
time the thread opened, not something worth restating every follow-up.

No router, no intent classifier: turn handling is a small set of explicit
rules (`_looks_narrative`'s cue words), not a separate module.

## Medical Document Intelligence Pipeline (5 Models)

The document processing subsystem provides a modular clinical document intelligence pipeline replacing legacy monolithic OCR. It is designed for clinical documents (discharge summaries, prescriptions, lab reports, referral notes) and architected with a **laptop-first, hardware-agnostic execution engine** supporting CPU fallback, automatic NVIDIA CUDA GPU acceleration when available, and zero eviction for instant warm inference across multi-page documents.

### The 5 Models

1. **Restormer** (`models/restormer_adapter.py`): Transformer-based photographic restoration for degraded clinical scans. Executed conditionally only when quality assessment detects blur, noise, skew, or uneven illumination. Uses `torch.inference_mode()` and mixed precision (FP16) on CUDA; falls back gracefully to bilateral filtering, unsharp masking, and contrast normalization when neural weights are unmounted.
2. **RT-DETR** (`models/rtdetr_adapter.py`): Real-time transformer object detector for document layout and region segmentation. Identifies clinical document regions: `header`, `paragraph`, `table`, `signature`, `stamp`, `barcode`, `footer`, `roi`.
3. **LayoutLMv3** (`models/layoutlmv3_adapter.py`): Multi-modal layout understanding. Establishes multi-column reading order and hierarchical relationships (e.g. section header -> child lines) without re-detecting bounding boxes.
4. **PP-OCR v4** (`models/ppocr_adapter.py`): High-throughput printed text detection and recognition using ONNX Runtime. Queries active execution providers (`CUDAExecutionProvider` or `CPUExecutionProvider`) and preserves canonical bounding box coordinates.
5. **TrOCR** (`models/trocr_adapter.py`): Vision-encoder-decoder transformer specialized for handwritten clinical prescriptions, annotations, and sign-offs. Emits calibrated confidence scores.

### Pipeline Architecture & Stages

```
Input Scan (PDF / Image)
  │
  ▼
[Stage 1: Ingestion & Validation] ─── MIME validation, PyMuPDF 250 DPI contiguous rasterization
  │
  ▼
[Stage 2: Quality Assessment]    ─── Laplacian blur, Hough deskew, contrast metrics
  │                                   │
  ├─── (Quality Pass) ───────────────┼─── (Quality Fail: blur/noise/skew)
  │                                   ▼
  │                             [Stage 3: Conditional Enhancement]
  │                               Restormer (CUDA FP16/CPU FP32) / Classical CV
  ▼                                   │
[Stage 4: Region Detection] ◄─────────┘
  RT-DETR semantic region classification
  │
  ▼
[Stage 5: Layout Understanding]
  LayoutLMv3 spatial topological sorting & hierarchy linking
  │
  ▼
[Stage 6: ROI Classification & Routing]
  Gradient entropy + edge density classification
  ├─── Printed (> 0.65)   ───► PP-OCR (ONNX)
  ├─── Handwritten (> 0.65) ─► TrOCR (Inference Mode / Autocast)
  └─── Mixed / Ambiguous  ───► Dual-Engine Fusion (confidence-weighted)
  │
  ▼
[Stage 7: Clinical Normalization]
  Lexicon surface lookup, RxNorm, SNOMED CT, LOINC resolution
  │
  ▼
[Stage 8: Structuring & Provenance]
  Bounding box attribution, stage-minimum confidence propagation
  │
  ▼
[Stage 9: Clinical Validation]
  Dosage checks, date sanity, unit validation, contradiction rules
  │
  ▼
[Stage 10: Persistence / Review]
  Structured PipelineResult with truthful metadata & reviewable JSON
```

### Laptop-First Runtime & Hardware Profiles

The pipeline decouples hardware-independent stages from execution policies via declarative profiles in `config/profiles/`:

- **Laptop Profile (`config/profiles/laptop.json` - Active Default)**:
  - `device`: `"auto"` (automatically selects NVIDIA CUDA GPU when present, otherwise safe CPU fallback)
  - `precision`: `"auto"` (`fp16` on CUDA for 2-3x speedup, `fp32` on CPU for numerical stability)
  - `max_resident_models`: `5` (keeps all pipeline models resident in RAM/VRAM to eliminate reloading overhead across documents)
  - `enable_eviction`: `false` (LRU eviction disabled)
  - `idle_timeout_seconds`: `1800` (30 minutes)
  - `cpu_threads`: `"auto"` (dynamically sets PyTorch & OpenCV threads up to `min(8, os.cpu_count())`)
  - `pdf_dpi`: `250` (optimized rasterization resolution balancing OCR fidelity and memory)
- **Jetson Profile (`config/profiles/jetson.json` - Future Roadmap)**:
  - Preserves memory-constrained edge deployment parameters (`max_resident_models = 2`, `enable_eviction = true`, `idle_timeout_seconds = 300`) without modifying pipeline code.

#### Configuration Precedence
1. **Hardcoded Defaults** (safe fallbacks in `config/loader.py`)
2. **Profile Configuration** (`config/profiles/<profile>.json`)
3. **Environment Variables** (`KRAVOS_DEFAULT_PROFILE`, `KRAVOS_DEVICE`, `KRAVOS_PRECISION`, `KRAVOS_MAX_RESIDENT_MODELS`, `KRAVOS_ENABLE_EVICTION`, `KRAVOS_PDF_DPI`)
4. **Runtime Constructor / CLI Arguments** (`--profile`, `--device`, `--dpi`)

### Runtime Diagnostics CLI

Inspect the active runtime environment, PyTorch CUDA availability, ONNX Runtime execution providers, active hardware profile, process memory (RSS), and registered model truthfulness:

```pwsh
# Formatted console summary
python scripts/ocr_system_info.py

# Machine-readable JSON output
python scripts/ocr_system_info.py --json
```

### Document Ingestion & Diagnostic CLI

```pwsh
# Run full end-to-end diagnostic and inspection report on any medical document
python -m scripts.test_ocr_pipeline demo/scans/discharge_summary.png

# Diagnose uploaded prescription image with verbose OCR line printing and save JSON artifact
python -m scripts.test_ocr_pipeline <path_to_image> --verbose --save

# Process a document with 5-model pipeline (laptop profile)
python scripts/ocr_ingest.py demo/scans/discharge_summary.png --patient <patient_id>

# Ingest with explicit device, profile, and DPI overrides
python scripts/ocr_ingest.py demo/scans/discharge_summary.png --patient <patient_id> --profile laptop --device auto --dpi 250

# Commit extracted facts directly to Postgres
python scripts/ocr_ingest.py demo/scans/discharge_summary.png --patient <patient_id> --commit

# Run legacy single-model RapidOCR engine for baseline comparison
python scripts/ocr_ingest.py demo/scans/discharge_summary.png --patient <patient_id> --legacy

# Extract text directly from born-digital PDF text layer
python scripts/ocr_ingest.py document.pdf --patient <patient_id> --text-layer
```

### Comparative Benchmarking & Evaluation

Benchmark **Legacy RapidOCR** vs the **5-Model Document Intelligence Pipeline**, measuring cold-start latency, warm latency (P50, P95), Character Error Rate (CER), Word Error Rate (WER), and process RSS / CUDA VRAM:

```pwsh
# Run OCR benchmark test suite
python -m pytest tests/test_ocr_benchmark.py -v

# Run full comparative benchmark (outputs eval/pipeline_benchmark.json)
python eval/ocr_eval.py --runs 3 --device auto --profile laptop

# Benchmark only the 5-model pipeline
python eval/ocr_eval.py --runs 3 --pipeline-only
```

### HTTP Endpoints

- `POST /api/ocr/extract`: Multipart upload (`file`, required `patientId`, optional `documentDate`). Proposes lexicon-coded facts (dry run).
- `POST /api/ocr/commit`: Accepts approved clinical facts and atomically commits them into Postgres with full provenance tracing.
- `POST /api/ocr/analyze`: Full structured document extraction returning patient demographics, provider info, prescription line-items with dosages/schedules, lab test tables, and OCR lines.
- `POST /api/ocr/quality`: Pre-screening endpoint returning page-level blur, skew, brightness, and contrast metrics without OCR overhead.
- `GET /api/ocr/models`: Returns runtime status, device, precision, and version for all registered model adapters.
- `GET /api/ocr/health`: Health status and model readiness.


## Optional features (all off by default, all degrade safely)

| Feature | Env var | What it needs |
|---|---|---|
| LLM prose polish (`backend/llm_polish.py`) | `KRAVOS_LLM_POLISH_ENABLED=true` | `ANTHROPIC_API_KEY` |
| Vector search over clinical notes (`backend/vector_store.py`) | — (auto-detected) | `PINECONE_API_KEY`, then run `python scripts/index_reports.py --limit 20` as a smoke test before the full ~58,000-fact index |
| Conversational RAG (`backend/conversation.py`, `POST /api/chat`) | — (auto-detected) | `GROQ_API_KEY`; reuses whatever `PINECONE_API_KEY` is already indexed |

None of these are required for the app to work — the templated brief and
coded retrieval, now Postgres-backed, are the whole product; everything
here is additive.

## Known data-corpus quirks

Worth knowing before you extend or demo this — both discovered by directly
querying the corpus, documented in [`demo/queries.md`](demo/queries.md):

- No fact anywhere in the corpus has `status: "stopped"` or
  `"discontinued"` — only `final`, `active`, `resolved`, `completed`.
- Every allergy in the corpus has `criticality: low` (880/880). The UI renders
  `high` in danger tone regardless, so real data cannot hide one.
- ~98 surface forms reach more than one code. Most are intended
  (`acetaminophen` → 10 RxNorm products is the ingredient rollup working), but
  a few are cross-specimen: `glucose`, `hemoglobin` and `leukocytes` each
  reach a blood analyte *and* a urine dipstick, because `normalize_loinc()`
  strips the specimen phrase from both displays. The curated
  `data/synonyms.csv` concepts (`blood glucose`, `hemoglobin`) resolve to the
  blood-only code sets; the bare analyte deliberately still spans all of them.
- ~~The NER doesn't resolve the bare word "hypertension"~~ — **fixed.** The
  root cause was that a concept's own name was never indexed as a surface
  form, which silently broke six concepts. Found by the gold eval set.

## What's deliberately not built yet

- **M3 (the HIU side)**: requesting consent and receiving records from
  other facilities. M2's gateway client, callback auth and
  `abha/fidelius.py` (which already decrypts) are the parts it would reuse.
- **M2's optional HIP-initiated linking methods** (Mobile OTP, Aadhaar OTP,
  Direct Auth). NHA's sheet needs one method, and Demographic Auth, the
  mandatory one, is built.
- **The remaining ABHA V3 sections.** M1 covers enrolment, login, profile,
  QR and card. Driving-licence and demographic enrolment, fingerprint/iris
  capture, re-KYC, delete/deactivate/reactivate, Forgot ABHA and the
  Benefit/Child APIs are not built; `abha/endpoints.py` lists the paths.
- **Harder eval archetypes.** The gold set exists now but is saturated (see
  **Evaluation**). Worth adding: queries mixing several categories, misspelled
  drug names, trend-shaped answers, and the narrative REPORT path, which no
  archetype touches yet. `tests/queries.md` remains a debugging instrument and
  still claims no accuracy number.
- **Drug-class rollups** ("is he on antiplatelets?"). `beta blockers`,
  `ACE inhibitors`, etc. resolve `UNRESOLVED` on purpose — an honest miss
  beats a class list that's silently out of date.
- **A frontend for `/api/chat`.** The conversational endpoint is API-only —
  no thread UI in `frontend/` yet. The single-shot `/api/query` UI is
  unaffected and unchanged.
- **Intent classification and a query router.** `/api/chat`'s turn handling
  is a couple of explicit rules, not a classifier; Path B's more ambitious
  version (`retrieval/intent.py`, `retrieval/router.py`, `RetrievalPlan`) is
  still just schema and Docker config, dormant (see `PATH_B_STATUS.md`).
