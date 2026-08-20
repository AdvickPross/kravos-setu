# Path B — progress log & restart prompt

Companion to `kravos_path_b_claude_code_prompt.md`. Log first, restart prompt at
the bottom. Last updated: **2026-08-19**, stopped by user request (credit budget).

---

## Status: Path B is ~15% done — infrastructure only, no retrieval logic yet

Path A (query-side NER) is complete and green: **97 tests passing**. Path B work
so far touched nothing Path A depends on, so the repo is in a working state.
`retrieval/db.py` is not imported by any existing module — deleting the three
new files would return the repo to its end-of-Path-A state exactly.

### Done

| # | Step (from the prompt's order of work) | State |
|---|---|---|
| 1 | Postgres schema | **done** — `retrieval/db.py` |
| 1 | Docker/compose + config | **done** — `docker-compose.yml`, `data/db.json` |
| 1 | `scripts/load_facts.py` | **not started** |
| 2 | `retrieval/intent.py` + `data/intent_cues.json` | **not started** |
| 3 | `retrieval/router.py` + `RetrievalPlan` | **not started** |
| 4 | `retrieval/sql_store.py` | not started (gated — see checkpoint) |
| 5 | latency instrumentation | not started |
| 6 | extended probes | not started |

### Files added this pass

- **`docker-compose.yml`** — `postgres:16`, container `kravos-postgres`, host port
  **5434** (5432 is the usual default, 5433 is taken by your other project),
  db/user/password all `kravos`, named volume `kravos_pgdata`, `pg_isready`
  healthcheck, `shm_size: 1gb`.
- **`data/db.json`** — connection settings, per-field env overrides
  (`KRAVOS_PORT`, `KRAVOS_PASSWORD`, …). The port is config, not code.
- **`retrieval/db.py`** — `DbSettings` (loads `data/db.json`, `.connect()` imports
  psycopg lazily so the rules layers stay importable without a database),
  `SCHEMA_SQL`, `COLUMNS`.

### Schema decisions already made (in `SCHEMA_SQL`)

- Flat `facts` table mirroring the JSONL schema, `fact_id` TEXT PRIMARY KEY.
- Indexes: `(patient_id, category)`, `(patient_id, code_system, code)`, `(date)`,
  plus `(patient_id, date DESC)` for the recency ranking the SQL store will do.
- `also_in` as `JSONB`; `date` as `TIMESTAMPTZ` (values carry `+05:30` offsets).
- **Consent lineage columns present from the first load** —
  `consent_artefact_id`, `hi_type`, `hip_id`, all nullable. Per the prompt: the
  shape must be right now, because an unscoped query path that exists is a path
  someone uses later. The repository-interface constraint (a consent-scope object
  the store cannot be called without) is **not built yet** — that lands with
  `sql_store.py`.

### Environment changes made outside the repo

- **`pip install "psycopg[binary]"`** → psycopg **3.3.4** installed into the
  system Python (3.14.6). Not yet added to `requirements.txt` — do that next pass.
- **Docker Desktop was not running**; I launched it. As of the last check the
  daemon is **up** (`docker ps` responds) and **no containers exist yet** —
  `docker compose up -d` has never been run, so the volume and database do not
  exist either.

### Corpus facts measured (first 200,000 records, streamed)

- `value` is **`str` or `null`** — never a JSON number. TEXT column is faithful;
  a numeric convenience column (`value_num`) was *considered* for lab trends but
  **not added** — decide before loading, since backfilling means a reload.
- `status` values seen: `final`, `completed`, `resolved`, `active`.
- `safety_class`: `CLINICAL`, `SAFETY`, `CONTEXT`.
- `also_in`: always a list, empty in ~95% of records.

⚠️ **These come from a 200k-record prefix, not the full 1,083,231.** A full-file
status scan was started and cancelled, so the complete set of `status` values is
**unverified** — in particular, whether `stopped` / `discontinued` / `entered-in-error`
appear at all. This matters: the router's `status_filter` (active / historical /
any) maps onto these values. **Get them from Postgres after the load**
(`SELECT status, count(*) FROM facts GROUP BY status`), not from another pass
over the 701 MB file.

### Open questions to settle next pass

1. **`value_num`** — add a nullable numeric column for lab trending, or keep
   `value` TEXT only and cast at query time? Cheaper to decide before the load.
2. **`status` vocabulary** — confirm from the loaded table before writing the
   status-cue rules; do not assume `stopped` exists just because the clinical
   guidance talks about stopping drugs.
3. **Idempotency style for the loader** — truncate-and-reload (simple, exactly
   idempotent) vs staging table + `ON CONFLICT DO NOTHING` (supports incremental
   top-ups). Leaning truncate-by-default with an opt-in `--keep-existing`.

---

## Restart prompt

> Continuing Path B on Kravos Setu (Windows/pwsh, repo root `D:\Repos\kravos-setu`).
> Read `kravos_path_b_claude_code_prompt.md` for the full spec and
> `kravos_path_b_progress.md` for what's done — don't redo the finished parts.
>
> Already built: `docker-compose.yml` (postgres:16 on host port **5434**),
> `data/db.json` (connection config, `KRAVOS_*` env overrides), `retrieval/db.py`
> (`DbSettings`, `SCHEMA_SQL` including the nullable consent-lineage columns,
> `COLUMNS`). psycopg 3.3.4 is installed but missing from `requirements.txt`.
> Docker Desktop is running; the `kravos-postgres` container has never been
> created. Path A (NER) is complete — 97 tests green; keep them that way.
>
> Pick up at the loader and work in this order:
> **`scripts/load_facts.py` → `retrieval/intent.py` (+ `data/intent_cues.json`, tests)
> → `retrieval/router.py` + `RetrievalPlan` (+ tests) → STOP.**
>
> The prompt requires a hard checkpoint: **show me `RetrievalPlan` and the router
> decision tree before writing `sql_store.py`.** Honour it.
>
> Constraints that still bind: rules and SQL only (no ML, no embeddings, no LLM);
> never load `facts.jsonl` into memory; terminology and cue words live in data
> files; `tests/queries.md` stays a debugging instrument — **no accuracy,
> precision or recall numbers from it**, report counts of observed behaviours
> instead.
>
> Cost discipline: I'm watching the credit budget. Avoid full-corpus passes over
> `facts.jsonl` — after the load, get distributions from Postgres instead. If you
> are running low, stop and update this file rather than starting new work.
>
> Commands to bring the database up:
> ```pwsh
> docker compose up -d
> docker compose ps
> python scripts/load_facts.py          # expect 1,083,231 rows
> ```
