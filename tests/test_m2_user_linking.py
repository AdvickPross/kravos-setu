"""User-initiated linking (NHA USER_INIT_LINK_603-605), end to end against
kravos_test: discover -> init -> OTP -> confirm, with a fake gateway."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import backend.m2_service as svc
from abha.m2_models import HipDiscoveryCallbackRequest, LinkConfirmRequest, LinkInitRequest
from backend.abdm_store import list_care_contexts, set_patient_mobile
from backend.abha_link import AbhaLinkStore
from backend.data_layer import connection
from conftest import insert_facts, insert_patients

HIP_ID = "kravos-hip-1"
JANE = "patient-jane"
TWIN = "patient-twin"
MOBILE = "9876543210"


class FakeGateway:
    hip_id = HIP_ID

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.fail_with: Exception | None = None

    def _record(self, name: str, body: dict, request_id: str) -> dict:
        if self.fail_with:
            raise self.fail_with
        self.sent.append((name, body))
        return {}

    def on_discover(self, body, *, request_id):
        return self._record("on_discover", body, request_id)

    def on_init(self, body, *, request_id):
        return self._record("on_init", body, request_id)

    def on_confirm(self, body, *, request_id):
        return self._record("on_confirm", body, request_id)

    def last(self, name: str) -> dict:
        return [body for sent, body in self.sent if sent == name][-1]


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
            {"patient_id": TWIN, "name": "Janet33 Doe514", "birth_date": "1990-09-09", "gender": "female"},
        ]
    )
    insert_facts(
        [
            _fact("f1", JANE, "CONDITION", "2024-01-02T10:00:00", "enc-new"),
            _fact("f2", JANE, "MEDICATION", "2024-01-02T10:30:00", "enc-new"),
            _fact("f3", JANE, "LAB", "2023-06-01T09:00:00", "enc-old"),
            _fact("f4", JANE, "ALLERGY", "2022-01-01T00:00:00", None),
        ]
    )
    store = AbhaLinkStore(tmp_path / "abha_links.json")
    store.link(abha_number="91-1234-5678-9012", patient_id=JANE, abha_address="jane@sbx")
    monkeypatch.setattr(svc, "default_store", lambda: store)

    otps: list[str] = []
    monkeypatch.setattr(svc, "deliver_otp", lambda *, mobile, otp, link_ref_number: otps.append(otp))
    return {"gateway": FakeGateway(), "otps": otps}


def _discover(abha_address="jane@sbx", *, name="Jane Doe", yob=1990, gender="F", mobile=MOBILE, txn="txn-1"):
    verified = [{"type": "MOBILE", "value": mobile}] if mobile else []
    return HipDiscoveryCallbackRequest.model_validate(
        {
            "transactionId": txn,
            "patient": {
                "id": abha_address,
                "name": name,
                "gender": gender,
                "yearOfBirth": yob,
                "verifiedIdentifiers": verified,
            },
        }
    )


def _init(refs, *, txn="txn-1", patient_ref=JANE):
    return LinkInitRequest.model_validate(
        {
            "transactionId": txn,
            "abhaAddress": "jane@sbx",
            "patient": [
                {
                    "referenceNumber": patient_ref,
                    "careContexts": [{"referenceNumber": r} for r in refs],
                    "hiType": "OPConsultation",
                    "count": len(refs),
                }
            ],
        }
    )


def _confirm(link_ref, otp):
    return LinkConfirmRequest.model_validate(
        {"confirmation": {"linkRefNumber": link_ref, "token": otp}}
    )


def _run(handler, gateway, request, request_id):
    svc.start_m2_transaction(
        patient_id=None, transaction_id=None, request_id=request_id, operation="TEST"
    )
    handler(gateway, request, gateway_request_id=request_id)


def _status(request_id):
    with connection().cursor() as cur:
        cur.execute(
            "SELECT status, error_code FROM abdm_transactions WHERE request_id = %s",
            (request_id,),
        )
        return cur.fetchone()


def _offered(body):
    return [cc["referenceNumber"] for group in body["patient"] for cc in group["careContexts"]]


# -- discovery ------------------------------------------------------------------


def test_discovery_matches_on_abha_address_and_offers_encounters(world):
    gw = world["gateway"]
    _run(svc.handle_discovery, gw, _discover(), "req-d1")

    body = gw.last("on_discover")
    assert body["transactionId"] == "txn-1"
    assert body["response"] == {"requestId": "req-d1"}
    assert body["matchedBy"] == ["ABHA_ADDRESS"]
    [group] = body["patient"]
    assert group["referenceNumber"] == JANE
    assert group["hiType"] == "OPConsultation"
    assert group["count"] == 2
    # Newest first; the encounter-less allergy is not a care context.
    assert _offered(body) == ["enc-new", "enc-old"]
    assert group["careContexts"][0]["display"] == "Visit on 02 Jan 2024: Diagnoses, Medications"
    assert _status("req-d1") == ("DISCOVERY_RESPONSE_SENT", None)


def test_discovery_falls_back_to_mobile_with_fuzzy_demographics(world):
    set_patient_mobile(patient_id=JANE, mobile=MOBILE)
    gw = world["gateway"]
    _run(svc.handle_discovery, gw, _discover("unknown@sbx", name="DOE JANE"), "req-d2")

    body = gw.last("on_discover")
    assert body["matchedBy"] == ["MOBILE"]
    assert body["patient"][0]["referenceNumber"] == JANE


@pytest.mark.parametrize(
    "overrides",
    [{"yob": 1991}, {"gender": "M"}, {"name": "Priya Sharma"}, {"mobile": "9000000000"}, {"mobile": None}],
)
def test_mobile_match_needs_every_demographic(world, overrides):
    set_patient_mobile(patient_id=JANE, mobile=MOBILE)
    gw = world["gateway"]
    _run(svc.handle_discovery, gw, _discover("unknown@sbx", **overrides), "req-d3")

    body = gw.last("on_discover")
    assert body["error"]["code"] == "PATIENT_NOT_FOUND"
    assert "patient" not in body
    assert _status("req-d3") == ("DISCOVERY_ERROR_SENT", "PATIENT_NOT_FOUND")


def test_two_patients_on_one_mobile_matching_is_ambiguous(world):
    # A shared family mobile, and a request close enough to both names.
    set_patient_mobile(patient_id=JANE, mobile=MOBILE)
    set_patient_mobile(patient_id=TWIN, mobile=MOBILE)
    with connection().cursor() as cur:
        cur.execute("UPDATE patients SET name = 'Jane9 Doe9', birth_date = '1990-01-01' WHERE patient_id = %s", (TWIN,))
    gw = world["gateway"]
    _run(svc.handle_discovery, gw, _discover("unknown@sbx"), "req-d4")

    assert gw.last("on_discover")["error"]["code"] == "MULTIPLE_PATIENTS_FOUND"


# -- the full linking flow ---------------------------------------------------------


def _discover_and_init(world, refs=("enc-new",)):
    gw = world["gateway"]
    _run(svc.handle_discovery, gw, _discover(), "req-d")
    _run(svc.handle_link_init, gw, _init(list(refs)), "req-i")
    return gw.last("on_init")["link"]["referenceNumber"]


def test_link_init_sends_otp_and_returns_link_reference(world):
    link_ref = _discover_and_init(world)

    body = world["gateway"].last("on_init")
    assert body["transactionId"] == "txn-1"
    assert body["response"] == {"requestId": "req-i"}
    assert body["link"]["authenticationType"] == "DIRECT"
    assert body["link"]["meta"]["communicationMedium"] == "MOBILE"
    assert link_ref
    [otp] = world["otps"]
    assert len(otp) == 6 and otp.isdigit()


def test_wrong_otp_is_refused_then_right_otp_links(world):
    link_ref = _discover_and_init(world)
    otp = world["otps"][0]
    gw = world["gateway"]
    wrong = "000000" if otp != "000000" else "111111"

    _run(svc.handle_link_confirm, gw, _confirm(link_ref, wrong), "req-c1")
    assert gw.last("on_confirm")["error"]["code"] == "OTP_INVALID"

    _run(svc.handle_link_confirm, gw, _confirm(link_ref, otp), "req-c2")
    body = gw.last("on_confirm")
    assert "error" not in body
    assert body["response"] == {"requestId": "req-c2"}
    assert body["patient"][0]["display"] == "Jane752 Doe514"
    assert _offered(body) == ["enc-new"]
    assert _status("req-c2") == ("LINK_CONFIRMED", None)

    linked = list_care_contexts(patient_id=JANE)
    assert [(c["reference_number"], c["status"], c["hip_id"]) for c in linked] == [
        ("enc-new", "LINKED", HIP_ID)
    ]


def test_linked_care_contexts_are_not_offered_again(world):
    link_ref = _discover_and_init(world)
    gw = world["gateway"]
    _run(svc.handle_link_confirm, gw, _confirm(link_ref, world["otps"][0]), "req-c")

    _run(svc.handle_discovery, gw, _discover(txn="txn-2"), "req-d2")
    assert _offered(gw.last("on_discover")) == ["enc-old"]


def test_otp_cannot_be_reused(world):
    link_ref = _discover_and_init(world)
    gw = world["gateway"]
    otp = world["otps"][0]
    _run(svc.handle_link_confirm, gw, _confirm(link_ref, otp), "req-c1")
    _run(svc.handle_link_confirm, gw, _confirm(link_ref, otp), "req-c2")

    assert gw.last("on_confirm")["error"]["code"] == "LINK_REFERENCE_UNKNOWN"
    assert len(list_care_contexts(patient_id=JANE)) == 1


def test_three_wrong_otps_lock_the_request(world):
    link_ref = _discover_and_init(world)
    gw = world["gateway"]
    otp = world["otps"][0]
    wrong = "000000" if otp != "000000" else "111111"

    codes = []
    for i in range(3):
        _run(svc.handle_link_confirm, gw, _confirm(link_ref, wrong), f"req-w{i}")
        codes.append(gw.last("on_confirm")["error"]["code"])
    _run(svc.handle_link_confirm, gw, _confirm(link_ref, otp), "req-right")

    assert codes == ["OTP_INVALID", "OTP_INVALID", "OTP_ATTEMPTS_EXCEEDED"]
    assert gw.last("on_confirm")["error"]["code"] in ("OTP_ATTEMPTS_EXCEEDED", "LINK_REFERENCE_UNKNOWN")
    assert list_care_contexts(patient_id=JANE) == []


def test_expired_otp_is_refused(world, monkeypatch):
    monkeypatch.setattr(svc, "OTP_TTL_SECONDS", -1)
    link_ref = _discover_and_init(world)
    gw = world["gateway"]

    _run(svc.handle_link_confirm, gw, _confirm(link_ref, world["otps"][0]), "req-c")

    assert gw.last("on_confirm")["error"]["code"] == "OTP_EXPIRED"
    assert list_care_contexts(patient_id=JANE) == []


def test_init_refuses_care_contexts_that_were_not_offered(world):
    gw = world["gateway"]
    _run(svc.handle_discovery, gw, _discover(), "req-d")

    _run(svc.handle_link_init, gw, _init(["enc-of-someone-else"]), "req-i1")
    assert gw.last("on_init")["error"]["code"] == "CARE_CONTEXT_NOT_OFFERED"

    _run(svc.handle_link_init, gw, _init(["enc-new"], patient_ref=TWIN), "req-i2")
    assert gw.last("on_init")["error"]["code"] == "CARE_CONTEXT_NOT_OFFERED"
    assert world["otps"] == []


def test_init_for_unknown_transaction_is_refused(world):
    gw = world["gateway"]
    _run(svc.handle_link_init, gw, _init(["enc-new"], txn="never-discovered"), "req-i")

    assert gw.last("on_init")["error"]["code"] == "UNKNOWN_TRANSACTION"


def test_reply_failure_is_recorded_not_raised(world):
    gw = world["gateway"]
    gw.fail_with = RuntimeError("gateway down")

    _run(svc.handle_discovery, gw, _discover(), "req-d")

    assert _status("req-d") == ("REPLY_FAILED", "RuntimeError")


# -- matching helpers -----------------------------------------------------------


def test_name_similarity_ignores_synthea_digits_case_and_order():
    assert svc.name_similarity("Abby752 Elenora790 Braun514", "braun abby elenora") == 1.0
    assert svc.name_similarity("Jane Doe", "Priya Sharma") < svc.NAME_MATCH_THRESHOLD


@pytest.mark.parametrize(
    "raw, expected",
    [("+91 98765-43210", "9876543210"), ("9876543210", "9876543210"), ("12345", None), (None, None)],
)
def test_normalize_mobile(raw, expected):
    assert svc.normalize_mobile(raw) == expected
