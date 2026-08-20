# Path B status — paused

Written on request, as a snapshot for resuming Path B later. Path B is the
real router / SQL-retrieval / Postgres track described in
`kravos_path_b_claude_code_prompt.md`. It was explicitly superseded, not
abandoned by accident: after the infra-only pass logged in
`kravos_path_b_progress.md`, the decision was made to build the simplified
hackathon-track demo first (`kravos_hackathon_full_build_prompt.md`,
in-memory per-patient JSON, no intent classifier, no router). Path B resumes
after that demo is done.

**Nothing in this session touched Path B.** No Path B file was created,
edited, or was mid-edit at the time of this pause. This document exists so
the state doesn't have to be reconstructed from memory later — everything
below matches `kravos_path_b_progress.md`, which itself was only read this
session, never written to.

---

## Component status

| Component | State |
|---|---|
| Postgres schema (`retrieval/db.py`: `SCHEMA_SQL`, `DbSettings`, `COLUMNS`) | **Done** — schema defined, never applied to a running database |
| Docker/compose (`docker-compose.yml`) + config (`data/db.json`) | **Done** — files exist, container never started |
| `scripts/load_facts.py` | **Not started** |
| `retrieval/intent.py` + `data/intent_cues.json` | **Not started** |
| `retrieval/router.py` + `RetrievalPlan` | **Not started** |
| `retrieval/sql_store.py` | **Not started** (gated behind the `RetrievalPlan` checkpoint anyway) |
| Per-stage latency instrumentation (Path B's own) | **Not started** |
| Extended probes for route distribution | **Not started** |

No file under `retrieval/` was deleted or modified for Path B beyond what's
listed above as done. `retrieval/intent.py`, `retrieval/router.py`, and
`retrieval/sql_store.py` do not exist on disk — confirmed just now, not
assumed.

## Partial work — none

There is no partially-written function or class to finish or revert. The
three Path B files that exist (`docker-compose.yml`, `data/db.json`,
`retrieval/db.py`) were completed in the prior pass and are internally
consistent — `retrieval/db.py` imports psycopg lazily inside `.connect()`
specifically so it stays importable with no database running, which it
currently is (nothing imports this module from anywhere else in the repo,
so it has no effect on the hackathon track).

## Decisions made outside the original prompt

Recorded already in `kravos_path_b_progress.md`; repeating the ones that
matter for resuming:

- Postgres port is **5434** (5433 is taken by another local project,
  `taskapi-db`).
- Schema includes nullable consent-lineage columns
  (`consent_artefact_id`, `hi_type`, `hip_id`) from the first draft, per the
  prompt's instruction that the shape must be right before anything is
  built on it — but no consent-scope object/interface constraint exists yet
  (that was slated to land with `sql_store.py`, which doesn't exist).
- `value_num` (a numeric convenience column for lab trending) was
  *considered*, not added — open question, see below.
- `status` vocabulary used by the router's planned `status_filter` was only
  measured on a 200k-record prefix, not the full 1,083,231 — unverified
  whether `stopped`/`discontinued`/`entered-in-error` appear at all.

## Open questions

1. `value_num` — nullable numeric column for lab trending vs. `value` TEXT
   + cast-at-query-time. Cheaper to decide before any load happens.
   **Still open.**
2. `status` vocabulary — **resolved 2026-08-20**, from the hackathon track's
   demo-curation pass, which streamed every `facts_by_patient/*.jsonl` file
   (not `facts.jsonl` directly, and not another sample). The full,
   corpus-wide set is exactly `{final, active, resolved, completed}` —
   `stopped`, `discontinued`, and `entered-in-error` do not occur anywhere.
   Medications specifically only ever carry `active` or `completed`. Path
   B's `status_filter` (active / historical / any) should be designed
   against this real vocabulary now, not the assumption that `stopped`
   exists.
3. Loader idempotency style — truncate-and-reload vs. staging table +
   `ON CONFLICT DO NOTHING`. **Still open.**

## Test status

- **97 tests** cover the NER layer (`retrieval/`), untouched and green.
- Path B has **zero dedicated tests** — nothing beyond schema/config exists
  to test. `intent.py`, `router.py`, and `sql_store.py` were never started,
  so there's no code to have tests for.
- The hackathon track (separate, in progress, not part of this pause) has
  since added 35 more tests across `backend/`. Full current suite: **132
  passed**, 0 failed (verified just now via `python -m pytest tests/ -q`).
  This number will keep moving as the hackathon track continues; it's not a
  Path B signal, just proof the tree isn't broken.

## Environment state — Docker / Postgres

- Docker Desktop **is running** right now (`docker info` responds,
  `docker compose config` on `docker-compose.yml` validates cleanly).
- `docker ps -a` shows **no `kravos-postgres` container** — it has never
  been created. The two containers that do exist (`taskapi-app`,
  `taskapi-db`, both `Exited`) belong to an unrelated project and were not
  touched.
- No volume named `kravos_pgdata` exists yet either, since `docker compose
  up` was never run for this project.
- `psycopg[binary]` **3.3.4** is installed in the system Python (3.14.6),
  from the prior pass. It is **not yet in `requirements.txt`** — do that
  when `scripts/load_facts.py` is actually written, not before.

## Exact next step to resume Path B

Unchanged from `kravos_path_b_progress.md`'s restart prompt:

1. `docker compose up -d` (brings up `kravos-postgres` on host port 5434
   for the first time)
2. `python scripts/load_facts.py` — **does not exist yet, write it first**
   (streamed `COPY` load, idempotent, row-count assertion against
   1,083,231, add `psycopg[binary]` to `requirements.txt` at the same time)
3. `retrieval/intent.py` + `data/intent_cues.json` + tests
4. `retrieval/router.py` + `RetrievalPlan` + tests
5. **Stop.** Show `RetrievalPlan` and the router decision tree before
   writing `sql_store.py` — this is a hard checkpoint in the original
   prompt, not optional.

No work should proceed past step 4 without that review, per the original
prompt's own instruction.
