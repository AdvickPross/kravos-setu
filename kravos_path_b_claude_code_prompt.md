# Claude Code Prompt — Kravos Setu: Intent, Router, SQL Retrieval

Continuing directly from the query-side NER work. Paste from the repo root.

---

## Where we are

The NER layer is built and green (97 tests). `parse_query()` takes a doctor's question and returns a `ParsedQuery` with `ResolvedEntity` objects — span, category, `code_system`, `codes` (list), `display`, `MatchQuality`, `negated` — plus `date_from`/`date_to`.

Established behaviours to preserve and build against:

- The lexicon is corpus-derived: 1,170 rows → 2,443 surface forms → 1,156 codes. Regenerated via `scripts/build_lexicon.py`, never hardcoded.
- Multi-code resolution is normal. 23 lab analytes map to several LOINC codes; `glucose` returns 4. SQL must filter `code IN (...)`, never `code =`.
- **One entity per (category, code_system) group, sharing a span.** `aspirin` emits two entities at identical offsets — ALLERGY 1191 and the MEDICATION products. The router must handle overlapping entities rather than assume one entity per span.
- Negation is resolved at parse time (`negated=True`), including self-negating terms like NKDA and a `but` blocker that stops negation bleeding forward.
- Vocabulary strategy is **Option B**: facts are stored with whatever `code_system` the source sent. No canonicalization at ingestion. Resolution returns code sets spanning systems.

Facts live in `facts.jsonl` — 1,083,231 records, 701 MB, 952 Synthea patients. Schema per record: `fact_id`, `patient_id`, `category`, `safety_class`, `text`, `code_system`, `code`, `display`, `value`, `unit`, `date`, `status`, `source_resource_type`, `source_resource_id`, `encounter_id`, `also_in`.

Category counts: LAB 600,024 · PROCEDURE 163,236 · VITAL 141,593 · MEDICATION 70,114 · REPORT 57,987 · CONDITION 35,665 · IMMUNIZATION 13,732 · ALLERGY 880.

---

## What to build now

Three things, in this order: **Postgres fact store → intent classifier → router → SQL retrieval.** No vector search, no embeddings, no LLM, no reranking. Rules only.

### 1. Postgres fact store

Load `facts.jsonl` into Postgres. I'm on Windows; Postgres runs in Docker.

- Flat `facts` table mirroring the JSONL schema. `fact_id` primary key.
- Index on `(patient_id, category)` and `(patient_id, code_system, code)` — those are the two access patterns the router will generate. Also index `date` for range filtering.
- Stream the load; never read the 701 MB file into memory. Use `COPY` from a generated intermediate if that's fastest, but keep the script re-runnable and idempotent.
- `scripts/load_facts.py`, with a row count assertion at the end (expect 1,083,231).
- **Consent scaffolding, structurally enforced now rather than retrofitted.** Add nullable consent lineage columns (`consent_artefact_id`, `hi_type`, `hip_id`) to the facts table, and make the repository interface accept a consent-scope object it cannot be called without. Synthea data has no real consent artefact, so pass a permissive default scope — but the *shape* must be right, because an unscoped query path that exists is a path someone uses later. Do not add consent filtering logic beyond the interface constraint yet.

### 2. Intent classifier — rules

`retrieval/intent.py`. Input `ParsedQuery`, output an intent label. Rules and keyword patterns only; no model, no training.

Starting taxonomy — extend if the probe queries demand it, but justify additions:

- `MEDICATION_RECON` — current/active meds
- `LAB_TREND` — a lab value over time
- `LAB_LATEST` — most recent value of a lab
- `ALLERGY_CHECK` — safety-critical
- `CONDITION_HISTORY` — does the patient have X
- `PROCEDURE_HISTORY` — has the patient had X
- `SYMPTOM_RECALL` — narrative, "has he ever mentioned…"
- `FULL_BRIEF` — synthesized summary
- `UNKNOWN` — honest fallback

Much of this falls out of NER's output. Entity categories are a strong signal — a resolved MEDICATION entity plus no time expression is likely `MEDICATION_RECON`; a LAB entity plus a date range is `LAB_TREND`. The intent words the probes surfaced as UNRESOLVED (`meds`, `dose`, `history of`, `ever`, `currently`, `on`) belong here as cue words, not in the lexicon.

Keep cue words in a data file (`data/intent_cues.json`), consistent with how `query_cues.json` already works.

### 3. Router — rules, the decision tree, not a model

`retrieval/router.py`. Input `ParsedQuery` + intent, output a `RetrievalPlan`.

The tree:

1. Do all entities resolve to codes with acceptable `MatchQuality` (EXACT or SYNONYM)? Any UNRESOLVED entity, or FUZZY on a safety-critical query, pushes toward VECTOR or BOTH.
2. Does the intent need narrative? `SYMPTOM_RECALL` and `FULL_BRIEF` → narrative needed.
3. Neither → `SQL`. Narrative only → `VECTOR`. Both → `BOTH`.
4. After SQL executes: empty or suspiciously small result → set a `fallback_to_vector` flag on the plan.
5. **Safety-critical override.** `ALLERGY_CHECK` always routes SQL *and* forces the vector fallback regardless of SQL result size. Never depends on a single store being complete.

The vector path does not exist yet. Emit `VECTOR`/`BOTH` plans honestly and have the executor raise `NotImplementedError` — do not silently downgrade them to SQL. I want to see how often the router asks for a path that isn't built; that number tells me when to build it.

`RetrievalPlan` should be an explicit Pydantic object: route, per-category code sets, date window, negated entities, safety flag, fallback flag. It's the contract between routing and execution and should be inspectable in tests.

### 4. SQL retrieval

`retrieval/sql_store.py`. Executes a `RetrievalPlan` against Postgres.

- Filter: `patient_id`, `category`, `code IN (...)`, date range.
- **Safety-critical queries return all matches, never top-k.** Allergy and interaction checks must not silently truncate.
- Everything else ranked by recency, with a sane limit.
- `status` is load-bearing and must be exposed, not filtered away. See below.
- Return facts with full provenance intact — `source_resource_type`, `source_resource_id`, `encounter_id`, `also_in`. Provenance is not optional in this system.

### 5. Per-stage latency instrumentation

Time each stage — parse, intent, route, SQL — and attach the timings to the result object. Not logging; structured fields. Targets are ~50–100 ms for a SQL-only medication or allergy check. Instrument from the first commit; retrofitting timings produces fiction.

---

## Two things from clinical guidance that shape this

I checked perioperative guidelines (SPAQI consensus, several hospital perioperative medication guidelines) since the wedge is pre-operative assessment. Two findings that are requirements, not nice-to-haves:

### Drug classes are the clinical vocabulary — but don't build the rollups yet

Perioperative guidance is organized **by drug class, not by drug**. The guidelines are literally tabulated as "drug class → drugs in class": antiplatelets, ACE inhibitors/ARBs, NSAIDs, SGLT2 inhibitors, statins, anticoagulants, MAOIs. "Is he on antiplatelets?" is the real question an anaesthetist asks.

Right now NER returns UNRESOLVED for `beta blockers`, `ACE inhibitors`, etc. **Leave it that way for this pass.** Reason: one of those same guidelines states plainly that its class lists are not all-inclusive and providers must check for unlisted agents. That instruction works for a human who notices a gap; a retrieval system that silently reports "no antiplatelets on file" because its class list predates ticagrelor is a dangerous failure. An honest UNRESOLVED is safer than a confident partial answer.

What I want **now** is only this: make sure nothing you build forecloses class rollups later.

- Class → ingredient set is just another concept row with more codes. `synonyms.csv` already has the right shape. Don't add a parallel mechanism.
- The router must be able to carry a code set of 30+ codes for one entity as naturally as it carries 4.
- Since class membership is defined at ingredient level and combination products exist (Aggrenox = dipyridamole + aspirin), a class query must match on **any** extracted ingredient. The normalizer already extracts ingredients — don't build anything that assumes one ingredient per medication fact.
- When classes do land, coverage must be disclosed with the answer ("class list covers N agents, reviewed <date>"), not hidden. That's the missingness-flagging behaviour that differentiates this product. Design the result object so a coverage/provenance note can attach to a resolved entity without restructuring.

### `status` distinguishes "on" from "stopped" and is clinically load-bearing

Perioperative guidance turns on exactly this: continue through surgery vs hold 2 days vs hold 7 days. A brief that can't tell "on clopidogrel" from "stopped clopidogrel 5 days ago" is answering a different question than the one asked.

Your earlier call was right — `stopped aspirin` is a status question, not a negation, and belongs here rather than in NER. So:

- Add status cue detection to the intent/router layer: `currently`, `still on`, `active`, `taking` vs `stopped`, `discontinued`, `held`, `was on`, `previously`.
- Put a `status_filter` on `RetrievalPlan` — active / historical / any.
- Default to **any**, and surface status per fact. Never filter it away silently. If a doctor asks "is he on aspirin" and the only fact is a stopped prescription, the answer is "stopped on <date>", not "no".

---

## Constraints

- Python, Pydantic v2. **No ML libraries, no embeddings, no model downloads, no LLM calls.** This entire layer is rules and SQL.
- **Windows / PowerShell.** All commands you give me must be pwsh.
- Postgres in Docker. I already run Postgres for another project on port 5433 — pick a different port and put it in config, don't collide.
- Never load `facts.jsonl` into memory.
- Terminology and cue words stay in data files.
- Don't build: vector retrieval, embeddings, fusion, reranking, briefing generation, the missingness detector, drug-class rollups. All explicitly out of scope for this pass.

## Evaluation discipline

`tests/queries.md` stays a debugging instrument. **Do not report accuracy, precision, or recall from it.** The gold evaluation set — 80–100 queries with gold `fact_id` lists — is a separate artifact that does not exist yet, and no metric in the spec is claimable until it does. If you find yourself wanting to state a number, state a count of behaviours observed instead.

Extend the probe set with end-to-end queries so I can see route distribution and which plans hit `NotImplementedError`.

## Order of work

Postgres schema + loader → `intent.py` (+ tests) → `router.py` and `RetrievalPlan` (+ tests) → `sql_store.py` → instrumentation → extended probes.

**Stop and show me `RetrievalPlan` and the router decision tree before writing `sql_store.py`.** That object is the contract everything downstream depends on, and I want to check it carries what the vector path and the class rollups will need before anything is built on top of it.
