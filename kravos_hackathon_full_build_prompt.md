# Claude Code Prompt — Kravos Setu Hackathon Build

Paste from the repo root (`E:\Kravos_Setu\db`).

---

## Project context

**Kravos Setu** is an AI clinical-context layer over Indian health records (ABDM/NDHM, FHIR R4). It answers a doctor's natural-language question about a patient from that patient's records, and flags what *should* be known but is missing. Wedge: **pre-operative assessment** — what an anaesthetist needs before surgery.

The framing that matters: **not "AI summarizes records."** The differentiator is identifying what is absent, not just retrieving what is present. Keep this in mind for the brief output format.

Cloud/server-shaped. Running locally now, hosted for demo. No edge deployment, no quantized or distilled models.

---

## What already exists — do not rebuild

### Ingestion (done, untouched)
- `extract.py` (~14KB) — FHIR bundle → flat facts
- `batch.py` — runs extractor over a bundle directory
- `facts.jsonl` — **1,083,231 facts, 701 MB, 952 Synthea patients**
- `patients.jsonl` — patient index
- `lexicon.csv` — 1,170 distinct `(category, code_system, code, display)` tuples
- `scripts/build_lexicon.py` — regenerates the above

### Query-side NER (done, 97 tests green)
```
retrieval/
  models.py      normalize.py    lexicon.py    ner.py
data/
  synonyms.csv (69 concepts)     normalization.json    query_cues.json
scripts/
  build_lexicon.py               run_probes.py
tests/
  test_normalize.py  test_lexicon.py  test_ner.py  queries.md
```

`parse_query(text) -> ParsedQuery` is the entrypoint. Returns `ResolvedEntity` objects with `span`, `start`, `end`, `category`, `code_system`, `codes` (list), `display`, `MatchQuality`, `negated`, plus `date_from`/`date_to`.

Index: 1,170 lexicon rows → 2,443 surface forms → 1,156 codes.

### Fact record schema
```json
{
  "fact_id": "d9780cfcf933f208",
  "patient_id": "bc2bb0d1-...",
  "category": "CONDITION",
  "safety_class": "CLINICAL",
  "text": "Condition: Medication review due (situation) [resolved] (resolved 2016-09-03)",
  "code_system": "http://snomed.info/sct",
  "code": "314529007",
  "display": "Medication review due (situation)",
  "value": null,
  "unit": null,
  "date": "2016-03-05T00:34:26+05:30",
  "status": "resolved",
  "source_resource_type": "Condition",
  "source_resource_id": "bc2bb0d1-...",
  "encounter_id": "bc2bb0d1-...",
  "also_in": []
}
```

### Corpus composition (measured)

| Category | Facts | Distinct codes |
|---|---|---|
| LAB | 600,024 | 253 |
| PROCEDURE | 163,236 | 333 |
| VITAL | 141,593 | 24 |
| MEDICATION | 70,114 | 253 |
| REPORT | 57,987 | 1 |
| CONDITION | 35,665 | 260 |
| IMMUNIZATION | 13,732 | 24 |
| ALLERGY | 880 | 22 |

Code systems: LOINC 798,321 · SNOMED 200,981 · RxNorm 70,197 · CVX 13,732.
Category → system is clean: LAB/VITAL → LOINC, CONDITION/PROCEDURE → SNOMED, MEDICATION → RxNorm, IMMUNIZATION → CVX. `REPORT` is a single LOINC document code (34117-2) whose `text` field holds full free-text clinical notes.

### Established behaviours to preserve

- **Multi-code resolution is normal.** 23 lab analytes map to several LOINC codes; `glucose` → 4. Always filter `code IN (...)`, never `code ==`.
- **Overlapping entities at identical offsets.** `aspirin` emits two entities — ALLERGY 1191 and MEDICATION products. Retrieval must handle two entities sharing a span.
- **Negation resolved at parse time.** `negated=True`, includes self-negating terms (NKDA) and a `but` blocker.
- **Vocabulary strategy is Option B.** Facts keep whatever `code_system` the source sent. No canonicalization at ingestion. Resolution returns code sets spanning systems.
- **`status` is clinically load-bearing.** Perioperative guidance turns on continue-vs-hold. "On clopidogrel" vs "stopped clopidogrel 5 days ago" are different answers. Never filter status away silently — always surface it.
- **Drug-class rollups are deliberately NOT built.** `beta blockers`, `ACE inhibitors` return UNRESOLVED. An incomplete class list silently under-retrieves, which is worse than an honest miss. Do not add them. Just don't foreclose them (see constraints).

---

## Target stack

- **Backend:** Python, FastAPI, Pydantic v2
- **Data layer now:** pre-split JSON per patient, read in-process
- **Data layer later:** Docker + Postgres (prompt me at the end — see final section)
- **Vector DB:** Pinecone (**stage 2 only**, see below)
- **Frontend:** Next.js 16, TypeScript
- **Environment:** Windows / PowerShell

---

## Pipeline to build

```
query text
  → parse_query()                    [DONE — do not modify]
  → retrieve(parsed, patient_id)     [NEW — category/code filter over patient facts]
  → build_brief(facts)               [NEW — template, status inline, missingness noted]
  → optional LLM polish pass         [NEW — isolated, must degrade gracefully]
  → FastAPI route                    [NEW]
  → Next.js UI                       [NEW]
```

No intent classifier. No routing decision tree. No `RetrievalPlan` object. Route is determined directly by resolved entity `category` — that information is already in the NER output and does not need re-deriving.

---

## Workstreams

Four parallel tracks with fixed interfaces between them. Each owns distinct files. Build stubs first so all four can proceed simultaneously, then swap stubs for real implementations.

### A — Data layer

**Owns:** `scripts/split_facts.py`, `backend/data_layer.py`

1. `scripts/split_facts.py` — one-time, streams `facts.jsonl` line by line, writes `facts_by_patient/{patient_id}.jsonl`. **Never load the 701 MB file into memory.** Idempotent, re-runnable, asserts total row count of 1,083,231 at the end. Also emit a small `facts_by_patient/_index.json` with per-patient fact counts.

2. `backend/data_layer.py` — the only module that touches fact storage:

```python
def get_patient_facts(
    patient_id: str,
    categories: list[str] | None = None,
    codes: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int | None = None,
) -> list[Fact]: ...

def list_patients() -> list[PatientSummary]: ...
def get_patient(patient_id: str) -> PatientSummary: ...
```

**This signature must be identical to what a Postgres-backed version would take.** It is an interface, and swapping JSON → Postgres later must be a single-file change that nothing else notices. No caller may read files directly.

`Fact` is a Pydantic model mirroring the JSONL schema exactly. Cache per-patient loads in a simple dict (952 patients × ~1,100 facts is fine in memory once warm; do not preload all patients at startup).

**Safety rule:** when `categories` includes `ALLERGY`, `limit` is ignored. Safety-critical retrieval returns complete sets, never top-k.

### B — Retrieval

**Owns:** `backend/retrieve.py`

```python
def retrieve(parsed: ParsedQuery, patient_id: str) -> RetrievalResult: ...
```

- For each `ResolvedEntity` with quality EXACT or SYNONYM, call `get_patient_facts` with that entity's `category` and `codes`, plus the query-level date window.
- Handle overlapping entities (aspirin → ALLERGY + MEDICATION) by issuing both lookups and keeping results distinct per entity.
- Facts for `negated=True` entities: retrieve them anyway, but mark them `negated_in_query=True` on the result. Do not silently drop — the doctor asking "any patient with no known allergies" still needs to see what's on file.
- If no entity resolved, return an empty result with `unresolved_spans` populated. Do not guess.
- **Never truncate ALLERGY results.**
- `RetrievalResult` is an explicit Pydantic model: facts grouped per entity, unresolved spans, the date window applied, and per-stage timings.

**Missingness (the product differentiator — build this, it is small).** Maintain `data/expected_facts.json`: a per-intent-ish checklist of what a pre-op assessment expects to see. Something like: recent basic metabolic panel, recent CBC, allergy documentation, current medication list, cardiac history. For each expected item, check whether the patient has any matching fact in a recency window, and populate `RetrievalResult.missing` with what is absent. This is a set-difference over coded facts, not a model.

Crucially: **distinguish "not present" from "not retrieved."** An empty result because the patient genuinely has no allergy facts is different from an empty result because nothing resolved. The result object must say which.

### C — Brief generation

**Owns:** `backend/brief.py`, `data/brief_templates/`

```python
def build_brief(result: RetrievalResult, patient: PatientSummary) -> Brief: ...
```

- **Template first, deterministically.** Group by category. Medications with status and date inline ("Clopidogrel 75mg — stopped 2016-03-05"). Allergies as a complete list or an explicit "No allergy records on file." Labs with value, unit, date, and most-recent-first ordering.
- **Missingness gets its own section**, visibly: "Not on file: no potassium in the last 90 days; no documented allergy history." This is the part that makes the product distinctive — do not bury it.
- Every rendered fact carries its `fact_id` so the UI can show provenance on hover/click.
- **Optional LLM polish pass**, strictly isolated in its own function behind a feature flag. Takes the templated brief and rewrites it as prose. Must be given *only* the retrieved facts, must be instructed to add nothing. **If the LLM call fails, times out, or the flag is off, the templated brief is returned unchanged.** The demo must be fully functional with the LLM path disabled.

Do not build an NLI groundedness checker. The template path is grounded by construction; the LLM path is a stretch feature.

### D — API + Frontend + demo curation

**Owns:** `backend/main.py`, `frontend/`, `demo/`

**FastAPI (`backend/main.py`):**
- `GET /api/patients` — list, with fact counts, searchable
- `GET /api/patients/{id}` — summary
- `POST /api/query` — body `{patient_id, query}` → full response: brief, facts grouped per entity, resolved entities with spans and match quality, missing items, per-stage timings
- CORS for the Next.js dev origin
- Structured per-stage timings on every response (parse / retrieve / brief), as fields, not logs

**Next.js 16 frontend (`frontend/`):**
- App Router, TypeScript, Tailwind
- Patient picker → query box → brief view
- **Show the parse.** Render the query with resolved entities highlighted, colour-coded by match quality (exact / synonym / fuzzy / unresolved) and struck-through or marked when negated. This makes the NER work visible instead of invisible, which matters for judging.
- **Show the missingness section prominently**, visually distinct from retrieved facts.
- Fact provenance on click — `fact_id`, `source_resource_type`, `encounter_id`, date.
- Show per-stage latency in a small footer. It is a real engineering signal and costs nothing.
- Generate TypeScript types from the Pydantic models rather than hand-writing them.

**Demo curation (`demo/`):**
- Pick 3–5 demo patients. Verify their facts do **not** hit multi-code collisions on the demo queries, and that at least one has interesting `status` variation (a stopped medication) and one has a genuine missingness gap.
- `demo/queries.md` — the rehearsed queries, what each demonstrates, expected output shape.
- Everything must work with the LLM flag off.

---

## Stage 2 — Pinecone (only after stage 1 is green end to end)

**Do not start this until the full pipeline above works and is demo-ready.**

The `REPORT` category is 57,987 free-text clinical notes — the 5% of the corpus with no code to filter on. This is the only place vector search earns its place; everything else is coded and belongs in the structured path.

**Owns:** `backend/vector_store.py`, `scripts/index_reports.py`

- Chunk `REPORT` facts by section (the notes have `# Chief Complaint`, `# History of Present Illness`, `# Allergies`, `# Medications`, `# Assessment and Plan` headers — split on those, they are consistent).
- Embed and upsert to Pinecone with metadata: `patient_id`, `fact_id`, `encounter_id`, `date`, `section`.
- **Namespace or metadata-filter by `patient_id` on every query, without exception.** Cross-patient leakage in a clinical system is the worst possible bug. Make it structurally impossible — the query function must require a `patient_id` argument and must always apply the filter.
- Prefer Pinecone's hosted embedding/inference so no local model download is needed.
- Wire in as a **fallback only**: when nothing resolves, or when the query is narrative in shape. Never as a replacement for coded retrieval.
- `scripts/index_reports.py` streams from the split files; never loads everything.

Keep this behind the same `RetrievalResult` interface so the frontend does not change.

---

## Constraints

- **Windows / PowerShell.** Every command you give me must be pwsh, not bash.
- **Never load `facts.jsonl` into memory.** Stream it, always.
- Terminology and cue words live in data files, never hardcoded.
- Do not modify anything under `retrieval/` — the NER layer is done and tested.
- No ML libraries in stage 1. No embeddings, no local models, no training. Stage 1 is filtering and templating.
- Secrets (Pinecone key, LLM key) in `.env`, never committed. Provide a `.env.example`.
- `definitions.json/` is NDHM insurance/claims CodeSystems — irrelevant, do not read.
- Postgres runs on 5433 for another project on this machine. Do not collide when the time comes.

### Do not foreclose drug-class rollups

They are deferred, not cancelled — perioperative guidance is organized by class ("is he on antiplatelets?"), so this is coming.

- A class is just a concept row with more codes. `synonyms.csv` already has the shape. Do not build a parallel mechanism.
- Retrieval must carry a 30+ code set for one entity as naturally as it carries 4.
- Class membership is ingredient-level and combination products exist (Aggrenox = dipyridamole + aspirin). Do not assume one ingredient per medication fact.
- When classes land, **coverage must be disclosed with the answer** ("class list covers N agents, reviewed <date>"), not hidden. Design `RetrievalResult` and `Brief` so a coverage note can attach to a resolved entity without restructuring.

---

## Evaluation discipline

`tests/queries.md` is a **debugging instrument, not an evaluation set.** Do not report accuracy, precision, or recall from it, or from anything else. The gold evaluation set — 80–100 queries with gold `fact_id` lists — does not exist yet, and no metric in the product spec is claimable until it does. If you want to state a number, state a count of observed behaviours instead.

---

## Order of work

1. `Fact` / `PatientSummary` / `RetrievalResult` / `Brief` Pydantic models — **show me these before proceeding**
2. `scripts/split_facts.py`, run it, verify counts
3. `backend/data_layer.py` + tests
4. `backend/retrieve.py` + missingness + tests
5. `backend/brief.py` (template path only) + tests
6. `backend/main.py` FastAPI routes
7. `frontend/` Next.js 16
8. `demo/` curation and rehearsal
9. LLM polish pass behind flag
10. **Stop. Confirm demo-ready.** Only then, stage 2 Pinecone.

**Stop and show me the Pydantic models after step 1**, before anything is built on them. Those are the contracts everything else depends on.

---

## Final step — prompt me about Postgres

When stage 1 is complete and the demo works end to end, **remind me explicitly** that:

- `data_layer.py` was built as a swappable interface specifically so JSON → Postgres is a one-file change
- Postgres is the intended production path (Docker, port ≠ 5433, indexes on `(patient_id, category)` and `(patient_id, code_system, code)` and `date`)
- Consent lineage columns (`consent_artefact_id`, `hi_type`, `hip_id`) and a consent-scoped repository interface should go in at the same time, because retrofitting a scope parameter across a grown codebase never fully happens
- The gold evaluation set is still outstanding and blocks every quality claim

Ask me whether to proceed with that migration rather than assuming.
