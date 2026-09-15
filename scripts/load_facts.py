"""Load facts.jsonl and patients.jsonl into Postgres.

Streams both files -- never reads facts.jsonl (701 MB) into memory. Uses
``COPY`` via a generated intermediate for speed. Idempotent by default:
truncates ``facts`` and ``patients`` before loading, so re-running this
script always leaves the tables matching the two source files exactly.
Pass ``--keep-existing`` to load into whatever is already there instead
(``ON CONFLICT (fact_id) DO NOTHING`` / same for patients).

    pwsh> docker compose up -d
    pwsh> python scripts/load_facts.py          # expect 1,083,231 facts, 952 patients
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from retrieval.db import COLUMNS, PATIENT_COLUMNS, SCHEMA_SQL, DbSettings  # noqa: E402

EXPECTED_FACTS = 1_083_231
EXPECTED_PATIENTS = 952

DEFAULT_FACTS_PATH = REPO_ROOT / "facts.jsonl"
DEFAULT_PATIENTS_PATH = REPO_ROOT / "patients.jsonl"


def _copy_value(value) -> str:
    """Render one Python value the way Postgres's text-format COPY expects."""
    if value is None:
        return r"\N"
    if isinstance(value, (list, dict)):
        text = json.dumps(value)
    else:
        text = str(value)
    # COPY's text format escapes backslash, tab and newline with a backslash.
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def _facts_rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            yield "\t".join(_copy_value(record.get(col)) for col in COLUMNS) + "\n"


def _patients_rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            values = {
                "patient_id": record.get("patient_id"),
                "name": record.get("name"),
                "birth_date": record.get("birth_date"),
                "gender": record.get("gender"),
                "deceased": record.get("deceased"),
                "n_facts": record.get("_n_facts", 0),
                "source_file": record.get("_source_file"),
            }
            yield "\t".join(_copy_value(values[col]) for col in PATIENT_COLUMNS) + "\n"


def load(
    conn,
    facts_path: Path,
    patients_path: Path,
    keep_existing: bool,
) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)

        if not keep_existing:
            print("truncating facts, patients ...", file=sys.stderr)
            cur.execute("TRUNCATE TABLE facts")
            cur.execute("TRUNCATE TABLE patients")

        print(f"loading {patients_path.name} ...", file=sys.stderr)
        if keep_existing:
            _copy_via_staging(cur, "patients", PATIENT_COLUMNS, "patient_id", _patients_rows(patients_path))
        else:
            with cur.copy(f"COPY patients ({', '.join(PATIENT_COLUMNS)}) FROM STDIN") as copy:
                for row in _patients_rows(patients_path):
                    copy.write(row)

        print(f"loading {facts_path.name} (streamed, this is the 701 MB file) ...", file=sys.stderr)
        if keep_existing:
            _copy_via_staging(cur, "facts", COLUMNS, "fact_id", _facts_rows(facts_path))
        else:
            with cur.copy(f"COPY facts ({', '.join(COLUMNS)}) FROM STDIN") as copy:
                n = 0
                for row in _facts_rows(facts_path):
                    copy.write(row)
                    n += 1
                    if n % 200_000 == 0:
                        print(f"  {n:,} facts streamed ...", file=sys.stderr)

        cur.execute("SELECT count(*) FROM facts")
        n_facts = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM patients")
        n_patients = cur.fetchone()[0]

    conn.commit()
    return n_facts, n_patients


def _copy_via_staging(cur, table: str, columns: tuple[str, ...], pk: str, rows) -> None:
    """``--keep-existing`` path: load into a temp staging table, then
    upsert-by-ignore into the real one, so re-running with existing data
    never raises on a duplicate primary key."""
    staging = f"_staging_{table}"
    cur.execute(f"CREATE TEMP TABLE {staging} (LIKE {table} INCLUDING ALL) ON COMMIT DROP")
    with cur.copy(f"COPY {staging} ({', '.join(columns)}) FROM STDIN") as copy:
        for row in rows:
            copy.write(row)
    cur.execute(f"INSERT INTO {table} SELECT * FROM {staging} ON CONFLICT ({pk}) DO NOTHING")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts-path", type=Path, default=DEFAULT_FACTS_PATH)
    parser.add_argument("--patients-path", type=Path, default=DEFAULT_PATIENTS_PATH)
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="load into whatever is already in the tables, skipping duplicates by "
        "primary key, instead of truncating first",
    )
    parser.add_argument(
        "--dbname",
        default=None,
        help="override the database to load into (default: data/db.json's, via DbSettings)",
    )
    args = parser.parse_args()

    if not args.facts_path.exists():
        parser.error(f"{args.facts_path} not found (see README's Prerequisites)")
    if not args.patients_path.exists():
        parser.error(f"{args.patients_path} not found")

    settings = DbSettings.load()
    if args.dbname:
        settings = DbSettings(**{**settings.__dict__, "dbname": args.dbname})

    conn = settings.connect()
    try:
        n_facts, n_patients = load(conn, args.facts_path, args.patients_path, args.keep_existing)
    finally:
        conn.close()

    print(f"facts: {n_facts:,} (expected {EXPECTED_FACTS:,})", file=sys.stderr)
    print(f"patients: {n_patients:,} (expected {EXPECTED_PATIENTS:,})", file=sys.stderr)

    if n_facts != EXPECTED_FACTS:
        print(
            f"WARNING: fact count does not match the known corpus size -- "
            f"expected {EXPECTED_FACTS:,}, got {n_facts:,}",
            file=sys.stderr,
        )
    if n_patients != EXPECTED_PATIENTS:
        print(
            f"WARNING: patient count does not match -- expected {EXPECTED_PATIENTS:,}, "
            f"got {n_patients:,}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
