"""HIP-initiated linking, context notify and deep-link SMS
(NHA HIP_INTI_LINK_501-505, HIP_INIT_NOTIFY_HIECM) against kravos_test."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import backend.m2_hip_linking as hl
from backend.abdm_store import list_care_contexts, save_link_token, set_patient_mobile
from backend.abha_link import AbhaLinkStore
from backend.data_layer import connection
from conftest import insert_facts, insert_patients

HIP_ID = "kravos-hip-1"
JANE = "patient-jane"
RAVI = "patient-ravi"
ADDRESS = "jane@sbx"


class FakeGateway:
    hip_id = HIP_ID

    def __init__(self) -> None:
        self.client = SimpleNamespace(config=SimpleNamespace(hip_name="Kravos Setu"))
        self.calls: list[tuple[str, dict, dict]] = []
        self.fail_with: Exception | None = None

    def _record(self, name, body, **kwargs):
        if self.fail_with:
            raise self.fail_with
        self.calls.append((name, body, kwargs))
        return {}

    def generate_link_token(self, body, *, request_id):
        return self._record("generate_link_token", body, request_id=request_id)

    def link_care_context(self, body, *, link_token, request_id):
        return self._record("link_care_context", body, link_token=link_token, request_id=request_id)

    def context_notify(self, body, *, request_id):
        return self._record("context_notify", body, request_id=request_id)

    def sms_notify(self, body, *, request_id):
        return self._record("sms_notify", body, request_id=request_id)

    def named(self, name):
        return [(body, kwargs) for n, body, kwargs in self.calls if n == name]


def _fact(fact_id, patient_id, category, date, encounter_id):
    return {
        "fact_id": fact_id,
        "patient_id": patient_id,
        "category": category,
        "display": category.title(),
        "date": datetime.fromisoformat(date).replace(tzinfo=timezone.utc),
        "encounter_id": encounter_id,
    }


@pytest.fixture
def world(use_test_db, tmp_path, monkeypatch):
    insert_patients(
        [
            {"patient_id": JANE, "name": "Jane752 Doe514", "birth_date": "1990-05-01", "gender": "female"},
            {"patient_id": RAVI, "name": "Ravi11 Kumar22", "birth_date": "1985-02-02", "gender": "male"},
        ]
    )
    insert_facts(
        [
            _fact("f1", JANE, "CONDITION", "2024-01-02T10:00:00", "enc-new"),
            _fact("f2", JANE, "LAB", "2023-06-01T09:00:00", "enc-old"),
            _fact("f3", RAVI, "VITAL", "2024-03-03T09:00:00", "enc-ravi"),
        ]
    )
    store = AbhaLinkStore(tmp_path / "abha_links.json")
    store.link(abha_number="91-1234-5678-9012", patient_id=JANE, abha_address=ADDRESS)
    monkeypatch.setattr(hl, "default_store", lambda: store)
    return FakeGateway()


def _statuses(patient_id=JANE):
    return sorted((c["reference_number"], c["status"]) for c in list_care_contexts(patient_id=patient_id))


def _transaction(request_id):
    with connection().cursor() as cur:
        cur.execute(
            "SELECT patient_id, operation, status, error_code FROM abdm_transactions WHERE request_id = %s",
            (request_id,),
        )
        return cur.fetchone()


# -- demographics as the schema wants them ------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [("Jane752 Doe514", "Jane Doe"), ("Mary-Ann O'Neil3", "Mary-Ann O'Neil"), ("  A1  B2 ", "A B"), (None, "")],
)
def test_abdm_name_strips_synthea_digits(raw, expected):
    assert hl.abdm_name(raw) == expected


@pytest.mark.parametrize("raw, expected", [("female", "F"), ("male", "M"), ("other", "O"), (None, "U"), ("x", "U")])
def test_abdm_gender_codes(raw, expected):
    assert hl.abdm_gender(raw) == expected


# -- token, then link -----------------------------------------------------------------


def test_no_token_requests_one_with_schema_valid_demographics(world):
    assert hl.hip_link_patient(world, JANE) == "TOKEN_REQUESTED"

    [(body, kwargs)] = world.named("generate_link_token")
    assert body == {
        "abhaAddress": ADDRESS,
        "name": "Jane Doe",
        "gender": "F",
        "yearOfBirth": 1990,
        "abhaNumber": 91123456789012,
    }
    assert _transaction(kwargs["request_id"]) == (JANE, "LINK_TOKEN_GENERATION", "INITIATED", None)
    assert world.named("link_care_context") == []


def test_token_is_not_requested_twice_while_one_is_pending(world):
    hl.hip_link_patient(world, JANE)
    assert hl.hip_link_patient(world, JANE) == "TOKEN_PENDING"
    assert len(world.named("generate_link_token")) == 1


def test_with_a_token_every_unlinked_care_context_is_linked(world):
    save_link_token(abha_address=ADDRESS, hip_id=HIP_ID, link_token="tok-1")

    assert hl.hip_link_patient(world, JANE) == "LINK_REQUESTED"

    [(body, kwargs)] = world.named("link_care_context")
    assert kwargs["link_token"] == "tok-1"
    assert body["abhaAddress"] == ADDRESS
    assert body["abhaNumber"] == 91123456789012
    [group] = body["patient"]
    assert group["referenceNumber"] == JANE
    assert group["display"] == "Jane Doe"
    assert group["hiType"] == "OPConsultation"
    assert [c["referenceNumber"] for c in group["careContexts"]] == ["enc-new", "enc-old"]
    assert group["count"] == 2
    # Recorded before the call, so a fast callback finds them.
    assert _statuses() == [("enc-new", "LINK_REQUESTED"), ("enc-old", "LINK_REQUESTED")]
    # And not sent again while in flight.
    assert hl.hip_link_patient(world, JANE) == "NOTHING_TO_LINK"


def test_successful_callback_links_and_failed_one_is_retried(world, monkeypatch):
    monkeypatch.setattr(hl, "CARE_CONTEXTS_PER_LINK_CALL", 1)
    save_link_token(abha_address=ADDRESS, hip_id=HIP_ID, link_token="tok-1")
    hl.hip_link_patient(world, JANE)
    first, second = [kwargs["request_id"] for _, kwargs in world.named("link_care_context")]

    hl.settle_link_callback(
        {"status": "Successfully Linked care context", "response": {"requestId": first}}
    )
    hl.settle_link_callback(
        {
            "status": "Failed to link care context",
            "error": {"code": "ABDM-1038", "message": "ABHA address does not match the link token"},
            "response": {"requestId": second},
        }
    )

    assert _statuses() == [("enc-new", "LINKED"), ("enc-old", "LINK_FAILED")]
    assert _transaction(second)[2:] == ("LINK_FAILED", "ABDM-1038")

    # The next run retries only the failed one.
    assert hl.hip_link_patient(world, JANE) == "LINK_REQUESTED"
    body, _ = world.named("link_care_context")[-1]
    assert [c["referenceNumber"] for c in body["patient"][0]["careContexts"]] == ["enc-old"]


def test_link_call_failure_frees_the_care_contexts(world):
    save_link_token(abha_address=ADDRESS, hip_id=HIP_ID, link_token="tok-1")
    world.fail_with = RuntimeError("gateway down")

    assert hl.hip_link_patient(world, JANE) == "LINK_REQUEST_FAILED"
    # Both were in the one failed call; both are free for the next run.
    assert _statuses() == [("enc-new", "LINK_FAILED"), ("enc-old", "LINK_FAILED")]


def test_untagged_patient_is_not_linked(world):
    assert hl.hip_link_patient(world, RAVI) == "NO_ABHA_ADDRESS"
    assert world.calls == []


def test_callback_without_request_id_is_refused(world):
    with pytest.raises(ValueError):
        hl.settle_link_callback({"status": "Successfully Linked care context"})


# -- context notify -------------------------------------------------------------------


def test_context_notify_needs_a_linked_care_context(world):
    with pytest.raises(LookupError, match="not linked"):
        hl.notify_care_context_update(world, JANE, "enc-new", ["OPConsultation"])


def test_context_notify_sends_the_spec_body(world):
    save_link_token(abha_address=ADDRESS, hip_id=HIP_ID, link_token="tok-1")
    hl.hip_link_patient(world, JANE)
    _, kwargs = world.named("link_care_context")[0]
    hl.settle_link_callback({"status": "Successfully Linked care context", "response": {"requestId": kwargs["request_id"]}})

    request_id = hl.notify_care_context_update(world, JANE, "enc-new", ["OPConsultation", "Prescription"])

    [(body, kw)] = world.named("context_notify")
    assert kw == {"request_id": request_id}
    n = body["notification"]
    assert n["patient"] == {"id": ADDRESS}
    assert n["careContext"] == {"patientReference": JANE, "careContextReference": "enc-new"}
    assert n["hiTypes"] == ["OPConsultation", "Prescription"]
    assert n["hip"] == {"id": HIP_ID}
    assert n["date"].endswith("Z")

    assert hl.settle_acknowledgement(
        {"acknowledgement": {"status": "SUCCESS"}, "response": {"requestId": request_id}},
        ok_status="CONTEXT_NOTIFIED",
    ) == "ACCEPTED"
    assert _transaction(request_id)[2] == "CONTEXT_NOTIFIED"


def test_errored_acknowledgement_keeps_the_code(world):
    hl.start_m2_transaction(patient_id=JANE, transaction_id=None, request_id="r-1", operation="CONTEXT_NOTIFY")
    assert hl.settle_acknowledgement(
        {
            "acknowledgement": {"status": "ERRORED"},
            "error": {"code": "ABDM-1006", "message": "No links found for the patient in the given HIP"},
            "response": {"requestId": "r-1"},
        },
        ok_status="CONTEXT_NOTIFIED",
    ) == "ERRORED"
    assert _transaction("r-1")[2:] == ("ERRORED", "ABDM-1006")


# -- deep-link SMS ----------------------------------------------------------------------


def test_patient_without_abha_gets_one_sms_per_new_records(world):
    set_patient_mobile(patient_id=RAVI, mobile="9876543210")

    assert hl.sms_notify_patient(world, RAVI, "9876543210") == "SMS_REQUESTED"
    [(body, _)] = world.named("sms_notify")
    assert body == {"notification": {"phoneNo": "9876543210", "hip": {"id": HIP_ID, "name": "Kravos Setu"}}}

    assert hl.sms_notify_patient(world, RAVI, "9876543210") == "ALREADY_NOTIFIED"

    insert_facts([_fact("f9", RAVI, "LAB", "2024-09-09T09:00:00", "enc-ravi-2")])
    assert hl.sms_notify_patient(world, RAVI, "9876543210") == "SMS_REQUESTED"
    assert len(world.named("sms_notify")) == 2


def test_patient_with_abha_is_linked_not_texted(world):
    assert hl.sms_notify_patient(world, JANE, "9999999999") == "HAS_ABHA"
    assert world.named("sms_notify") == []


def test_failed_sms_is_retried_next_run(world):
    world.fail_with = RuntimeError("gateway down")
    assert hl.sms_notify_patient(world, RAVI, "9876543210") == "SMS_FAILED"
    world.fail_with = None
    assert hl.sms_notify_patient(world, RAVI, "9876543210") == "SMS_REQUESTED"


# -- the batch ------------------------------------------------------------------------


def test_batch_links_tagged_patients_and_texts_the_rest(world):
    set_patient_mobile(patient_id=RAVI, mobile="9876543210")
    save_link_token(abha_address=ADDRESS, hip_id=HIP_ID, link_token="tok-1")

    outcomes = hl.run_linking_batch(world)

    assert outcomes == {"LINK_REQUESTED": 1, "SMS_REQUESTED": 1}
    assert len(world.named("link_care_context")) == 1
    assert len(world.named("sms_notify")) == 1
    # A second run has nothing new to do.
    assert hl.run_linking_batch(world) == {"NOTHING_TO_LINK": 1, "ALREADY_NOTIFIED": 1}
