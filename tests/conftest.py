"""Shared fixtures for the backend test suite.

Storage is Postgres now (``backend/data_layer.py``). Two kinds of tests
exist here and they must not be conflated:

- **Isolated tests** (test_data_layer.py, test_retrieve.py, test_main.py)
  insert their own rows into a dedicated ``kravos_test`` database and use
  the ``use_test_db`` fixture below to point ``backend.data_layer`` at it,
  so results are deterministic and independent of whatever the real corpus
  contains.
- **Real-corpus tests** (test_eval.py, test_abha_routes.py) deliberately
  read the ``kravos`` database ``scripts/load_facts.py`` populates with the
  real 1,083,231-row corpus, and must keep doing so -- they do not request
  ``use_test_db``.

``insert_facts`` / ``insert_patients`` always write to ``kravos_test``
explicitly, regardless of which database ``backend.data_layer`` is
currently pointed at, so seeding never touches real data.

Requires a running Postgres: ``docker compose up -d`` before ``pytest``.
"""

from __future__ import annotations

import os
os.environ.setdefault("KRAVOS_CONNECT_TIMEOUT", "1")

import pytest
from psycopg.types.json import Jsonb

from retrieval.db import COLUMNS, PATIENT_COLUMNS, SCHEMA_SQL, TEST_DBNAME, DbSettings


def _test_settings() -> DbSettings:
    base = DbSettings.load()
    return DbSettings(**{**base.__dict__, "dbname": TEST_DBNAME})


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_schema():
    base = DbSettings.load()
    # kravos_test may not exist yet on a fresh checkout -- create it against
    # the maintenance connection (the real 'kravos' db), then apply the
    # schema to it. Never touches kravos's own tables.
    try:
        conn = base.connect()
    except Exception as exc:
        # Postgres is not running; tests that require a live database will fail
        # at point of use or should be skipped. Non-DB tests can proceed.
        return

    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DBNAME,))
            if not cur.fetchone():
                cur.execute(f"CREATE DATABASE {TEST_DBNAME}")
    finally:
        conn.close()

    test_conn = _test_settings().connect()
    try:
        with test_conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        test_conn.commit()
    finally:
        test_conn.close()


@pytest.fixture
def use_test_db(monkeypatch):
    """Point backend.data_layer at kravos_test for this test only, and
    start it from an empty database.

    Resets data_layer's cached connection on both ends so it actually
    reconnects to the newly-selected database rather than reusing whatever
    connection an earlier test left open.
    """
    import backend.data_layer as data_layer

    monkeypatch.setenv("KRAVOS_DBNAME", TEST_DBNAME)
    if data_layer._conn is not None and not data_layer._conn.closed:
        data_layer._conn.close()
    data_layer._conn = None

    conn = _test_settings().connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE conversation_turns, conversations, facts, patients")
    finally:
        conn.close()

    yield

    if data_layer._conn is not None and not data_layer._conn.closed:
        data_layer._conn.close()
    data_layer._conn = None


def insert_facts(records: list[dict]) -> None:
    """Insert raw fact dicts (facts.jsonl shape, or test_*.py's local
    ``_fact()`` builders) into kravos_test's facts table. Always kravos_test
    -- never wherever backend.data_layer itself currently points."""
    conn = _test_settings().connect()
    conn.autocommit = True
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    try:
        with conn.cursor() as cur:
            for record in records:
                values = [
                    Jsonb(record.get("also_in") or []) if col == "also_in" else record.get(col)
                    for col in COLUMNS
                ]
                cur.execute(f"INSERT INTO facts ({', '.join(COLUMNS)}) VALUES ({placeholders})", values)
    finally:
        conn.close()


def insert_patients(records: list[dict]) -> None:
    """Insert raw patient dicts (patients.jsonl shape, ``_n_facts`` /
    ``_source_file`` aliases accepted the same way PatientSummary accepts
    them) into kravos_test's patients table."""
    conn = _test_settings().connect()
    conn.autocommit = True
    placeholders = ", ".join(["%s"] * len(PATIENT_COLUMNS))
    try:
        with conn.cursor() as cur:
            for record in records:
                row = {
                    "patient_id": record.get("patient_id"),
                    "name": record.get("name"),
                    "birth_date": record.get("birth_date"),
                    "gender": record.get("gender"),
                    "deceased": record.get("deceased"),
                    "n_facts": record.get("n_facts", record.get("_n_facts", 0)),
                    "source_file": record.get("source_file", record.get("_source_file")),
                }
                cur.execute(
                    f"INSERT INTO patients ({', '.join(PATIENT_COLUMNS)}) VALUES ({placeholders})",
                    [row[c] for c in PATIENT_COLUMNS],
                )
    finally:
        conn.close()
