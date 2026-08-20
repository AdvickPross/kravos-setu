"""The only module that touches fact storage.

Backed by pre-split per-patient JSON now (``facts_by_patient/{patient_id}.jsonl``,
produced by ``scripts/split_facts.py``). The function signatures here are
deliberately the same shape a Postgres-backed version would take -- filter
kwargs in, ``Fact``/``PatientSummary`` objects out -- so swapping the body for
SQL later is a single-file change nothing else notices. No caller may read
``facts.jsonl``, ``facts_by_patient/``, or ``patients.jsonl`` directly.

Per-patient facts are cached in a plain dict, populated lazily on first
access. 952 patients x ~1,100 facts is fine in memory once warm; nothing is
preloaded at import time or startup.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from backend.models import Fact, PatientSummary

REPO_ROOT = Path(__file__).resolve().parent.parent
FACTS_BY_PATIENT_DIR = REPO_ROOT / "facts_by_patient"
PATIENTS_PATH = REPO_ROOT / "patients.jsonl"

_MIN_DATETIME = datetime.min.replace(tzinfo=timezone.utc)

_patient_facts_cache: dict[str, list[Fact]] = {}
_patients_cache: dict[str, PatientSummary] | None = None


def _load_patients() -> dict[str, PatientSummary]:
    global _patients_cache
    if _patients_cache is not None:
        return _patients_cache

    patients: dict[str, PatientSummary] = {}
    with PATIENTS_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            summary = PatientSummary.model_validate(json.loads(line))
            patients[summary.patient_id] = summary

    _patients_cache = patients
    return patients


def _load_patient_facts(patient_id: str) -> list[Fact]:
    if patient_id in _patient_facts_cache:
        return _patient_facts_cache[patient_id]

    facts: list[Fact] = []
    path = FACTS_BY_PATIENT_DIR / f"{patient_id}.jsonl"
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                facts.append(Fact.model_validate_json(line))

    _patient_facts_cache[patient_id] = facts
    return facts


def list_patients() -> list[PatientSummary]:
    return list(_load_patients().values())


def get_patient(patient_id: str) -> PatientSummary:
    patients = _load_patients()
    if patient_id not in patients:
        raise KeyError(f"unknown patient_id: {patient_id}")
    return patients[patient_id]


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
    category_set = set(categories) if categories else None
    code_set = set(codes) if codes else None

    matches = [
        fact
        for fact in _load_patient_facts(patient_id)
        if (category_set is None or fact.category in category_set)
        and (code_set is None or fact.code in code_set)
        and (date_from is None or (fact.date is not None and fact.date.date() >= date_from))
        and (date_to is None or (fact.date is not None and fact.date.date() <= date_to))
    ]
    matches.sort(key=lambda f: f.date or _MIN_DATETIME, reverse=True)

    is_allergy_query = category_set is not None and "ALLERGY" in category_set
    if limit is not None and not is_allergy_query:
        matches = matches[:limit]

    return matches
