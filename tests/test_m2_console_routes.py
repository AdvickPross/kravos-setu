"""The /m2 console's read routes: they report state and never leak secrets."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import backend.m2_console_routes as console
from backend.abdm_store import save_link_token, save_linked_care_context, set_patient_mobile
from backend.abha_link import AbhaLinkStore
from backend.main import app
from backend.m2_service import start_m2_transaction
from conftest import insert_facts, insert_patients

JANE = "patient-jane"


@pytest.fixture
def api(use_test_db, tmp_path, monkeypatch) -> TestClient:
    insert_patients([{"patient_id": JANE, "name": "Jane752 Doe514", "birth_date": "1990-05-01", "gender": "female"}])
    insert_facts(
        [
            {"fact_id": f"f{i}", "patient_id": JANE, "category": cat, "display": cat.title(),
             "code_system": "http://snomed.info/sct", "code": "1",
             "date": datetime(2024, 1, i + 1, tzinfo=timezone.utc), "encounter_id": enc}
            for i, (cat, enc) in enumerate([("CONDITION", "enc-a"), ("VITAL", "enc-a"), ("LAB", "enc-b")])
        ]
    )
    store = AbhaLinkStore(tmp_path / "links.json")
    store.link(abha_number="91-1234-5678-9012", patient_id=JANE, abha_address="jane@sbx")
    monkeypatch.setattr(console, "default_store", lambda: store)
    monkeypatch.setattr(
        console,
        "_config",
        lambda: Mock(hip_id="hip-1", hip_name="Kravos Setu", environment=Mock(value="sandbox"), verify_gateway_tokens=True),
    )
    return TestClient(app)


def test_status_reports_configuration_without_calling_abdm(api, monkeypatch):
    body = api.get("/api/m2/status").json()
    assert body["hipId"] == "hip-1" and body["hipConfigured"] is True
    assert body["otpDelivery"] == "log"


def test_patient_state_masks_secrets_and_merges_statuses(api):
    set_patient_mobile(patient_id=JANE, mobile="9876543210")
    save_link_token(abha_address="jane@sbx", hip_id="hip-1", link_token="secret-token")
    save_linked_care_context(
        patient_id=JANE, hip_id="hip-1", transaction_id="t", reference_number="enc-a", display="x", hi_type="OPConsultation"
    )

    body = api.get(f"/api/m2/patients/{JANE}/state").json()

    assert body["abha"]["abhaNumber"] == "••••••••••9012"
    assert body["abha"]["abhaAddress"] == "jane@sbx"
    assert body["mobile"] == "••••••3210"
    assert body["linkTokenExpiresAt"] is not None
    assert "secret-token" not in str(body)
    assert body["tokenDemographics"] == {"name": "Jane Doe", "gender": "F", "yearOfBirth": 1990}
    assert {c["referenceNumber"]: c["status"] for c in body["careContexts"]} == {"enc-a": "LINKED", "enc-b": "UNLINKED"}


def test_fhir_preview_is_a_document_bundle(api):
    bundle = api.get(f"/api/m2/patients/{JANE}/care-contexts/enc-a/fhir").json()
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "document"
    assert bundle["entry"][0]["resource"]["resourceType"] == "Composition"
    assert api.get(f"/api/m2/patients/{JANE}/care-contexts/nope/fhir").status_code == 404


def test_transactions_filter_by_patient(api):
    start_m2_transaction(patient_id=JANE, transaction_id=None, request_id="r1", operation="LINK_TOKEN_GENERATION")
    start_m2_transaction(patient_id="other", transaction_id=None, request_id="r2", operation="SMS_NOTIFY")

    mine = api.get(f"/api/m2/transactions?patient_id={JANE}").json()
    everyone = api.get("/api/m2/transactions").json()

    assert [t["request_id"] for t in mine] == ["r1"]
    assert {t["request_id"] for t in everyone} == {"r1", "r2"}


@pytest.mark.parametrize("url", ["http://plain.example", "https://x.example/api/v3/hip"])
def test_bridge_url_must_be_an_https_base_url(api, monkeypatch, url):
    client = Mock()
    monkeypatch.setattr(console, "_m2_client", lambda: client)
    assert api.patch("/api/m2/bridge/url", json={"url": url}).status_code == 422
    client.update_bridge_url.assert_not_called()


def test_bridge_url_update_is_sent_without_trailing_slash(api, monkeypatch):
    client = Mock()
    monkeypatch.setattr(console, "_m2_client", lambda: client)
    response = api.patch("/api/m2/bridge/url", json={"url": "https://kravos.ngrok-free.app/"})
    assert response.status_code == 200
    client.update_bridge_url.assert_called_once_with("https://kravos.ngrok-free.app")
