"""The only module that touches fact storage.

Backed by Postgres now (``facts`` and ``patients`` tables, loaded by
``scripts/load_facts.py``). The function signatures here are unchanged from
the JSON-backed version this replaced -- filter kwargs in, ``Fact`` /
``PatientSummary`` objects out -- so nothing above this module (``retrieve``,
``brief``, the FastAPI routes) needed to change for the swap.

Connection settings come from ``retrieval.db.DbSettings``, which reads
``data/db.json`` and honours ``KRAVOS_*`` environment overrides -- including
``KRAVOS_DBNAME``, which is how ``tests/conftest.py`` points every test at
``kravos_test`` instead of the real corpus.

One connection, opened lazily and reused. Reconnects once on failure (the
container restarting during a long dev session) rather than raising.
"""

from __future__ import annotations

from datetime import date

import psycopg

from backend.models import Fact, PatientSummary
from retrieval.db import DbSettings

_conn: psycopg.Connection | None = None


def _connection() -> psycopg.Connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = DbSettings.load().connect()
        _conn.autocommit = True
    return _conn


def connection() -> psycopg.Connection:
    """The shared connection, for the other Postgres-backed tables this
    module doesn't own (conversations / conversation_turns --
    backend/conversation.py). Same lazy-reconnect instance ``get_patient*``
    uses, not a second connection to the same database."""
    return _connection()


def _fact_from_row(row: tuple, columns: list[str]) -> Fact:
    data = dict(zip(columns, row))
    return Fact.model_validate(data)


def _patient_from_row(row: tuple, columns: list[str]) -> PatientSummary:
    data = dict(zip(columns, row))
    return PatientSummary.model_validate(
        {
            "patient_id": data["patient_id"],
            "name": data["name"],
            "birth_date": data["birth_date"],
            "gender": data["gender"],
            "deceased": data["deceased"],
            "_n_facts": data["n_facts"],
            "_source_file": data["source_file"],
        }
    )


def list_patients() -> list[PatientSummary]:
    with _connection().cursor() as cur:
        cur.execute(
            "SELECT patient_id, name, birth_date, gender, deceased, n_facts, source_file "
            "FROM patients "
            # Hand-created records first. Someone who just added a patient is
            # about to look for them in a list of 952 Synthea ones, and the
            # table has no natural order to fall back on.
            "ORDER BY (source_file = 'manual') DESC NULLS LAST, name"
        )
        columns = [d.name for d in cur.description]
        return [_patient_from_row(row, columns) for row in cur.fetchall()]


def get_patient(patient_id: str) -> PatientSummary:
    with _connection().cursor() as cur:
        cur.execute(
            "SELECT patient_id, name, birth_date, gender, deceased, n_facts, source_file "
            "FROM patients WHERE patient_id = %s",
            (patient_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise KeyError(f"unknown patient_id: {patient_id}")
        columns = [d.name for d in cur.description]
        return _patient_from_row(row, columns)


_FACT_COLUMNS = (
    "fact_id",
    "patient_id",
    "category",
    "safety_class",
    "text",
    "code_system",
    "code",
    "display",
    "value",
    "unit",
    "date",
    "status",
    "source_resource_type",
    "source_resource_id",
    "encounter_id",
    "also_in",
)


def get_patient_facts(
    patient_id: str,
    categories: list[str] | None = None,
    codes: list[str] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int | None = None,
) -> list[Fact]:
    """Facts for one patient, filtered and ranked most-recent-first.

    Safety rule: when ``categories`` includes ``ALLERGY``, ``limit`` is
    ignored -- safety-critical retrieval returns the complete set, never
    top-k.
    """
    where = ["patient_id = %s"]
    params: list = [patient_id]

    if categories:
        where.append("category = ANY(%s)")
        params.append(list(categories))
    if codes:
        where.append("code = ANY(%s)")
        params.append(list(codes))
    if date_from is not None:
        where.append("date >= %s")
        params.append(date_from)
    if date_to is not None:
        where.append("date <= %s")
        params.append(date_to)

    is_allergy_query = bool(categories) and "ALLERGY" in categories
    sql = (
        f"SELECT {', '.join(_FACT_COLUMNS)} FROM facts "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY date DESC NULLS LAST"
    )
    if limit is not None and not is_allergy_query:
        sql += " LIMIT %s"
        params.append(limit)

    with _connection().cursor() as cur:
        cur.execute(sql, params)
        columns = [d.name for d in cur.description]
        return [_fact_from_row(row, columns) for row in cur.fetchall()]
