"""backend.main -- FastAPI routes.

Isolated from the real corpus the same way test_data_layer.py is: each test
points backend.data_layer at a tmp_path fixture before hitting the app
through TestClient, so responses are deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import data_layer
from backend.main import app

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


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


@pytest.fixture(autouse=True)
def _isolated_storage(monkeypatch, tmp_path):
    facts_dir = tmp_path / "facts_by_patient"
    facts_dir.mkdir()
    patients_path = tmp_path / "patients.jsonl"
    _write_jsonl(
        patients_path,
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
        ],
    )

    monkeypatch.setattr(data_layer, "FACTS_BY_PATIENT_DIR", facts_dir)
    monkeypatch.setattr(data_layer, "PATIENTS_PATH", patients_path)
    monkeypatch.setattr(data_layer, "_patient_facts_cache", {})
    monkeypatch.setattr(data_layer, "_patients_cache", None)
    return facts_dir, patients_path


@pytest.fixture
def client(_isolated_storage) -> TestClient:
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


def test_query_endpoint_returns_full_response_shape(client: TestClient, _isolated_storage):
    facts_dir, _ = _isolated_storage
    _write_jsonl(facts_dir / f"{PATIENT_ID}.jsonl", [_fact("f1")])

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


def test_query_endpoint_finds_the_allergy_fact(client: TestClient, _isolated_storage):
    facts_dir, _ = _isolated_storage
    _write_jsonl(facts_dir / f"{PATIENT_ID}.jsonl", [_fact("f1")])

    response = client.post(
        "/api/query", json={"patient_id": PATIENT_ID, "query": "aspirin allergy"}
    )
    body = response.json()
    fact_ids = {f["fact_id"] for e in body["result"]["entities"] for f in e["facts"]}
    assert "f1" in fact_ids
