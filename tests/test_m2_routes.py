"""M2 routes: app-facing calls use server-side identity; gateway callbacks
are authenticated and correlated by response.requestId."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import backend.m2_routes as m2_routes
from abha.client import AbhaClient
from abha.config import AbhaConfig, AbhaConfigError
from abha.errors import AbhaApiError
from abha.gateway_auth import GatewayAuthError
from abha.m2_client import AbdmM2Client
from backend.abdm_store import get_link_token, save_link_token
from backend.main import app

HIP_ID = "kravos-hip-1"
OUR_REQUEST_ID = "11111111-1111-4111-8111-111111111111"
GATEWAY_REQUEST_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def api() -> TestClient:
    return TestClient(app)


@pytest.fixture
def m2(monkeypatch):
    """Mock the ABDM client and the store; gateway auth accepts everything."""
    upstream = Mock()
    upstream.hip_id = HIP_ID
    upstream.generate_link_token.return_value = {}
    upstream.link_care_context.return_value = {}
    config = SimpleNamespace(verify_gateway_tokens=True)

    monkeypatch.setattr(m2_routes, "_client", lambda: upstream)
    monkeypatch.setattr(m2_routes, "_config", lambda: config)
    monkeypatch.setattr(m2_routes, "new_request_id", lambda: OUR_REQUEST_ID)
    monkeypatch.setattr(m2_routes, "start_m2_transaction", Mock(return_value=1))
    monkeypatch.setattr(m2_routes, "update_transaction", Mock(return_value=1))
    monkeypatch.setattr(m2_routes, "save_link_token", Mock())
    monkeypatch.setattr(m2_routes, "get_link_token", Mock(return_value="link-tok"))
    for handler in ("handle_discovery", "handle_link_init", "handle_link_confirm"):
        monkeypatch.setattr(m2_routes, handler, Mock())
    verifier = Mock()
    verifier.verify.return_value = {"iss": "gateway"}
    monkeypatch.setattr(m2_routes, "_gateway_verifier", lambda: verifier)
    upstream.verifier = verifier
    return upstream


def _callback_headers(**overrides) -> dict[str, str]:
    headers = {
        "Authorization": "Bearer gateway-jwt",
        "REQUEST-ID": GATEWAY_REQUEST_ID,
        "TIMESTAMP": "2026-10-18T10:00:00.000Z",
        "X-HIP-ID": HIP_ID,
        "X-CM-ID": "sbx",
    }
    headers.update(overrides)
    return {k: v for k, v in headers.items() if v is not None}


def _token_request() -> dict:
    return {
        "patientId": "patient-1",
        "abhaAddress": "jane@sbx",
        "name": "Jane Doe",
        "gender": "F",
        "yearOfBirth": 1990,
    }


def _link_request() -> dict:
    return {
        "patientId": "patient-1",
        "abhaAddress": "jane@sbx",
        "patient": [
            {
                "referenceNumber": "patient-1",
                "display": "Jane Doe",
                "careContexts": [{"referenceNumber": "visit-1", "display": "OPD 2026-10-01"}],
                "hiType": "OPConsultation",
                "count": 1,
            }
        ],
    }


# -- the M2 client: host and headers -----------------------------------------


def test_gateway_host_resolves_to_hiecm_not_abha():
    config = AbhaConfig(client_id="x", client_secret="y")
    client = AbhaClient(config, transport=Mock())
    assert (
        client._url("/api/hiecm/v3/token/generate-token", "gateway")
        == "https://dev.abdm.gov.in/api/hiecm/v3/token/generate-token"
    )


def test_m2_client_sends_server_identity_to_the_gateway():
    abha = Mock()
    abha.config = AbhaConfig(client_id="x", client_secret="y", hip_id=HIP_ID)
    AbdmM2Client(abha).link_care_context(
        {"abhaAddress": "jane@sbx"}, link_token="link-tok", request_id=OUR_REQUEST_ID
    )
    call = abha.post.call_args
    assert call.args[0] == "/api/hiecm/hip/v3/link/carecontext"
    assert call.kwargs["host"] == "gateway"
    assert call.kwargs["extra_headers"] == {
        "REQUEST-ID": OUR_REQUEST_ID,
        "X-CM-ID": "sbx",
        "X-HIP-ID": HIP_ID,
        "X-LINK-TOKEN": "link-tok",
    }
    # Authorization is never set here: AbhaClient adds the session token.
    assert "Authorization" not in call.kwargs["extra_headers"]


def test_m2_client_refuses_to_send_without_a_hip_id():
    abha = Mock()
    abha.config = AbhaConfig(client_id="x", client_secret="y")
    with pytest.raises(AbhaConfigError, match="ABDM_HIP_ID"):
        AbdmM2Client(abha).generate_link_token({}, request_id=OUR_REQUEST_ID)
    abha.post.assert_not_called()


# -- app-facing routes -----------------------------------------------------------


def test_link_token_uses_server_request_id_and_strips_our_fields(api, m2):
    response = api.post(
        "/api/m2/link-token",
        json=_token_request(),
        # Caller-supplied ABDM headers must have no effect.
        headers={"Authorization": "Bearer attacker", "X-HIP-ID": "someone-else"},
    )

    assert response.status_code == 202
    assert response.json() == {"status": "PENDING", "requestId": OUR_REQUEST_ID}
    m2_routes.start_m2_transaction.assert_called_once_with(
        patient_id="patient-1",
        transaction_id=None,
        request_id=OUR_REQUEST_ID,
        operation="LINK_TOKEN_GENERATION",
    )
    call = m2.generate_link_token.call_args
    assert call.kwargs == {"request_id": OUR_REQUEST_ID}
    assert "patientId" not in call.args[0]
    assert call.args[0]["abhaAddress"] == "jane@sbx"


def test_link_token_maps_abdm_error_to_502_and_fails_the_transaction(api, m2):
    m2.generate_link_token.side_effect = AbhaApiError(
        "bad demographics", status_code=400, code="ABDM-1001"
    )

    response = api.post("/api/m2/link-token", json=_token_request())

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ABDM-1001"
    m2_routes.update_transaction.assert_called_once_with(
        request_id=OUR_REQUEST_ID,
        status="FAILED",
        error_code="ABDM-1001",
        error_message="bad demographics",
    )


def test_link_care_contexts_needs_a_stored_token(api, m2):
    m2_routes.get_link_token.return_value = None

    response = api.post("/api/m2/link-care-contexts", json=_link_request())

    assert response.status_code == 409
    m2.link_care_context.assert_not_called()


def test_link_care_contexts_sends_the_stored_token(api, m2):
    response = api.post("/api/m2/link-care-contexts", json=_link_request())

    assert response.status_code == 202
    m2_routes.get_link_token.assert_called_once_with(abha_address="jane@sbx", hip_id=HIP_ID)
    call = m2.link_care_context.call_args
    assert call.kwargs == {"link_token": "link-tok", "request_id": OUR_REQUEST_ID}
    assert "linkToken" not in call.args[0]


# -- gateway callbacks ---------------------------------------------------------


def _token_callback(**overrides) -> dict:
    body = {
        "abhaAddress": "jane@sbx",
        "linkToken": "fresh-link-token",
        "response": {"requestId": OUR_REQUEST_ID},
    }
    body.update(overrides)
    return body


def test_token_callback_correlates_by_response_request_id_and_stores_token(api, m2):
    response = api.post(
        "/api/v3/hip/token/on-generate-token",
        json=_token_callback(),
        headers=_callback_headers(),
    )

    assert response.status_code == 202
    # Correlated by the id WE sent, not the gateway's REQUEST-ID header.
    m2_routes.update_transaction.assert_called_once_with(
        request_id=OUR_REQUEST_ID, status="TOKEN_GENERATED"
    )
    m2_routes.save_link_token.assert_called_once_with(
        abha_address="jane@sbx", hip_id=HIP_ID, link_token="fresh-link-token"
    )


def test_token_callback_for_unknown_request_is_not_stored(api, m2):
    m2_routes.update_transaction.return_value = 0

    response = api.post(
        "/api/v3/hip/token/on-generate-token",
        json=_token_callback(),
        headers=_callback_headers(),
    )

    assert response.status_code == 404
    m2_routes.save_link_token.assert_not_called()


def test_token_callback_error_marks_transaction_failed(api, m2):
    response = api.post(
        "/api/v3/hip/token/on-generate-token",
        json={
            "error": {"code": "ABDM-1204", "message": "Demographic mismatch"},
            "response": {"requestId": OUR_REQUEST_ID},
        },
        headers=_callback_headers(),
    )

    assert response.status_code == 202
    assert response.json()["status"] == "ERROR"
    m2_routes.update_transaction.assert_called_once_with(
        request_id=OUR_REQUEST_ID,
        status="TOKEN_GENERATION_FAILED",
        error_code="ABDM-1204",
        error_message="Demographic mismatch",
    )
    m2_routes.save_link_token.assert_not_called()


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/v3/hip/token/on-generate-token", _token_callback()),
        (
            "/api/v3/hip/patient/care-context/discover",
            {
                "transactionId": "t-1",
                "patient": {
                    "id": "jane@sbx",
                    "verifiedIdentifiers": [{"type": "MOBILE", "value": "9999999999"}],
                    "unverifiedIdentifiers": [],
                    "name": "Jane Doe",
                    "gender": "F",
                    "yearOfBirth": 1990,
                },
            },
        ),
        ("/api/v3/hip/link/care-context/init", {"transactionId": "t-1", **_link_request()}),
        (
            "/api/v3/hip/link/care-context/confirm",
            {"confirmation": {"linkRefNumber": "ref-1", "token": "123456"}},
        ),
    ],
)
def test_callbacks_reject_tokens_the_gateway_did_not_sign(api, m2, path, body):
    m2.verifier.verify.side_effect = GatewayAuthError("token signature is invalid")

    response = api.post(path, json=body, headers=_callback_headers())

    assert response.status_code == 401
    m2_routes.start_m2_transaction.assert_not_called()
    m2_routes.update_transaction.assert_not_called()
    m2_routes.save_link_token.assert_not_called()
    m2_routes.handle_discovery.assert_not_called()
    m2_routes.handle_link_init.assert_not_called()
    m2_routes.handle_link_confirm.assert_not_called()


def test_callbacks_addressed_to_another_hip_are_refused(api, m2):
    response = api.post(
        "/api/v3/hip/token/on-generate-token",
        json=_token_callback(),
        headers=_callback_headers(**{"X-HIP-ID": "other-hip"}),
    )

    assert response.status_code == 400
    m2_routes.save_link_token.assert_not_called()


def test_removed_relay_routes_are_gone(api):
    for path in (
        "/api/v3/hiecm/v3/token/generate-token",
        "/api/v3/hiecm/hip/v3/link/carecontext",
        "/api/v3/hiecm/user-initiated-linking/v3/patient/care-context/discover",
        "/api/v3/hiecm/user-initiated-linking/v3/link/care-context/init",
        "/api/v3/hiecm/user-initiated-linking/v3/link/care-context/confirm",
        "/api/v3/hiecm/user-initiated-linking/v3/link/care-context/on-init",
        "/api/v3/hiecm/user-initiated-linking/v3/patient/care-context/on-discover",
        "/api/v3/hiu/patient/care-context/on-discover",
        "/api/v3/hiu/patient/care-context/on-init",
    ):
        assert api.post(path, json={}).status_code in (404, 405), path


@pytest.mark.parametrize(
    "path, body, handler",
    [
        (
            "/api/v3/hip/patient/care-context/discover",
            {"transactionId": "t-1", "patient": {"id": "jane@sbx", "name": "Jane", "gender": "F", "yearOfBirth": 1990}},
            "handle_discovery",
        ),
        ("/api/v3/hip/link/care-context/init", {"transactionId": "t-1", **_link_request()}, "handle_link_init"),
        (
            "/api/v3/hip/link/care-context/confirm",
            {"confirmation": {"linkRefNumber": "ref-1", "token": "123456"}},
            "handle_link_confirm",
        ),
    ],
)
def test_callbacks_answer_202_and_hand_off_with_the_gateway_request_id(api, m2, path, body, handler):
    response = api.post(path, json=body, headers=_callback_headers())

    assert response.status_code == 202
    mock = getattr(m2_routes, handler)
    mock.assert_called_once()
    assert mock.call_args.args[0] is m2
    # The reply must carry the gateway's REQUEST-ID as response.requestId.
    assert mock.call_args.kwargs == {"gateway_request_id": GATEWAY_REQUEST_ID}


def test_patient_mobile_is_normalised_and_masked(api, monkeypatch):
    saved = Mock()
    monkeypatch.setattr(m2_routes, "set_patient_mobile", saved)
    monkeypatch.setattr(m2_routes, "get_patient", Mock())

    response = api.put("/api/m2/patients/p-1/mobile", json={"mobile": "+91 98765-43210"})

    assert response.status_code == 200
    assert response.json() == {"patientId": "p-1", "mobile": "******3210"}
    saved.assert_called_once_with(patient_id="p-1", mobile="9876543210")


def test_patient_mobile_for_unknown_patient_is_404(api, monkeypatch):
    monkeypatch.setattr(m2_routes, "get_patient", Mock(side_effect=KeyError("p-x")))
    monkeypatch.setattr(m2_routes, "set_patient_mobile", Mock())

    response = api.put("/api/m2/patients/p-x/mobile", json={"mobile": "9876543210"})

    assert response.status_code == 404
    m2_routes.set_patient_mobile.assert_not_called()


# -- link token store ----------------------------------------------------------


def test_link_token_store_round_trip_and_replace(use_test_db):
    assert get_link_token(abha_address="jane@sbx", hip_id=HIP_ID) is None

    save_link_token(abha_address="jane@sbx", hip_id=HIP_ID, link_token="first")
    save_link_token(abha_address="jane@sbx", hip_id=HIP_ID, link_token="second")

    assert get_link_token(abha_address="jane@sbx", hip_id=HIP_ID) == "second"
    assert get_link_token(abha_address="jane@sbx", hip_id="other-hip") is None
