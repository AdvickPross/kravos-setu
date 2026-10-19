"""Consent and data transfer (NHA HIP_INIT_SHARE_CARECONTEXT, consent
grant/revoke/expire) end to end against kravos_test.

The fake HIU holds its own key material and decrypts every page we push, so
the test checks what an HIU actually receives, not that encrypt() was called.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import backend.m2_data_flow as df
from abha import fidelius
from backend.abdm_store import get_consent, get_data_transfer, save_linked_care_context
from conftest import insert_facts, insert_patients

HIP_ID = "kravos-hip-1"
JANE = "patient-jane"
PUSH_URL = "https://hiu.example/data/push"


class FakeGateway:
    """ABDM gateway plus the HIU's push endpoint."""

    hip_id = HIP_ID

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.pushes: list[dict] = []
        self.push_headers: list[dict] = []
        self.push_fails = False
        self.client = SimpleNamespace(
            config=SimpleNamespace(hip_name="Kravos Setu"),
            session=SimpleNamespace(authorization=lambda: "Bearer gw-token"),
            transport=SimpleNamespace(send_json=self._push),
        )

    def _push(self, method, url, *, headers, json_body):
        if self.push_fails:
            raise RuntimeError("HIU down")
        assert (method, url) == ("POST", PUSH_URL)
        self.push_headers.append(headers)
        self.pushes.append(json_body)
        return {}

    def consent_on_notify(self, body, *, request_id):
        self.calls.append(("consent_on_notify", body))

    def health_information_on_request(self, body, *, request_id):
        self.calls.append(("on_request", body))

    def health_information_notify(self, body, *, request_id):
        self.calls.append(("notify", body))

    def last(self, name):
        return [b for n, b in self.calls if n == name][-1]

    def named(self, name):
        return [b for n, b in self.calls if n == name]


def _fact(fact_id, category, date, encounter_id, **extra):
    return {
        "fact_id": fact_id,
        "patient_id": JANE,
        "category": category,
        "code_system": "http://snomed.info/sct",
        "code": "38341003",
        "display": f"{category.title()} item",
        "date": datetime.fromisoformat(date).replace(tzinfo=timezone.utc),
        "encounter_id": encounter_id,
        **extra,
    }


@pytest.fixture
def world(use_test_db):
    insert_patients([{"patient_id": JANE, "name": "Jane752 Doe514", "birth_date": "1990-05-01", "gender": "female"}])
    insert_facts(
        [
            _fact("f1", "CONDITION", "2024-01-02T10:00:00", "enc-new", status="active"),
            _fact("f2", "VITAL", "2024-01-02T10:05:00", "enc-new", value="81", unit="/min",
                  code_system="http://loinc.org", code="8867-4", display="Heart rate"),
            _fact("f3", "REPORT", "2024-01-02T10:30:00", "enc-new", text="# Chief Complaint\nNo complaints."),
            _fact("f4", "LAB", "2023-06-01T09:00:00", "enc-old", value="5.4", unit="%"),
            _fact("f5", "ALLERGY", "2022-01-01T00:00:00", None, value="low"),
        ]
    )
    for ref in ("enc-new", "enc-old"):
        save_linked_care_context(
            patient_id=JANE, hip_id=HIP_ID, transaction_id="t", reference_number=ref, display=ref, hi_type="OPConsultation"
        )
    return SimpleNamespace(gateway=FakeGateway(), hiu=fidelius.generate_key_material())


def _grant(world, consent_id="consent-1", *, date_from="2024-01-01T00:00:00Z", date_to="2024-12-31T00:00:00Z",
           hi_types=("OPConsultation",), care_contexts=("enc-new", "enc-old"), hip=HIP_ID, erase="2099-01-01T00:00:00Z"):
    payload = {
        "notification": {
            "status": "GRANTED",
            "consentId": consent_id,
            "consentDetail": {
                "schemaVersion": "v3",
                "consentId": consent_id,
                "patient": {"id": "jane@sbx"},
                "careContexts": [{"patientReference": JANE, "careContextReference": c} for c in care_contexts],
                "hip": {"id": hip, "name": "Kravos Setu"},
                "hiu": {"id": "hiu-1", "name": "Some HIU"},
                "hiTypes": list(hi_types),
                "permission": {
                    "accessMode": "VIEW",
                    "dateRange": {"from": date_from, "to": date_to},
                    "dataEraseAt": erase,
                    "frequency": {"unit": "HOUR", "value": 1, "repeats": 0},
                },
            },
            "signature": "sig",
            "grantAcknowledgement": False,
        }
    }
    df.handle_consent_notification(world.gateway, payload, gateway_request_id=f"req-{consent_id}")
    return payload


def _hi_request(world, consent_id="consent-1", *, date_from="2020-01-01T00:00:00Z", date_to="2030-01-01T00:00:00Z",
                push_url=PUSH_URL, x509=True):
    return {
        "transactionId": "txn-1",
        "hiRequest": {
            "consent": {"id": consent_id},
            "dateRange": {"from": date_from, "to": date_to},
            "dataPushUrl": push_url,
            "keyMaterial": {
                "cryptoAlg": "ECDH",
                "curve": "curve25519",
                "dhPublicKey": {
                    "expiry": "2099-01-01T00:00:00Z",
                    "parameters": "Curve25519/32byte random key",
                    "keyValue": world.hiu.public_key(x509=x509),
                },
                "nonce": world.hiu.nonce_b64,
            },
        },
    }


def _decrypt(world, page):
    """What the HIU does with one pushed page."""
    [entry] = page["entries"]
    plain = fidelius.decrypt(
        entry["content"],
        requester_private_key=world.hiu.private_key,
        requester_nonce=world.hiu.nonce,
        sender_public_key=page["keyMaterial"]["dhPublicKey"]["keyValue"],
        sender_nonce=base64.b64decode(page["keyMaterial"]["nonce"]),
    )
    assert hashlib.md5(plain).hexdigest() == entry["checksum"]
    return entry["careContextReference"], json.loads(plain)


def _statuses(world):
    return {
        s["careContextReference"]: s["hiStatus"]
        for s in world.gateway.last("notify")["notification"]["statusNotification"]["statusResponses"]
    }


# -- consent ---------------------------------------------------------------------


def test_granted_consent_is_stored_and_acknowledged(world):
    _grant(world)

    stored = get_consent("consent-1")
    assert stored["hip_id"] == HIP_ID and stored["hi_types"] == ["OPConsultation"]
    assert stored["date_from"] == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert world.gateway.last("consent_on_notify") == {
        "acknowledgement": {"status": "OK", "consentId": "consent-1"},
        "response": {"requestId": "req-consent-1"},
    }


@pytest.mark.parametrize("status", ["REVOKED", "EXPIRED", "DENIED"])
def test_revoked_or_expired_consent_is_deleted(world, status):
    _grant(world)
    df.handle_consent_notification(
        world.gateway,
        {"notification": {"status": status, "consentId": "consent-1"}},
        gateway_request_id="req-2",
    )
    assert get_consent("consent-1") is None
    assert world.gateway.last("consent_on_notify")["acknowledgement"]["consentId"] == "consent-1"


def test_consent_for_another_hip_is_not_stored(world):
    _grant(world, hip="someone-else")
    assert get_consent("consent-1") is None


# -- transfer -------------------------------------------------------------------------


def test_transfer_delivers_decryptable_fhir_for_consented_range(world):
    _grant(world)  # consent covers 2024 only
    df.handle_health_information_request(world.gateway, _hi_request(world), gateway_request_id="req-hi")

    assert world.gateway.last("on_request") == {
        "hiRequest": {"transactionId": "txn-1", "sessionStatus": "ACKNOWLEDGED"},
        "response": {"requestId": "req-hi"},
    }
    [page] = world.gateway.pushes
    assert (page["pageNumber"], page["pageCount"], page["transactionId"]) == (1, 1, "txn-1")
    assert world.gateway.push_headers[0]["Authorization"] == "Bearer gw-token"

    reference, bundle = _decrypt(world, page)
    assert reference == "enc-new"
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "document"
    composition = bundle["entry"][0]["resource"]
    assert composition["resourceType"] == "Composition"
    assert composition["meta"]["profile"][0].endswith("/OPConsultRecord")
    kinds = sorted(e["resource"]["resourceType"] for e in bundle["entry"][4:])
    # 2024 facts only: the 2022 allergy is outside the consented range.
    assert kinds == ["Condition", "DocumentReference", "Observation"]

    # enc-old (2023) is outside the consent's range: reported, not sent.
    assert _statuses(world) == {"enc-new": "DELIVERED", "enc-old": "ERRORED"}
    notification = world.gateway.last("notify")["notification"]
    assert notification["notifier"] == {"type": "HIP", "id": HIP_ID}
    assert notification["statusNotification"]["sessionStatus"] == "TRANSFERRED"
    assert get_data_transfer("txn-1")["status"] == "TRANSFERRED"


def test_every_page_has_its_own_key_material(world):
    _grant(world, date_from="2020-01-01T00:00:00Z")
    df.handle_health_information_request(world.gateway, _hi_request(world), gateway_request_id="req-hi")

    pages = world.gateway.pushes
    assert [p["pageCount"] for p in pages] == [2, 2]
    assert len({p["keyMaterial"]["nonce"] for p in pages}) == 2
    assert len({p["keyMaterial"]["dhPublicKey"]["keyValue"] for p in pages}) == 2
    assert sorted(_decrypt(world, p)[0] for p in pages) == ["enc-new", "enc-old"]


def test_raw_point_requester_gets_a_raw_point_back(world):
    _grant(world)
    df.handle_health_information_request(world.gateway, _hi_request(world, x509=False), gateway_request_id="req-hi")
    key = world.gateway.pushes[0]["keyMaterial"]["dhPublicKey"]["keyValue"]
    assert len(base64.b64decode(key)) == 65
    _decrypt(world, world.gateway.pushes[0])


def test_revoked_consent_shares_nothing(world):
    _grant(world)
    df.handle_consent_notification(
        world.gateway, {"notification": {"status": "REVOKED", "consentId": "consent-1"}}, gateway_request_id="r"
    )
    df.handle_health_information_request(world.gateway, _hi_request(world), gateway_request_id="req-hi")

    ack = world.gateway.last("on_request")
    assert ack["hiRequest"]["sessionStatus"] == "ERRORED"
    assert ack["error"]["code"] == "ABDM-9999"
    assert world.gateway.pushes == []
    assert get_data_transfer("txn-1")["status"] == "REFUSED"


def test_erased_consent_shares_nothing(world):
    _grant(world, erase="2001-01-01T00:00:00Z")
    df.handle_health_information_request(world.gateway, _hi_request(world), gateway_request_id="req-hi")
    assert world.gateway.last("on_request")["hiRequest"]["sessionStatus"] == "ERRORED"
    assert world.gateway.pushes == []


def test_request_outside_the_consented_range_is_refused(world):
    _grant(world)
    df.handle_health_information_request(
        world.gateway,
        _hi_request(world, date_from="2019-01-01T00:00:00Z", date_to="2019-12-31T00:00:00Z"),
        gateway_request_id="req-hi",
    )
    assert world.gateway.last("on_request")["hiRequest"]["sessionStatus"] == "ERRORED"
    assert world.gateway.pushes == []


def test_non_consented_hi_type_is_not_shared(world):
    _grant(world, hi_types=("Prescription",))
    df.handle_health_information_request(world.gateway, _hi_request(world), gateway_request_id="req-hi")
    assert world.gateway.pushes == []
    assert set(_statuses(world).values()) == {"ERRORED"}
    assert world.gateway.last("notify")["notification"]["statusNotification"]["sessionStatus"] == "FAILED"


def test_unlinked_care_context_is_not_shared(world):
    _grant(world, care_contexts=("enc-unlinked",))
    df.handle_health_information_request(world.gateway, _hi_request(world), gateway_request_id="req-hi")
    assert world.gateway.pushes == []
    assert _statuses(world) == {"enc-unlinked": "ERRORED"}


def test_plain_http_push_url_is_refused(world):
    _grant(world)
    df.handle_health_information_request(
        world.gateway, _hi_request(world, push_url="http://hiu.example/push"), gateway_request_id="req-hi"
    )
    assert world.gateway.last("on_request")["hiRequest"]["sessionStatus"] == "ERRORED"


def test_failed_push_is_reported_per_care_context(world):
    _grant(world)
    world.gateway.push_fails = True
    df.handle_health_information_request(world.gateway, _hi_request(world), gateway_request_id="req-hi")

    assert _statuses(world)["enc-new"] == "ERRORED"
    assert world.gateway.last("notify")["notification"]["statusNotification"]["sessionStatus"] == "FAILED"


# -- the FHIR itself -----------------------------------------------------------------


def test_bundle_resources_carry_the_corpus_codes_and_values(world):
    _grant(world)
    df.handle_health_information_request(world.gateway, _hi_request(world), gateway_request_id="req-hi")
    _, bundle = _decrypt(world, world.gateway.pushes[0])
    resources = {e["resource"]["resourceType"]: e["resource"] for e in bundle["entry"]}

    vital = resources["Observation"]
    assert vital["code"]["coding"][0] == {"system": "http://loinc.org", "code": "8867-4", "display": "Heart rate"}
    assert vital["valueQuantity"]["value"] == 81.0 and vital["valueQuantity"]["unit"] == "/min"
    note = resources["DocumentReference"]["content"][0]["attachment"]
    assert base64.b64decode(note["data"]).decode() == "# Chief Complaint\nNo complaints."
    assert resources["Patient"]["gender"] == "female"
    assert resources["Organization"]["identifier"][0]["value"] == HIP_ID
    # Every section entry resolves to a resource in the bundle.
    urls = {e["fullUrl"] for e in bundle["entry"]}
    for section in resources["Composition"]["section"]:
        assert all(ref["reference"] in urls for ref in section["entry"])
