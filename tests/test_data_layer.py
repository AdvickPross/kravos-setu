"""backend.data_layer -- the only module allowed to touch fact storage.

Isolated from the real corpus: every test inserts rows into ``kravos_test``
(see tests/conftest.py) instead of the ``kravos`` database
scripts/load_facts.py populates with the real corpus, so filtering,
ordering, and the allergy safety rule are verified deterministically rather
than against whatever happens to be loaded for real.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend import data_layer
from tests.conftest import insert_facts, insert_patients

PATIENT = "patient-1"


@pytest.fixture(autouse=True)
def _isolated_db(use_test_db):
    pass


def _fact(fact_id: str, **overrides) -> dict:
    base = {
        "fact_id": fact_id,
        "patient_id": PATIENT,
        "category": "LAB",
        "safety_class": "CLINICAL",
        "text": "text",
        "code_system": "http://loinc.org",
        "code": "1234-5",
        "display": "display",
        "value": None,
        "unit": None,
        "date": None,
        "status": "final",
        "source_resource_type": "Observation",
        "source_resource_id": "res-1",
        "encounter_id": "enc-1",
        "also_in": [],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------- facts


def test_filters_by_category():
    insert_facts([_fact("f1", category="LAB"), _fact("f2", category="MEDICATION")])
    results = data_layer.get_patient_facts(PATIENT, categories=["LAB"])
    assert [f.fact_id for f in results] == ["f1"]


def test_filters_by_code_in_list_not_equality():
    """Multi-code resolution is normal upstream; the store must filter
    ``code IN (...)``-style, never on a single code."""
    insert_facts([_fact("f1", code="A"), _fact("f2", code="B"), _fact("f3", code="C")])
    results = data_layer.get_patient_facts(PATIENT, codes=["A", "B"])
    assert {f.fact_id for f in results} == {"f1", "f2"}


def test_filters_by_date_range_inclusive():
    insert_facts(
        [
            _fact("early", date="2020-01-01T00:00:00+05:30"),
            _fact("in_range", date="2020-06-01T00:00:00+05:30"),
            _fact("late", date="2021-01-01T00:00:00+05:30"),
        ]
    )
    results = data_layer.get_patient_facts(
        PATIENT, date_from=date(2020, 3, 1), date_to=date(2020, 9, 1)
    )
    assert [f.fact_id for f in results] == ["in_range"]


def test_undated_facts_excluded_by_date_range():
    insert_facts([_fact("dated", date="2020-06-01T00:00:00+05:30"), _fact("undated", date=None)])
    results = data_layer.get_patient_facts(PATIENT, date_from=date(2020, 1, 1))
    assert [f.fact_id for f in results] == ["dated"]


def test_orders_most_recent_first_with_undated_last():
    insert_facts(
        [
            _fact("older", date="2019-01-01T00:00:00+05:30"),
            _fact("newer", date="2021-01-01T00:00:00+05:30"),
            _fact("undated", date=None),
        ]
    )
    results = data_layer.get_patient_facts(PATIENT)
    assert [f.fact_id for f in results] == ["newer", "older", "undated"]


def test_limit_truncates_ranked_results():
    insert_facts(
        [
            _fact("a", date="2019-01-01T00:00:00+05:30"),
            _fact("b", date="2020-01-01T00:00:00+05:30"),
            _fact("c", date="2021-01-01T00:00:00+05:30"),
        ]
    )
    results = data_layer.get_patient_facts(PATIENT, limit=2)
    assert [f.fact_id for f in results] == ["c", "b"]


def test_allergy_category_ignores_limit():
    insert_facts([_fact(f"f{i}", category="ALLERGY") for i in range(5)])
    results = data_layer.get_patient_facts(PATIENT, categories=["ALLERGY"], limit=1)
    assert len(results) == 5


def test_unknown_patient_returns_empty_list_not_error():
    results = data_layer.get_patient_facts("no-such-patient")
    assert results == []


# ------------------------------------------------------------------ patients


def test_list_and_get_patient():
    insert_patients(
        [
            {
                "patient_id": PATIENT,
                "name": "Test Patient",
                "birth_date": "1990-01-01",
                "gender": "female",
                "deceased": None,
                "_source_file": "x.json",
                "_n_facts": 3,
            }
        ]
    )
    listed = data_layer.list_patients()
    assert [p.patient_id for p in listed] == [PATIENT]

    fetched = data_layer.get_patient(PATIENT)
    assert fetched.name == "Test Patient"
    assert fetched.n_facts == 3


def test_get_patient_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        data_layer.get_patient("no-such-patient")
