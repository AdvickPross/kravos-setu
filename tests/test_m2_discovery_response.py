from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import backend.m2_routes as m2_routes
from abha.errors import AbhaApiError, AbhaTransportError
from backend.main import app


@pytest.fixture
def api() -> TestClient:
    return TestClient(app)


@pytest.fixture
def mocked_m2(monkeypatch):
    """Mock ABDM calls and transaction storage."""
    upstream = Mock()
    upstream.respond_to_care_context_discovery.return_value = {
        "status": "accepted"
    }

    monkeypatch.setattr(
        m2_routes,
        "_client",
        lambda: upstream,
    )
    monkeypatch.setattr(
        m2_routes,
        "start_m2_transaction",
        Mock(return_value=1),
    )
    monkeypatch.setattr(
        m2_routes,
        "update_transaction",
        Mock(return_value=1),
    )

    return upstream


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer test-token",
        "REQUEST-ID": "123e4567-e89b-12d3-a456-426614174000",
        "TIMESTAMP": "2026-10-14T10:00:00.000Z",
        "X-CM-ID": "test-cm",
    }


def _success_payload() -> dict:
    return {
        "transactionId": "transaction-123",
        "patient": [
            {
                "referenceNumber": "patient-123",
                "display": "Test Patient",
                "careContexts": [
                    {
                        "referenceNumber": "visit-123",
                        "display": "Consultation",
                    }
                ],
                "hiType": "Prescription",
                "count": 1,
            }
        ],
        "matchedBy": ["MOBILE"],
        "response": {
            "requestId": "123e4567-e89b-12d3-a456-426614174000"
        },
    }


def test_discovery_response_forwards_payload_and_headers(
    api: TestClient,
    mocked_m2,
):
    response = api.post(
        "/api/v3/hiecm/user-initiated-linking/v3/patient/care-context/on-discover",
        json=_success_payload(),
        headers=_headers(),
    )

    assert response.status_code == 202
    mocked_m2.respond_to_care_context_discovery.assert_called_once()

    call = mocked_m2.respond_to_care_context_discovery.call_args
    assert call.kwargs["body"] == _success_payload()
    assert call.kwargs["extra_headers"] == _headers()

    m2_routes.start_m2_transaction.assert_called_once_with(
        patient_id=None,
        transaction_id="transaction-123",
        request_id=_headers()["REQUEST-ID"],
        operation="DISCOVERY_RESPONSE",
    )

    m2_routes.update_transaction.assert_called_once_with(
        request_id=_headers()["REQUEST-ID"],
        transaction_id="transaction-123",
        status="DISCOVERY_RESPONSE_SENT",
    )


def test_discovery_response_rejects_missing_required_header(
    api: TestClient,
    mocked_m2,
):
    headers = _headers()
    headers.pop("X-CM-ID")

    response = api.post(
        "/api/v3/hiecm/user-initiated-linking/v3/patient/care-context/on-discover",
        json=_success_payload(),
        headers=headers,
    )

    assert response.status_code == 400
    assert "X-CM-ID" in response.json()["detail"]
    mocked_m2.respond_to_care_context_discovery.assert_not_called()

@pytest.mark.parametrize(
    ("header_name", "expected_detail"),
    [
        ("Authorization", "Authorization"),
        ("REQUEST-ID", "REQUEST-ID"),
        ("TIMESTAMP", "TIMESTAMP"),
    ],
)
def test_discovery_response_rejects_missing_other_required_headers(
    api: TestClient,
    mocked_m2,
    header_name: str,
    expected_detail: str,
):
    headers = _headers()
    headers.pop(header_name)

    response = api.post(
        "/api/v3/hiecm/user-initiated-linking/v3/patient/care-context/on-discover",
        json=_success_payload(),
        headers=headers,
    )

    assert response.status_code == 400
    assert expected_detail in response.json()["detail"]
    mocked_m2.respond_to_care_context_discovery.assert_not_called()

def test_discovery_response_maps_abdm_api_error_to_502(
    api: TestClient,
    mocked_m2,
):
    mocked_m2.respond_to_care_context_discovery.side_effect = AbhaApiError(
        "Invalid discovery response",
        status_code=400,
        code="ABDM-4001",
    )

    response = api.post(
        "/api/v3/hiecm/user-initiated-linking/v3/patient/care-context/on-discover",
        json=_success_payload(),
        headers=_headers(),
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ABDM-4001"
    assert response.json()["detail"]["message"] == "Invalid discovery response"

    m2_routes.update_transaction.assert_called_once_with(
        request_id=_headers()["REQUEST-ID"],
        status="FAILED",
        error_code="ABDM-4001",
        error_message="Invalid discovery response",
    )


def test_discovery_response_maps_transport_error_to_502(
    api: TestClient,
    mocked_m2,
):
    mocked_m2.respond_to_care_context_discovery.side_effect = (
        AbhaTransportError("Connection failed")
    )

    response = api.post(
        "/api/v3/hiecm/user-initiated-linking/v3/patient/care-context/on-discover",
        json=_success_payload(),
        headers=_headers(),
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "ABDM service could not be reached"

    m2_routes.update_transaction.assert_called_once_with(
        request_id=_headers()["REQUEST-ID"],
        status="FAILED",
        error_code="TRANSPORT_ERROR",
        error_message="Connection failed",
    )


def test_discovery_response_rejects_invalid_payload(
    api: TestClient,
    mocked_m2,
):
    payload = _success_payload()
    payload["transactionId"] = ""

    response = api.post(
        "/api/v3/hiecm/user-initiated-linking/v3/patient/care-context/on-discover",
        json=payload,
        headers=_headers(),
    )

    assert response.status_code == 422
    mocked_m2.respond_to_care_context_discovery.assert_not_called()
    
def test_discovery_response_forwards_failure_payload(
    api: TestClient,
    mocked_m2,
):
    payload = {
        "transactionId": "transaction-123",
        "error": {
            "code": "ABDM-4001",
            "message": "Invalid discovery response",
        },
        "response": {
            "requestId": "123e4567-e89b-12d3-a456-426614174000"
        },
    }

    response = api.post(
        "/api/v3/hiecm/user-initiated-linking/v3/patient/care-context/on-discover",
        json=payload,
        headers=_headers(),
    )

    assert response.status_code == 202

    mocked_m2.respond_to_care_context_discovery.assert_called_once_with(
        body=payload,
        extra_headers=_headers(),
    )

def test_hiu_discovery_callback_preserves_legacy_payload(
    api: TestClient,
    monkeypatch,
):
    update_transaction = Mock(return_value=1)
    save_care_context = Mock()

    monkeypatch.setattr(
        m2_routes,
        "update_transaction",
        update_transaction,
    )
    monkeypatch.setattr(
        m2_routes,
        "save_care_context",
        save_care_context,
    )

    payload = {
        "transactionId": "transaction-123",
        "referenceNumber": "patient-123",
        "careContexts": [
            {
                "referenceNumber": "visit-123",
                "display": "Consultation",
            }
        ],
        "hiType": "Prescription",
    }

    response = api.post(
        "/api/v3/hiu/patient/care-context/on-discover",
        json=payload,
        headers={
            "X-CM-ID": "test-cm",
            "REQUEST-ID": "123e4567-e89b-12d3-a456-426614174000",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "SUCCESS",
        "transactionId": "transaction-123",
    }

    update_transaction.assert_called_once_with(
        request_id="123e4567-e89b-12d3-a456-426614174000",
        transaction_id="transaction-123",
        status="DISCOVERY_RECEIVED",
    )

    save_care_context.assert_called_once_with(
        patient_id=None,
        transaction_id="transaction-123",
        reference_number="visit-123",
        display="Consultation",
        hi_type="Prescription",
        status="DISCOVERED",
    )