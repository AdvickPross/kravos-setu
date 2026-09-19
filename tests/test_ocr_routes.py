"""Upload, review, commit.

Most of these never reach the OCR engine: the guardrails reject before a
pixel is read, which is both the point and what keeps them fast. The two that
do run recognition are the ones that cannot be faked -- the id stability the
whole approve-then-commit design rests on, and the refusal to write anything
the reviewer did not tick.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_SCAN = REPO_ROOT / "demo" / "scans" / "discharge_summary.png"

needs_scan = pytest.mark.skipif(
    not DEMO_SCAN.exists(), reason="run scripts/make_demo_scan.py"
)


@pytest.fixture
def api() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def patient_id() -> str:
    from backend.data_layer import list_patients

    try:
        patients = list_patients()
    except Exception:
        pytest.skip("Postgres is not running or no patient corpus loaded")
    if not patients:
        pytest.skip("no patient corpus loaded")
    return patients[0].patient_id


def upload(name: str = "discharge_summary.png"):
    return {"file": (name, DEMO_SCAN.read_bytes(), "image/png")}


# --------------------------------------------------------------------------
# guardrails, none of which reach the engine
# --------------------------------------------------------------------------


def test_a_non_image_is_refused_by_type(api: TestClient, patient_id: str):
    response = api.post(
        "/api/ocr/extract",
        files={"file": ("notes.md", b"# not a scan", "text/markdown")},
        data={"patientId": patient_id},
    )
    assert response.status_code == 415
    assert ".png" in response.json()["detail"]


@needs_scan
def test_an_unknown_patient_is_refused_before_ocr(api: TestClient, patient_id: str):
    """A fact filed against a patient that does not exist reads as real
    history and resolves to nothing."""
    response = api.post(
        "/api/ocr/extract", files=upload(), data={"patientId": "no-such-patient"}
    )
    assert response.status_code == 404


def test_an_empty_file_is_refused(api: TestClient, patient_id: str):
    response = api.post(
        "/api/ocr/extract",
        files={"file": ("blank.png", b"", "image/png")},
        data={"patientId": patient_id},
    )
    assert response.status_code == 422


def test_an_oversized_file_is_refused(api: TestClient, patient_id: str):
    from backend.ocr_routes import MAX_UPLOAD_BYTES

    response = api.post(
        "/api/ocr/extract",
        files={"file": ("huge.png", b"x" * (MAX_UPLOAD_BYTES + 1), "image/png")},
        data={"patientId": patient_id},
    )
    assert response.status_code == 413


@needs_scan
def test_a_bad_document_date_says_so(api: TestClient, patient_id: str):
    response = api.post(
        "/api/ocr/extract",
        files=upload(),
        data={"patientId": patient_id, "documentDate": "last tuesday"},
    )
    assert response.status_code == 422
    assert "ISO-8601" in response.json()["detail"]


def test_committing_nothing_is_refused(api: TestClient, patient_id: str):
    response = api.post(
        "/api/ocr/commit",
        files=upload(),
        data={"patientId": patient_id, "factIds": ""},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# the design the two endpoints exist for
# --------------------------------------------------------------------------


@needs_scan
def test_extract_proposes_and_writes_nothing(api: TestClient, patient_id: str):
    """The gap between the endpoints is the review step. `/extract` must be
    safe to call on anything."""
    response = api.post("/api/ocr/extract", files=upload(), data={"patientId": patient_id})
    assert response.status_code == 200
    body = response.json()
    assert body["facts"], "the demo scan should yield facts"
    assert body["lines"], "and the raw lines a reviewer reads"
    assert body["sections_seen"]
    # Every proposed fact carries the evidence for itself.
    for fact in body["facts"]:
        assert fact["line_text"]
        assert 0 < fact["confidence"] <= 1
        assert fact["page"] >= 1


@needs_scan
def test_fact_ids_survive_the_round_trip(api: TestClient, patient_id: str):
    """The bug this endpoint pair was broken by.

    Fact ids derive from (file, page, line, code). Spooling the upload to a
    randomly-named temp file made them differ between the `/extract` a
    reviewer approved and the `/commit` meant to honour it, so every commit
    failed with "none of the approved facts were found". The upload now keeps
    its real basename, which also stops `source_resource_id` pointing at a
    temp path that no longer exists.
    """
    first = api.post(
        "/api/ocr/extract", files=upload(), data={"patientId": patient_id}
    ).json()
    second = api.post(
        "/api/ocr/extract", files=upload(), data={"patientId": patient_id}
    ).json()

    assert [f["fact_id"] for f in first["facts"]] == [f["fact_id"] for f in second["facts"]]
    assert first["source_file"] == "discharge_summary.png"
    assert not first["source_file"].startswith("tmp")


@needs_scan
def test_the_filename_reaches_provenance(api: TestClient, patient_id: str):
    """Two scans of the same content under different names are different
    documents, and each fact has to say which one it came from."""
    a = api.post(
        "/api/ocr/extract", files=upload("opd_slip.png"), data={"patientId": patient_id}
    ).json()
    b = api.post(
        "/api/ocr/extract", files=upload("discharge.png"), data={"patientId": patient_id}
    ).json()
    assert {f["fact_id"] for f in a["facts"]}.isdisjoint({f["fact_id"] for f in b["facts"]})


@needs_scan
def test_only_approved_facts_are_written(api: TestClient, patient_id: str):
    """An unticked fact is a fact the reviewer rejected, and rejecting has to
    mean something."""
    extracted = api.post(
        "/api/ocr/extract", files=upload("approval_test.png"), data={"patientId": patient_id}
    ).json()
    chosen = [f["fact_id"] for f in extracted["facts"][:2]]

    response = api.post(
        "/api/ocr/commit",
        files=upload("approval_test.png"),
        data={"patientId": patient_id, "factIds": ",".join(chosen)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requested"] == 2, "only the ticked facts should be written"
    assert body["inserted"] + body["already_present"] == 2


@needs_scan
def test_an_id_that_is_not_in_the_document_is_refused(api: TestClient, patient_id: str):
    """A commit carrying ids the file does not produce is not a partial
    success -- something is wrong with the request and it should say so."""
    response = api.post(
        "/api/ocr/commit",
        files=upload(),
        data={"patientId": patient_id, "factIds": "deadbeefdeadbeef"},
    )
    assert response.status_code == 409
