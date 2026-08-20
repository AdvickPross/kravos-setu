# Kravos Setu — hackathon build log

Running log for `kravos_hackathon_full_build_prompt.md`. Newest entry at the
bottom; one entry per major change. Plain language on purpose — this is the file
to read when picking the work back up.

**Repo root is `D:\Repos\kravos-setu`**, not the `E:\Kravos_Setu\db` the prompt
mentions. All paths below are relative to the real root.

---

## Where things stood before this build started

- **Ingestion** — done and untouched (`extract.py`, `batch.py`, `facts.jsonl`
  with 1,083,231 facts, `patients.jsonl` with 952 patients, `lexicon.csv`).
- **Query-side NER** — done, 97 tests green. `parse_query()` is the entrypoint.
- **Path B (Postgres, intent classifier, router)** — started, then explicitly
  superseded by this prompt: no intent classifier, no router, no
  `RetrievalPlan`, and the data layer is per-patient JSON for now. Three files
  survive from it and are **dormant, not deleted**: `docker-compose.yml`,
  `data/db.json`, `retrieval/db.py`. They already match the Postgres shape this
  prompt asks about in its final step (port 5434, so no clash with the 5433
  instance), so they are worth keeping. Nothing imports them.
  See `kravos_path_b_progress.md` for that thread.

---

## 2026-08-20 — Step 1: the Pydantic contracts

**Changed:** added `backend/__init__.py` and `backend/models.py`. Nothing else
touched. `retrieval/` untouched, as required. Tests still 97 green.

**What is in there.** Four primary models plus the supporting types they need:

- `Fact` — mirrors the JSONL schema exactly, provenance fields included.
- `PatientSummary` — mirrors `patients.jsonl`, with the underscore keys
  (`_n_facts`, `_source_file`) aliased to clean names.
- `RetrievalResult` — facts grouped per entity, unresolved spans, the date
  window applied, missing items, per-stage timings.
- `Brief` — titled sections of lines, each line carrying its `fact_id`s, plus a
  separate `missing` list.
- Supporting: `EntityFacts`, `MissingItem`, `StageTimings`, `BriefSection`,
  `BriefLine`, `QueryRequest`, `QueryResponse`, and three enums
  (`RetrievalOutcome`, `MissingReason`, `BriefSource`).

**Decisions made in this step, and why:**

1. **`ResolvedEntity` and `ParsedQuery` are imported from `retrieval.models`,
   not re-declared.** Re-declaring them would create two truths about the same
   shape and quietly drift.
2. **`Fact.value` is `str | None`, not a number.** Measured across the corpus:
   populated values always arrive as strings (`"91.3"`). Casting belongs at the
   point of use.
3. **`EntityFacts.outcome`** (`found` / `none_on_file` / `not_retrieved`) is the
   mechanism that keeps "this patient has no allergy records" separate from
   "nothing in the query resolved". The prompt calls this out as crucial and it
   needed a field rather than an inference from an empty list.
4. **Facts are grouped per entity, not merged into one pile**, because entities
   can share a span — `aspirin` is an ALLERGY substance *and* four MEDICATION
   products, which are two different clinical answers.
5. **`EntityFacts.coverage_note` is the reserved seat for drug-class rollups.**
   When classes land, "class list covers N agents, reviewed <date>" attaches
   there with no restructuring. `code_count` is a property specifically so that
   nothing downstream assumes a small code set — a class carries 30+ as
   naturally as glucose carries 4.
6. **`Brief.prose` never replaces `Brief.sections`.** The LLM polish pass adds a
   field and flips `source`; the grounded template always survives, so the demo
   works with the flag off.
7. **`StageTimings` has explicit fields**, including `vector_ms` and `llm_ms`
   reserved as `None` — timings are structured response fields, not logs.
8. **`MissingItem.reason`** separates `absent` (never recorded) from `stale`
   (recorded, but outside the recency window), because "no potassium ever" and
   "no potassium in 90 days" are different things to tell an anaesthetist.

**Verified, not assumed:** a real row from `facts.jsonl` and a real row from
`patients.jsonl` both parse into the models; a full `QueryResponse` containing a
live `parse_query()` result serialises to JSON and round-trips back.

**Stopped here on purpose.** The prompt requires the models to be reviewed
before anything is built on them.

**Next up (steps 2–4):** `scripts/split_facts.py` (streamed, idempotent, assert
1,083,231), then `backend/data_layer.py` + tests, then `backend/retrieve.py`
with the missingness check + tests.

---

## 2026-08-20 — Steps 2–4: split, data layer, retrieval + missingness

**Changed:** `scripts/split_facts.py` (new, run), `facts_by_patient/` (952
per-patient `.jsonl` files + `_index.json`, generated, not committed source),
`backend/data_layer.py` (new), `backend/retrieve.py` (new),
`data/expected_facts.json` (new), `tests/test_data_layer.py` (11 tests),
`tests/test_retrieve.py` (14 tests). `retrieval/` untouched. All 122 tests
green (97 original + 25 new).

**Step 2 — the split.** facts.jsonl turned out to be contiguous by
`patient_id` (verified: 952 id-transitions for 952 distinct patients before
writing any code), so the splitter holds one output handle open at a time
instead of 952 simultaneously-open files. Idempotent by clearing
`facts_by_patient/` at the start of every run rather than trying to merge
with a stale prior split. Ran it: **1,083,231 facts -> 952 files**, assertion
passed, `_index.json` counts sum back to the total.

**Step 3 — `backend/data_layer.py`.** Implements the three functions from
the prompt's fixed signature exactly (`get_patient_facts`, `list_patients`,
`get_patient`). Two module-level caches, both lazy: per-patient facts
(populated on first access, not preloaded at import) and the full
`patients.jsonl` (952 rows, small enough to load whole and cache once).
Ranking is most-recent-first with undated facts sorted last (implemented via
a tz-aware `datetime.min` sentinel, since comparing `None` against a
tz-aware date raises). **Safety rule implemented literally**: when
`categories` includes `ALLERGY`, `limit` is ignored regardless of what was
passed. Unknown `patient_id` returns `[]` from `get_patient_facts` (no file
on disk is a legitimate outcome for a filtered query) but `get_patient()`
raises `KeyError` (an unknown patient id at the summary lookup is a caller
bug, not a data fact). Tests are isolated from the real corpus via a
`tmp_path` + `monkeypatch` fixture — deterministic, and don't depend on
`facts_by_patient/` existing on disk.

**Step 4 — `backend/retrieve.py`.**

- Loops `parsed.entities`; only `EXACT`/`SYNONYM` quality entities get
  queried. `FUZZY`/`UNRESOLVED` go straight to `unresolved_spans` — no
  router in this pass to push them anywhere else, per the prompt.
- Negated entities are retrieved anyway and flagged (`negated_in_query`),
  never dropped.
- Overlapping entities (aspirin -> ALLERGY + MEDICATION at the same span)
  naturally produce two separate `EntityFacts`, since the loop is per-entity
  and each keeps its own list.
- No `limit` is passed from `retrieve()` — the prompt's ALLERGY-ignores-limit
  rule lives in `data_layer`; nothing here needed a default cap, so one
  wasn't invented.
- **Missingness** (`compute_missing`): reads `data/expected_facts.json`, a
  6-item pre-op checklist — renal function, electrolytes, CBC, allergy
  documentation, current medications, cardiac history. Every code set in
  that file is copied verbatim from existing `data/synonyms.csv` concept
  rows (renal function tests, electrolytes, hemoglobin/hematocrit/
  leukocytes/platelets, ischemic heart disease/MI/heart failure/AF) — no new
  codes were invented for this. `window_days: 90` on the three lab items is
  a placeholder clinical default, flagged as such in the file's own
  `_comment`, not a measured value. Two-item category-only checks (allergy
  docs, current meds) have `codes: []` and `window_days: null`, meaning
  "any fact in this category counts, no recency requirement." Each item
  becomes `ABSENT` (nothing on file) or `STALE` (on file, but outside the
  window) — never both, and never silently skipped.
- `StageTimings.retrieve_ms` populated on every call, using
  `time.perf_counter()` around the whole function body (instrumented from
  this first commit, not retrofitted).

**Verified, not assumed:** ran the real `parse_query()` output for
`"is he on aspirin, any allergies, and what was his last glucose?"` through
`retrieve()` against the real split corpus for one real patient — 4 entities
resolved (aspirin x2, allergies, glucose), all `FOUND_NONE` for this
particular (child) patient, 5 missingness items surfaced, `retrieve_ms`
~2.8ms.

**Not done in this pass:** no check yet that `facts_by_patient/` is excluded
from whatever the repo's ignore rules are — it's 687MB of generated data,
should not be committed if this becomes a git repo. Flag before any commit.

**Next up (step 5):** `backend/brief.py` — template rendering only, no LLM
polish yet.

---

## 2026-08-20 — Step 5: `backend/brief.py`

**Changed:** `backend/brief.py` (new), `tests/test_brief.py` (10 tests). All
132 tests green (122 + 10).

Template-only, deterministic, no LLM. Facts are grouped by `entity.category`
across all of a result's `EntityFacts`, deduped by `fact_id`, sorted
most-recent-first per section, then sections are ordered by a fixed clinical
priority list (Allergies, Medications, Conditions, Labs, Vitals, Procedures,
Immunizations, Reports) rather than input order, so the brief reads the same
regardless of what order the query happened to mention things in.

Line format is one function for every category: `"{display} — {value_unit}
— {status date}"`, each segment included only if present. Checked against
the prompt's own example — a stopped-medication fact renders as
`"clopidogrel 75 MG Oral Tablet — stopped 2016-03-05"`, matching
`"Clopidogrel 75mg — stopped 2016-03-05"` in shape. Allergies get an
explicit `"No allergy records on file."` when empty (a clinical statement,
not the generic empty-section phrasing other categories get).

`Brief.missing` is `result.missing` passed straight through — no
transformation, since `MissingItem` is already the right shape and the
prompt says this section must not be buried.

Console gotcha, not a bug: printing a brief's em-dash headline through
`python -c "..."` in this Git Bash produced a `<20>` replacement character.
Verified with a raw byte read (`\xe2\x80\x94`) that the actual string is
correct UTF-8 — it's this terminal's stdout encoding, not the code. Noting
it so a future pass doesn't chase a phantom encoding bug.

**Not built here, deliberately:** the LLM polish pass (step 9) and
`Brief.prose` are untouched -- `source` stays `TEMPLATE` and `prose` stays
`None` from every `build_brief()` call in this pass.

**Next up (step 6):** `backend/main.py` -- FastAPI routes
(`/api/patients`, `/api/patients/{id}`, `POST /api/query`), CORS for the
Next.js dev origin, full per-stage timings assembled across parse / retrieve
/ brief.

---

## 2026-08-20 — Interrupt: Path B pause, documented separately

User interrupted mid-research (grepping for `parse_query`'s signature, no
file touched) to have Path B's state documented before switching attention
to this hackathon track. Wrote `PATH_B_STATUS.md` at the repo root: Path B
component table (schema/compose done, everything else — loader, intent,
router, sql_store, instrumentation — not started), confirmed no
`kravos-postgres` container has ever been created, confirmed
`retrieval/intent.py`/`router.py`/`sql_store.py` don't exist on disk, and
recorded the 132-passed test count as of that point. No Path B file was
touched this session before or after; the interrupt's premise (that Path B
work was in progress) didn't match what was actually happening, which
turned out to be this hackathon track the whole time. Also fixed an
arithmetic typo in this log's step-5 entry (said 142, the real number was
132). Resumed on "continue".

---

## 2026-08-20 — Step 6: `backend/main.py`

**Changed:** `backend/main.py` (new), `tests/test_main.py` (7 tests),
`requirements.txt` (added `fastapi`, `uvicorn[standard]`, `httpx` — httpx is
what `fastapi.testclient.TestClient` runs on). All 139 tests green (132 +
7).

Three routes, matching the prompt's list exactly: `GET /api/patients`
(optional `?q=` substring filter on name, case-insensitive), `GET
/api/patients/{id}` (404 via `HTTPException` when `data_layer.get_patient`
raises `KeyError`), `POST /api/query`. CORS restricted to
`http://localhost:3000` (the Next.js dev origin) rather than left open.

`api_query` is the only place that assembles `StageTimings` end to end —
wraps `parse_query()` and `build_brief()` each in their own
`time.perf_counter()` pair, takes `retrieve_ms` from what `retrieve()`
already measured internally, sums all three into `total_ms`. Route handlers
otherwise do no logic of their own: fetch patient, parse, retrieve, brief,
assemble response. `QueryResponse` (already defined in `backend/models.py`
at step 1) is returned directly as the route's return type, so FastAPI
derives the response schema from the same Pydantic model instead of a
separate declaration.

**Verified against the real corpus, not just the isolated tests:** ran
`uvicorn backend.main:app` in the background, hit `GET /api/patients`
(returned all 952) and `POST /api/query` for a real patient with a query
mixing an ALLERGY term and a bare word ("medications") that has no lexicon
entry of its own — confirmed the bare word doesn't spuriously resolve
(expected: there's no intent classifier in this track, so an unresolved
generic word just doesn't produce an entity), confirmed timings arrive
non-null (`parse_ms` ~54ms cold on first call, since that's when the
lexicon/cue files load once into the process-level `lru_cache`d
`QueryNER`). Killed the process afterward; nothing left running.

**Noted, not fixed:** `fastapi.testclient` currently emits a
`StarletteDeprecationWarning` about `httpx` being deprecated in favor of an
`httpx2` package that doesn't have a stable release yet. Cosmetic, from a
third-party library's own transitional churn, not something to chase now.

**Next up (step 7):** `frontend/` — Next.js 16, App Router, TypeScript,
Tailwind. Patient picker, query box, brief view with entity highlighting by
match quality, a visually distinct missingness section, fact provenance on
click, latency footer. TypeScript types generated from the Pydantic models
rather than hand-written.

---

## 2026-08-20 — Step 7: `frontend/` — Next.js 16

**Changed:** `frontend/` (new — Next.js 16.3.1, App Router, TypeScript,
Tailwind v4), `scripts/export_openapi.py` (new), `openapi.json` (generated,
repo root). No backend files touched; all 139 pytest tests still green.

**Types generated, not hand-written, per the prompt.** `scripts/export_openapi.py`
imports `backend.main.app` and dumps `app.openapi()` — that schema comes
straight from the Pydantic models via FastAPI, nothing re-declared. `npx
openapi-typescript openapi.json -o frontend/src/lib/api-types.ts` turns it
into TS types; `frontend/src/lib/api.ts` re-exports the ones components use
and wraps the three fetch calls. One consequence worth flagging: every
Pydantic field with a `default_factory` (all the list/object fields —
`entities`, `facts`, `sections`, `missing`, `timings`, etc.) is *absent* from
OpenAPI's `required` list, even though the JSON response always includes it
with its default value. openapi-typescript therefore types those fields as
`T | undefined`, so consuming code needs `?? []` / `?? {}` guards even
though the field is never actually missing on the wire — a mismatch between
Pydantic's serialization behavior and JSON Schema's "required" semantics,
not a bug in the generator or the types.

**Component layout:** `PatientPicker` (debounced search), `QueryBox`,
`ParsedQueryView` (entity highlighting), `BriefView` (sections +
click-to-expand provenance), `MissingSection`, `LatencyFooter`, orchestrated
from `app/page.tsx`.

**Entity highlighting groups by exact `(start, end)` offset**, not by
entity — the prompt's own example (`aspirin` -> ALLERGY substance +
MEDICATION products at identical offsets) is handled by merging same-offset
entities into one visual `<mark>`, coloured by the *worst* match quality
present in the group, with a hover tooltip listing every entity in the
group individually (category, quality, negation). A single merged
highlight rather than stacked overlapping spans — simpler to render
correctly, and confirmed to work in the browser check below.

**`PatientSummary.age_years` doesn't reach the frontend.** It's a plain
`@property` on the Pydantic model (see `backend/models.py`), and Pydantic
v2 only serializes `@computed_field`-decorated properties, not plain ones —
so it's absent from both the JSON response and the OpenAPI schema. Rather
than change the backend model for a display nicety, the frontend computes
age itself from `birth_date` (`frontend/src/lib/format.ts:ageFromBirthDate`).
`brief.py` still uses the real `patient.age_years` property server-side
for the headline, which works fine in Python — this is purely an
HTTP-boundary gap, not a bug in the model.

**Environment quirk hit and worked around, not fixed:** partway through,
`frontend/node_modules/` was found missing entirely even though `npm
install` had completed earlier in the session and `package-lock.json` was
present and current — something in this sandbox appears to prune
`node_modules` between tool calls under some condition not yet understood.
Re-ran `npm install` (40s, clean) and continued; flagging in case it
recurs, since it'll look like a mystery "module not found" otherwise.

**Verified in an actual browser, not just build/lint/typecheck.** No
project-level run skill existed for this repo yet, so followed the "run"
skill's browser-driven fallback pattern: installed Playwright + Chromium
into the scratch directory (kept out of the repo — this was a one-off
verification tool, not a project dependency), started both dev servers in
the background (`uvicorn backend.main:app --port 8000`, `npm run dev` on
3000), and drove the app with a real headless-Chromium script rather than
just reading rendered HTML.

- `npm run build` and `./node_modules/.bin/tsc --noEmit` both clean; `npm
  run lint` caught one real issue — `PatientPicker`'s effect called
  `setLoading(true)` synchronously in the effect body (new
  `react-hooks/set-state-in-effect` rule), fixed by moving it inside the
  debounce's `setTimeout` callback.
- First browser pass looked broken (submitting a query fired no
  `/api/query` request at all) — turned out to be a **test-script bug, not
  an app bug**: `page.click('button:has-text("Ask"))` is a substring match,
  and it was matching a patient list button for someone named *Jaskolski*
  (contains "ask") before ever reaching the real Ask button. Switched to
  `getByRole("button", { name: "Ask", exact: true })` and the real flow
  worked immediately.
- Confirmed end-to-end for two real patients: entity highlighting renders
  with correct colours and a working legend (exact/synonym/fuzzy/unresolved
  + negated), the missingness box is visually distinct (amber border) and
  phrases `absent` vs `stale` correctly ("never recorded" vs "last seen
  <date> (expected within N days)"), brief sections render with correct
  most-recent-first ordering and inline status+date
  (`"Ibuprofen 400 MG Oral Tablet [Ibu] — active 2026-04-23"`), clicking a
  brief line expands a provenance panel with `fact_id` / source type /
  resource id / encounter / date, and the latency footer shows real
  sub-3ms warm-cache timings. Zero console errors, zero failed requests,
  across all runs.
- Killed both dev server processes afterward (`Get-NetTCPConnection` on
  3000/8000 -> `Stop-Process`); nothing left running. Removed the throwaway
  log files. No project run-skill was written from this — it was a single
  verification pass, not a repeatable driver the task called for.

**Not done in this pass:** no frontend test suite (not requested by the
prompt, which specifies pytest coverage for the backend only). Dark mode
was not manually eyeballed, only implemented via Tailwind's
`dark:` classes following the scaffold's existing convention.

**Next up (step 8):** `demo/` curation — pick 3-5 demo patients (verify no
multi-code collisions on the demo queries, at least one with a stopped
medication for status variation, one with a genuine missingness gap),
`demo/queries.md`.

---

## 2026-08-20 — Step 8: `demo/` curation

**Changed:** `demo/queries.md` (new). No code changed; all 139 tests still
green.

Picked 4 patients (prompt asks 3-5) by querying `facts_by_patient/`
directly for the properties the prompt asks to verify, not by guessing:

1. **Hyo54 Coral377 Krajcik437** — one of only 174 patients (of 952) with
   any ALLERGY fact at all, and one of 32 with the specific aspirin allergy
   (code `1191`) — picked specifically so the demo has a genuine on-file
   allergy instead of another "no records" case. Also demonstrates
   overlapping entities cleanly: "aspirin" resolves to both an ALLERGY and
   a MEDICATION entity at the same span, and she's allergic but not
   prescribed it, so the two sections correctly diverge.
2. **Abby752 Elenora790 Braun514** — reused from earlier smoke testing; a
   healthy child patient where every entity resolves but almost nothing is
   on file, so the missingness box does the actual work.
3. **Abdul218 Bennie663 Schroeder447** — reused from the browser
   verification pass; ibuprofen/lisinopril refilled yearly back to 2016,
   alternating `active` (most recent) and `completed` (every prior fill).
4. **Lynwood354 Al123 Feest103** — comorbid diabetes + hypertension, and a
   glucose lab history hand-checked to confirm all 12 readings share one
   LOINC code before picking him (glucose resolves to 4 codes corpus-wide;
   this is the demo where that could have silently mixed blood draws with
   urine dipstick results, and didn't).

**Two findings surfaced by the curation process itself, not incidental:**

- **`status` never contains `stopped`, `discontinued`, or
  `entered-in-error` anywhere in the full 1,083,231-fact corpus** — checked
  directly this time (not the 200k-record prefix flagged as unverified in
  `PATH_B_STATUS.md`). Medications only ever carry `active` or `completed`.
  This closes Path B's open question #2 about the `status` vocabulary;
  updated `PATH_B_STATUS.md` to record it instead of leaving it marked
  unverified. The prompt's demo requirement ("a stopped medication") isn't
  satisfiable with this data — documented the substitution (`active` vs
  `completed` across repeat fills) in `demo/queries.md` rather than
  quietly picking a different phrase and hoping it reads the same.
- **"hypertension" alone resolves `UNRESOLVED`; "high blood pressure"**
  resolves correctly to the same SNOMED code (`59621000`, `synonym`
  quality) — a `data/synonyms.csv` surface-form gap, confirmed patient-
  independent by testing `QueryNER` directly outside any patient context.
  `retrieval/` is out of scope for this pass so this isn't fixed here, but
  it's recorded in `demo/queries.md` so query phrasing at demo time doesn't
  hit it live. "diabetes" has no equivalent gap.

Also caught, live-checked, and documented rather than left as a hedge: does
patient #2 (the missingness demo) have any MEDICATION fact at all, which
would change whether "current medications" shows up in her missingness
list? She has one — a pediatric fluoride gel, clinically irrelevant to what
the demo query asks about but enough to satisfy the checklist's
category-only presence check. Noted in `demo/queries.md` as a real,
honest limitation of how coarse that check is, worth saying out loud during
the demo rather than hoping nobody notices.

**Next up (step 9):** LLM polish pass behind a feature flag — isolated,
must degrade gracefully, template output must survive unchanged when the
flag is off or the call fails.

---

## 2026-08-20 — Step 9: LLM polish pass

**Changed:** `backend/llm_polish.py` (new), `backend/main.py` (wires it in,
adds `llm_ms` to the assembled timings), `.env.example` (new, repo root),
`requirements.txt` (added `anthropic`), `tests/test_llm_polish.py` (12
tests), `tests/test_main.py` (+1 test). All 159 tests green.

Loaded the `claude-api` skill before writing this, since the prompt
specifies "an LLM" without naming a provider and the environment is
Anthropic's own tooling — used `claude-opus-5` per the skill's default
("always use Opus 5 unless the user names a different model"), with
`output_config: {"effort": "low"}` since restating already-retrieved facts
as prose is a genuinely simple task, not one that benefits from more
reasoning depth.

**One function, one flag, everything else falls through it.**
`polish_brief(brief) -> Brief` in `backend/llm_polish.py` is the entire
surface: checks `KRAVOS_LLM_POLISH_ENABLED` (truthy strings:
`1/true/yes/on`, case-insensitive; anything else including unset is off),
and on any of {flag off, `anthropic` package not installed, no API key,
network error, timeout, empty response} returns the **same input `Brief`
object**, untouched. Only on an actual successful, non-empty response does
it return a *new* Brief via `model_copy(update=...)` — original object
never mutated, `sections`/`missing` copied through exactly as
`build_brief()` produced them, only `prose` and `source` change. This is
what "must be given only the retrieved facts, must add nothing" turns into
mechanically: the model only ever sees a plain-text digest built from the
`Brief` object's own sections/lines/missing-items (`_render_facts_digest`),
never the raw fact list, never anything else. The system prompt repeats the
"do not add/infer/round" constraints explicitly since that's the only
enforcement mechanism available without building the groundedness checker
the spec says not to build.

**`anthropic` imported lazily inside `_generate_prose`**, not at module
top — same pattern `retrieval/db.py` already uses for `psycopg`, so
`backend/llm_polish.py` (and everything that imports it, including
`backend/main.py`) stays importable with the package absent or the key
unset. This is load-bearing for "the demo must be fully functional with
the LLM path disabled": nothing downstream needs to know or care whether
the package is installed.

**`StageTimings.llm_ms` is `None`, not `0`, whenever the polish stage
didn't actually run** (flag off, or it ran and failed) — only set when
`brief.source` comes back `LLM_POLISHED`, consistent with the model's own
documented contract ("a stage that did not run stays `None`"). `total_ms`
only adds the LLM elapsed time when that stage genuinely ran.

**Deliberately did not make a live call to the Anthropic API in this
session** — every test and the manual smoke check exercises the flag-off
path (`KRAVOS_LLM_POLISH_ENABLED` unset in this environment) or mocks
`_generate_prose` directly; a real API call spends the user's separate
Anthropic API credits, not something to do without being asked. What *is*
verified end-to-end without a live call: flag-off returns the identical
object; a mocked failure (including simulating the package being
uninstalled via `ModuleNotFoundError`) returns the identical object; a
mocked success returns a new object with `prose` set, `source` flipped,
and `sections`/`missing` byte-for-byte unchanged; a real `POST /api/query`
through `TestClient` returns `llm_ms: null` and `source: "template"` with
the flag at its real default (unset). The one thing not verified is
`_generate_prose` itself against the live API — that's what a user running
this with a real key and the flag on would exercise first.

**Not built, per the spec:** no NLI groundedness checker. No retry logic
beyond the client's own defaults — a single failed attempt just falls back,
which is the correct behavior for a best-effort polish layer sitting in
front of a request that must stay fast.

**Stage 1 is now feature-complete per the prompt's order of work (steps
1-9).** Remaining: step 10's explicit stop-and-confirm gate before stage 2
(Pinecone), and the final reminder to prompt about the Postgres migration.

---

## 2026-08-20/21 — Step 10 checkpoint, then stage 2 (Pinecone)

Stopped at the prompt's step-10 gate and reported stage 1 as demo-ready,
per the required final-step reminder: `data_layer.py`'s swappable-interface
design, Postgres as the intended production path, consent lineage +
consent-scoped interface should land together with that migration, and the
gold evaluation set is still outstanding and blocks every quality claim.
Asked rather than assumed which to do next — chose **stage 2 (Pinecone)**.

**Changed:** `backend/vector_store.py` (new), `scripts/index_reports.py`
(new), `backend/models.py` (+`NarrativeMatch`, +`RetrievalResult.narrative_matches`
— additive only), `backend/retrieve.py` (+narrative fallback, +`vector_ms`),
`backend/main.py` (forwards `vector_ms`), `.env.example` (+`PINECONE_INDEX_NAME`),
`requirements.txt` (+`pinecone`), `tests/test_vector_store.py` (16 tests),
`tests/test_retrieve.py` (+5 tests). All 180 tests green. `frontend/`
untouched, per the spec's own instruction that this stage stays behind the
same `RetrievalResult` interface.

**Verified the Pinecone SDK calls against the actually-installed package
(9.1.0), not against fetched docs** — and they disagreed in a way worth
recording. WebFetched documentation described `upsert_records(namespace,
records)` (positional) and `index.search(query={"inputs":..., "top_k":...,
"filter":...})` (one nested dict). The installed SDK's real signatures,
read via `inspect.signature()` and the source file directly, are different:
`upsert_records(*, records, namespace)` (keyword-only, reversed order) and
`search(*, namespace, top_k, inputs, filter, fields, ...)` (all top-level
keyword params, no nested `query` dict). Also found, again from source, not
docs: `create_index_for_model`, `has_index`, and `Index()` are all
explicitly-marked backwards-compatibility shims in this version — the
current API is `pc.indexes.create(name=..., spec=IntegratedSpec(cloud=...,
region=..., embed=EmbedConfig(...)))`, `pc.indexes.exists(name)`, and
`pc.index(name=...)` (lowercase). Wrote against the verified-current forms
throughout. This is the same "don't trust a stale prior, verify against
what's actually there" principle the `claude-api` skill spells out for the
Anthropic SDK — it turned out to matter just as much for a second
external API with no bundled skill to catch it.

**Namespace-per-patient is the structural leakage guard, not the metadata
filter.** `VectorStore.search()`/`upsert_chunks()` both require
`patient_id` and use it as the Pinecone namespace directly — there is no
parameter combination in this module that can cross patients, because the
namespace argument isn't optional and nothing downstream can silently omit
it. A `{"patient_id": patient_id}` metadata filter is applied on every
query too, but as defense in depth on top of that boundary, not as the
thing this actually relies on — the prompt's "make it structurally
impossible" bar reads as wanting the stronger of the two guarantees to be
load-bearing, not decorative.

**Chunking is by single-`#` header only, verified against a real corpus
REPORT fact** (not synthesized) pulled straight from `facts.jsonl` — six
sections split correctly (Chief Complaint / History of Present Illness /
Social History / Allergies / Medications / Assessment and Plan), the
leading date line before the first header is correctly dropped, and the
nested `## Plan` sub-header under Assessment and Plan correctly stays
inside its parent chunk rather than fragmenting further (the regex only
matches a bare `#` followed by whitespace, not `##`).

**Fallback trigger is deliberately narrow: "nothing resolved," not
"narrative in shape."** The prompt names both triggers, but the second one
would need a cue-word heuristic to detect narrative-shaped queries, and the
hackathon prompt explicitly says "No intent classifier. No routing decision
tree." Building even a lightweight narrative-detector felt like it would
cross that line, so `retrieve()` only attempts the vector fallback when
`entities` came back empty — the one trigger that's already a clean,
existing signal (`RetrievalResult.resolved`) rather than new classification
logic. Noted as a scoping choice, not an oversight.

**Did not run `scripts/index_reports.py` against a live Pinecone
account.** Same reasoning as the LLM polish pass: this spends the user's
own external-service resources (a real index, ~58,000 REPORT facts chunked
and embedded, real time), not something to do without being asked. What
*is* verified without a live account: `chunk_report_text` /
`iter_report_chunks` against real corpus text (above), every `VectorStore`
method against a fake stand-in object asserting the exact keyword
arguments passed to the SDK, and `retrieve()`'s fallback wiring (attempted
only when nothing resolves, skipped when the flag is off, degrades to an
empty list when the store raises) via monkeypatched
`vector_search_enabled`/`VectorStore`. Confirmed via `TestClient` against
the real corpus that a fully-unresolved query (`"has he ever mentioned
trouble sleeping"`) returns `narrative_matches: []` and `vector_ms: null`
cleanly with `PINECONE_API_KEY` unset — the fallback path is provably inert
until a real key is added.

**Everything downstream of this stays untouched on purpose:**
`backend/brief.py` does not render `narrative_matches` yet, and the
frontend does not show them — the prompt's own instruction was "keep this
behind the same interface so the frontend does not change," and rendering
narrative hits is a UI decision beyond what this step asked for. The field
exists on the wire now; surfacing it is future work, not scope creep on
this pass.

## 2026-08-21 — The gold evaluation set

Corpus restored first: `facts.jsonl` (718 MB) was dropped back into the repo,
`scripts/split_facts.py` regenerated `facts_by_patient/` — **1,083,231 facts
across 952 patients, matching the assertion exactly** — and all four
rehearsed demo queries reproduce end to end. Before this the tree ran but
every query returned an empty brief, because `patients.jsonl` is tracked and
the facts are not.

**Built:** `scripts/build_eval_set.py`, `data/eval_set.jsonl` (97 queries,
15 archetypes), `scripts/run_eval.py`, `eval/report.md`, `tests/test_eval.py`
(19 tests). Suite went 180 → 199, all green. This closes the item the build
prompt flagged as blocking every quality claim.

**Gold is derived on a path that never touches the code being scored.** For
each query the clinical intent is hand-specified as `(categories, codes,
window)` clauses read off the question; nothing in the gold comes from
`parse_query()` or `retrieve()`. Scoring gold produced by the pipeline under
test would return 1.0 by construction and measure nothing, so
`tests/test_eval.py::test_gold_is_not_derived_from_the_pipeline` enforces
this **structurally, by walking the builder's AST** for banned imports and
calls — a substring check was tried first and failed, because the builder's
own comments discuss `parse_query` at length precisely to explain why it
isn't used.

`get_patient_facts()` *is* shared by both sides. That is deliberate and not
circular: the filter is identical on both sides, already has its own unit
tests, and is not what this set evaluates. The contested half is the mapping
from question text to codes — re-implementing the filter would add a bug
surface for no epistemic gain.

**Two-tier relevance.** `required` drives recall; `allowed` is neither
rewarded nor penalised. Needed because `penicillin` resolves to an ALLERGY
substance *and* MEDICATION products at one span, so "is he allergic to
penicillin" legitimately surfaces "prescribed penicillin V in 2017". Scoring
that as a false positive would score a judgement call as a bug. The rule is
applied blanket-wise to every drug allergen via `ALLERGEN_MEDICATION_CODES`,
never per query, so it cannot become a way to quietly delete individual
failures. It was added *after* seeing first results — worth stating plainly —
but it moved only precision and abstention; **recall did not shift** (0.828
before and after), which is the check that it didn't paper over real misses.

### Results

`precision 0.940 · recall 0.833 · F1 0.883` over 66 scored queries, plus
31 abstention cases, 31/31 clean. Median latency 1.2 ms, p95 12.1 ms.

**Read recall, not precision.** Retrieval is a strict `category` + `code IN
(...)` filter, so it can only return facts whose codes the parser already
produced; a false positive needs the parser to resolve a span to a
confidently *wrong* code set, which is far rarer than resolving it to
nothing. High precision says the vocabulary isn't mismapped, not that
retrieval is good. This caveat is printed in `eval/report.md` itself.

An early draft scored precision 1.000 — because three `lab_date_window`
entries had picked patients whose labs fell outside the window, resolving to
**empty gold** and being silently excluded from scoring as free passes,
flattering that archetype to a fake recall of 1.000. `Builder.add()` now
refuses any entry with non-empty `required` clauses but empty resolved gold,
and the windowed archetype walks candidate patients until one qualifies.
Precision fell to 0.940 immediately, which is the honest number.

### What the set found

Three defects, none of which the 47 probe queries in `tests/queries.md` had
surfaced in months of use:

**1. `most recent X` silently returns nothing.** `retrieval/ner.py`'s bare
`recent` cue applies a 6-month *recency filter*, but "most recent X" is a
sort-and-take-newest instruction. `most recent hemoglobin` sets a window of
2026-02-22 → 2026-08-21; the patient's two results are from 2025-08-08 and
2020-08-07, so the query returns **zero facts when a year-old result exists**.
`latest hemoglobin` and `what was her last hemoglobin` correctly set no
window, so the behaviour is inconsistent within one intent family. Seven
queries, the largest single block of misses. For a pre-op tool this is the
exact failure mode the product claims to prevent.

**2. A concept's own name is never indexed as a surface form.** One root
cause behind six broken concepts. `Lexicon._load_synonyms()` indexes only the
`surface_forms` column, so a row named `hypertension` whose forms are
`htn|high bp|...` leaves the word "hypertension" unindexed. Every author
listed the alternatives and assumed the canonical name was implied. It fails
two ways, both silent:

| concept | effect | consequence |
|---|---|---|
| `hypertension`, `nt-probnp` | no match at all | zero recall |
| `blood glucose`, `hemoglobin`, `leukocytes` | falls through to the raw lexicon display, which is **wider** | picks up the **urine dipstick** codes |
| `myocardial infarction` | falls through **narrower** | 1 of 4 declared codes |

The widening case is the clinically dangerous one: a "blood glucose" trend
silently mixes `25428-4`/`5792-7` (Glucose in Urine by Test strip) into blood
readings, and "hemoglobin" and "leukocytes" absorb their urine-strip
counterparts the same way. `demo/queries.md` had *worried* about exactly this
("glucose resolves to 4 LOINC codes... can silently mix unrelated tests") and
hand-checked one patient who happened to be clean; the eval set found the
general case. Note `fasting glucose` resolves correctly — the precise
phrasings work and the obvious generic one degrades.

`demo/queries.md` diagnosed the `hypertension` case as a one-off "lexicon gap"
in a footnote. It is not one-off; it is one line in `lexicon.py`.

**3. Minor lexicon gaps:** `peanuts` (plural — corpus has "Peanut
(substance)"), `eggs` (corpus display normalises to "eggs (edible)"), `bee
sting` (corpus has "Bee venom").

### Corrections to `demo/queries.md`

Both verified against the restored corpus, both were the doc drifting rather
than code being wrong:

- Patient #1 is **32, not 76** — born 1950-07-01, died 1982-12-21.
  `age_years` correctly reports age at death; 76 is what she would be today
  had she lived. Her record ends in 1982.
- Patient #2's CBC is **`stale`, not `absent`** — 8 CBC facts, most recent
  2025-04-12, outside the 90-day window. The box shows 4 `absent` + 1 `stale`,
  so she demonstrates both missingness reasons rather than one.

### Not done, deliberately

None of the three defects is fixed. Two of them live in `retrieval/`, which
the build prompt marks `[DONE — do not modify]`; that instruction was about
not rebuilding it during the backend pass rather than a ban on bug fixes, but
it is still not a call to make unilaterally. Fixes are one line in
`lexicon.py` (index the concept name), one cue change in `ner.py` (stop
treating "most recent" as a window), and three surface forms. Rerunning
`scripts/run_eval.py` afterwards gives a measured before/after for the first
time — which is the point of having built this.
