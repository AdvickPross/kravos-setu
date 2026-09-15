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
→ FastAPI → Next.js UI. **439 tests passing.** An optional LLM prose-polish
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

A **gold evaluation set** now exists (`data/eval_set.jsonl`, 100 queries
with gold `fact_id` answers), so precision and recall are quotable for the
first time — see [**Evaluation**](#evaluation) below.

Full build history and every design decision behind it:
[`kravos_hackathon_progress.md`](kravos_hackathon_progress.md). Path B
(a separate, more ambitious Postgres/router/intent-classifier track) is
paused, not abandoned — see [`PATH_B_STATUS.md`](PATH_B_STATUS.md).

## Repo layout

```
abha/         ABDM ABHA V3 identity integration (M1): session, crypto, enrolment, login, profile
retrieval/    Query-side NER (parse_query() -> ParsedQuery) and the Postgres schema (db.py)
backend/      FastAPI app: data layer (Postgres), retrieval, missingness, brief templating,
              LLM polish, vector search, conversational RAG (conversation.py, groq_client.py)
frontend/     Next.js 16 UI
scripts/      One-off/regenerable data pipeline scripts (see below)
data/         Lexicon synonyms, query/category cues, the pre-op missingness checklist, the gold eval set
tests/        pytest suite for retrieval/, backend/ and abha/ -- requires Postgres, see Setup
demo/         Curated demo patients and rehearsed queries
eval/         Generated evaluation report (scripts/run_eval.py)
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
verifying one. (M2 is facility-to-facility record flow, M3 is consent; both
sit on top of this and are not built.) Implemented from the *ABHA V3 APIs
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

- **M2 and M3** (facility-to-facility record flow, and the consent layer
  on top of it). Both route through the same Gateway pattern and will
  reuse the bottom half of `abha/` — config, session, transport, crypto —
  which is why those are separated from the flow modules.
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
