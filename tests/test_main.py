"""backend.main -- FastAPI routes.

Isolated from the real corpus the same way test_data_layer.py is: each test
inserts rows into kravos_test (see tests/conftest.py) before hitting the app
through TestClient, so responses are deterministic.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from tests.conftest import insert_facts, insert_patients

PATIENT_ID = "patient-1"


def _fact(fact_id: str, **overrides) -> dict:
    base = {
        "fact_id": fact_id,
        "patient_id": PATIENT_ID,
        "category": "ALLERGY",
        "safety_class": "SAFETY",
        "text": "text",
        "code_system": "http://snomed.info/sct",
        "code": "1191",
        "display": "Aspirin",
        "value": None,
        "unit": None,
        "date": None,
        "status": "active",
        "source_resource_type": "AllergyIntolerance",
        "source_resource_id": "res-1",
        "encounter_id": "enc-1",
        "also_in": [],
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _isolated_db(use_test_db):
    pass


@pytest.fixture(autouse=True)
def _seed_patients(_isolated_db):
    insert_patients(
        [
            {
                "patient_id": PATIENT_ID,
                "name": "Jane Doe",
                "birth_date": "1990-01-01",
                "gender": "female",
                "deceased": None,
                "_source_file": "x.json",
                "_n_facts": 1,
            },
            {
                "patient_id": "patient-2",
                "name": "John Smith",
                "birth_date": "1980-01-01",
                "gender": "male",
                "deceased": None,
                "_source_file": "y.json",
                "_n_facts": 0,
            },
        ]
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_list_patients(client: TestClient):
    response = client.get("/api/patients")
    assert response.status_code == 200
    names = {p["name"] for p in response.json()}
    assert names == {"Jane Doe", "John Smith"}


def test_list_patients_filters_by_query_param(client: TestClient):
    response = client.get("/api/patients", params={"q": "jane"})
    assert response.status_code == 200
    names = [p["name"] for p in response.json()]
    assert names == ["Jane Doe"]


def test_get_patient_by_id(client: TestClient):
    response = client.get(f"/api/patients/{PATIENT_ID}")
    assert response.status_code == 200
    assert response.json()["name"] == "Jane Doe"


def test_get_patient_404_for_unknown_id(client: TestClient):
    response = client.get("/api/patients/no-such-patient")
    assert response.status_code == 404


def test_query_endpoint_returns_full_response_shape(client: TestClient):
    insert_facts([_fact("f1")])

    response = client.post(
        "/api/query", json={"patient_id": PATIENT_ID, "query": "any allergies?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["patient"]["patient_id"] == PATIENT_ID
    assert body["query"] == "any allergies?"
    assert "entities" in body["parsed"]
    assert "sections" in body["brief"]
    assert "entities" in body["result"]
    assert body["timings"]["parse_ms"] is not None
    assert body["timings"]["retrieve_ms"] is not None
    assert body["timings"]["brief_ms"] is not None
    assert body["timings"]["total_ms"] is not None


def test_query_endpoint_llm_polish_off_by_default(client: TestClient, monkeypatch):
    monkeypatch.delenv("KRAVOS_LLM_POLISH_ENABLED", raising=False)
    response = client.post(
        "/api/query", json={"patient_id": PATIENT_ID, "query": "any allergies?"}
    )
    body = response.json()
    assert body["timings"]["llm_ms"] is None
    assert body["brief"]["source"] == "template"
    assert body["brief"]["prose"] is None


def test_query_endpoint_404_for_unknown_patient(client: TestClient):
    response = client.post(
        "/api/query", json={"patient_id": "no-such-patient", "query": "any allergies?"}
    )
    assert response.status_code == 404


def test_query_endpoint_finds_the_allergy_fact(client: TestClient):
    insert_facts([_fact("f1")])

    response = client.post(
        "/api/query", json={"patient_id": PATIENT_ID, "query": "aspirin allergy"}
    )
    body = response.json()
    fact_ids = {f["fact_id"] for e in body["result"]["entities"] for f in e["facts"]}
    assert "f1" in fact_ids
