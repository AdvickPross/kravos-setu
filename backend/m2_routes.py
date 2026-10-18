"""ABDM M2 (Building HIP) routes.

Two routers, because they have two different callers and two different
trust models:

* ``m2_router`` (``/api/m2``) is called by **our own app**. It starts
  HIP-initiated operations. Request bodies carry only clinical/identity data.
  Every ABDM header (session token, X-HIP-ID, X-CM-ID, REQUEST-ID) comes from
  server config, so nobody calling this server can act as the HIP.
* ``gateway_router`` (``/api/v3``) is called by **the ABDM gateway**. Every
  route requires a gateway-signed JWT (``require_gateway``). These paths are
  fixed by ABDM, relative to the bridge URL registered for the HIP.

ABDM is asynchronous. An outbound call returns 202, and the answer arrives
later on a callback whose body carries ``response.requestId``, which is the
REQUEST-ID we sent. That id is the correlation key for ``abdm_transactions``.
The callback's own REQUEST-ID header is a new id minted by the gateway, so it
is not used for correlation.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from abha.config import AbhaConfig, AbhaConfigError
from abha.errors import AbhaApiError, AbhaTransportError
from abha.gateway_auth import GatewayAuthError, GatewayTokenVerifier
from abha.m2_client import AbdmM2Client
from abha.m2_models import (
    HipDiscoveryCallbackRequest,
    LinkCareContextRequest,
    LinkConfirmRequest,
    LinkInitRequest,
    TokenRequest,
    TokenResponse,
)
from abha.transport import new_request_id
from backend.abdm_store import (
    get_link_token,
    save_link_token,
    set_patient_mobile,
    update_transaction,
)
from backend.data_layer import get_patient
from backend.m2_service import (
    handle_discovery,
    handle_link_confirm,
    handle_link_init,
    normalize_mobile,
    start_m2_transaction,
)

log = logging.getLogger(__name__)

m2_router = APIRouter(prefix="/api/m2", tags=["ABDM M2"])


def _config() -> AbhaConfig:
    from backend.abha_routes import _client as get_abha_client

    return get_abha_client().config


def _client() -> AbdmM2Client:
    """The M2 client, sharing M1's session and transport."""
    from backend.abha_routes import _client as get_abha_client

    return AbdmM2Client(get_abha_client())


def _hip_id() -> str:
    try:
        return _client().hip_id
    except AbhaConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# -- gateway authentication ---------------------------------------------------

_verifier: GatewayTokenVerifier | None = None
_verifier_lock = threading.Lock()


def _gateway_verifier() -> GatewayTokenVerifier:
    global _verifier
    if _verifier is None:
        with _verifier_lock:
            if _verifier is None:
                config = _config()
                _verifier = GatewayTokenVerifier(
                    jwks_url=config.gateway_jwks_url,
                    issuer=config.gateway_issuer,
                )
    return _verifier


def require_gateway(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    """Reject any callback not carrying a valid gateway-signed JWT."""
    if not _config().verify_gateway_tokens:
        return {}
    try:
        return _gateway_verifier().verify(authorization)
    except GatewayAuthError as exc:
        log.warning("rejected M2 callback: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc


gateway_router = APIRouter(
    prefix="/api/v3",
    tags=["ABDM M2 gateway callbacks"],
    dependencies=[Depends(require_gateway)],
)


def _require_header(value: str | None, header_name: str) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=400, detail=f"Missing {header_name} header")
    return value.strip()


def _require_own_hip_id(x_hip_id: str | None) -> None:
    """The gateway addresses each callback to one HIP; it must be us."""
    received = _require_header(x_hip_id, "X-HIP-ID")
    if received != _hip_id():
        raise HTTPException(status_code=400, detail="X-HIP-ID is not this HIP")


def _send(operation: str, request_id: str, call) -> dict[str, Any]:
    """Run an outbound ABDM call, recording failure against the transaction."""
    try:
        return call()
    except AbhaConfigError as exc:
        update_transaction(
            request_id=request_id,
            status="FAILED",
            error_code="CONFIG",
            error_message=str(exc),
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AbhaApiError as exc:
        update_transaction(
            request_id=request_id,
            status="FAILED",
            error_code=exc.code,
            error_message=exc.message,
        )
        raise HTTPException(
            status_code=502,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except AbhaTransportError as exc:
        update_transaction(
            request_id=request_id,
            status="FAILED",
            error_code="TRANSPORT_ERROR",
            error_message=str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail=f"ABDM service could not be reached ({operation})",
        ) from exc


# -- app-facing: HIP-initiated linking ------------------------------------------


@m2_router.post("/link-token", status_code=202)
def generate_link_token(payload: TokenRequest) -> dict[str, str]:
    """Ask ABDM for a link token for one ABHA address.

    The token arrives later on ``/api/v3/hip/token/on-generate-token``.
    Poll the transaction by the returned ``requestId``.
    """
    client = _client()
    request_id = new_request_id()
    start_m2_transaction(
        patient_id=payload.patient_id,
        transaction_id=None,
        request_id=request_id,
        operation="LINK_TOKEN_GENERATION",
    )
    body = payload.model_dump(by_alias=True, exclude_none=True)
    _send(
        "generate-token",
        request_id,
        lambda: client.generate_link_token(body, request_id=request_id),
    )
    return {"status": "PENDING", "requestId": request_id}


@m2_router.post("/link-care-contexts", status_code=202)
def link_care_contexts(payload: LinkCareContextRequest) -> dict[str, str]:
    """Link care contexts to an ABHA address using its stored link token."""
    client = _client()
    link_token = get_link_token(abha_address=payload.abha_address, hip_id=_hip_id())
    if link_token is None:
        raise HTTPException(
            status_code=409,
            detail="No valid link token for this ABHA address; "
            "call /api/m2/link-token first",
        )

    request_id = new_request_id()
    start_m2_transaction(
        patient_id=payload.patient_id,
        transaction_id=None,
        request_id=request_id,
        operation="LINK_CARE_CONTEXT",
    )
    body = payload.model_dump(by_alias=True, exclude_none=True)
    _send(
        "link/carecontext",
        request_id,
        lambda: client.link_care_context(
            body, link_token=link_token, request_id=request_id
        ),
    )
    return {"status": "PENDING", "requestId": request_id}


class PatientMobile(BaseModel):
    mobile: str = Field(..., min_length=10)


@m2_router.put("/patients/{patient_id}/mobile")
def put_patient_mobile(patient_id: str, payload: PatientMobile) -> dict[str, str]:
    """Record the mobile captured at registration.

    User-initiated discovery falls back to it when the ABHA address isn't
    linked here (USER_INIT_LINK_603), and the linking OTP is sent to it.
    """
    mobile = normalize_mobile(payload.mobile)
    if mobile is None:
        raise HTTPException(status_code=422, detail="mobile must have at least 10 digits")
    try:
        get_patient(patient_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown patient_id: {patient_id}") from None
    set_patient_mobile(patient_id=patient_id, mobile=mobile)
    return {"patientId": patient_id, "mobile": f"******{mobile[-4:]}"}


# -- gateway callbacks ---------------------------------------------------------


@gateway_router.post("/hip/token/on-generate-token", status_code=202)
def handle_link_token_callback(
    payload: TokenResponse,
    x_hip_id: str | None = Header(default=None, alias="X-HIP-ID"),
    request_id: str | None = Header(default=None, alias="REQUEST-ID"),
    timestamp: str | None = Header(default=None, alias="TIMESTAMP"),
) -> dict[str, str]:
    """Receive the link token ABDM generated for our generate-token call."""
    _require_header(request_id, "REQUEST-ID")
    _require_header(timestamp, "TIMESTAMP")
    _require_own_hip_id(x_hip_id)

    original_request_id = (payload.response or {}).get("requestId")
    if not original_request_id:
        raise HTTPException(status_code=400, detail="Missing response.requestId")

    if payload.error:
        updated = update_transaction(
            request_id=original_request_id,
            status="TOKEN_GENERATION_FAILED",
            error_code=str(payload.error.get("code", "UNKNOWN_ERROR")),
            error_message=str(
                payload.error.get("message", "ABDM link token generation failed")
            ),
        )
        if updated == 0:
            raise HTTPException(status_code=404, detail="No matching ABDM transaction")
        return {"status": "ERROR", "requestId": original_request_id}

    if not payload.link_token or not payload.abha_address:
        update_transaction(
            request_id=original_request_id,
            status="TOKEN_GENERATION_FAILED",
            error_code="INVALID_CALLBACK",
            error_message="linkToken or abhaAddress missing from callback",
        )
        raise HTTPException(
            status_code=400, detail="linkToken and abhaAddress are required"
        )

    updated = update_transaction(
        request_id=original_request_id,
        status="TOKEN_GENERATED",
    )
    if updated == 0:
        # Not ours: never store a token we did not ask for.
        raise HTTPException(status_code=404, detail="No matching ABDM transaction")

    save_link_token(
        abha_address=payload.abha_address,
        hip_id=_hip_id(),
        link_token=payload.link_token,
    )
    return {"status": "SUCCESS", "requestId": original_request_id}


@gateway_router.post("/hip/patient/care-context/discover", status_code=202)
def handle_hip_care_context_discovery(
    payload: HipDiscoveryCallbackRequest,
    background: BackgroundTasks,
    x_hip_id: str | None = Header(default=None, alias="X-HIP-ID"),
    request_id: str | None = Header(default=None, alias="REQUEST-ID"),
    timestamp: str | None = Header(default=None, alias="TIMESTAMP"),
) -> dict[str, str]:
    """USER_INIT_LINK_603/604: find the patient, answer on-discover."""
    validated_request_id = _require_header(request_id, "REQUEST-ID")
    _require_header(timestamp, "TIMESTAMP")
    _require_own_hip_id(x_hip_id)

    start_m2_transaction(
        patient_id=None,
        transaction_id=payload.transaction_id,
        request_id=validated_request_id,
        operation="DISCOVERY_REQUEST",
    )
    background.add_task(
        handle_discovery, _client(), payload, gateway_request_id=validated_request_id
    )
    return {"status": "ACCEPTED", "transactionId": payload.transaction_id}


@gateway_router.post("/hip/link/care-context/init", status_code=202)
def handle_link_care_context_init(
    payload: LinkInitRequest,
    background: BackgroundTasks,
    x_hip_id: str | None = Header(default=None, alias="X-HIP-ID"),
    request_id: str | None = Header(default=None, alias="REQUEST-ID"),
    timestamp: str | None = Header(default=None, alias="TIMESTAMP"),
) -> dict[str, str]:
    """USER_INIT_LINK_605, step 1: send the patient an OTP, answer on-init."""
    validated_request_id = _require_header(request_id, "REQUEST-ID")
    _require_header(timestamp, "TIMESTAMP")
    _require_own_hip_id(x_hip_id)

    start_m2_transaction(
        patient_id=None,
        transaction_id=payload.transaction_id,
        request_id=validated_request_id,
        operation="LINK_INIT_REQUEST",
    )
    background.add_task(
        handle_link_init, _client(), payload, gateway_request_id=validated_request_id
    )
    return {"status": "ACCEPTED", "transactionId": payload.transaction_id}


@gateway_router.post("/hip/link/care-context/confirm", status_code=202)
def handle_link_care_context_confirm(
    payload: LinkConfirmRequest,
    background: BackgroundTasks,
    x_hip_id: str | None = Header(default=None, alias="X-HIP-ID"),
    request_id: str | None = Header(default=None, alias="REQUEST-ID"),
    timestamp: str | None = Header(default=None, alias="TIMESTAMP"),
) -> dict[str, str]:
    """USER_INIT_LINK_605, step 2: check the OTP, link, answer on-confirm."""
    validated_request_id = _require_header(request_id, "REQUEST-ID")
    _require_header(timestamp, "TIMESTAMP")
    _require_own_hip_id(x_hip_id)

    start_m2_transaction(
        patient_id=None,
        transaction_id=None,
        request_id=validated_request_id,
        operation="LINK_CONFIRM_REQUEST",
    )
    background.add_task(
        handle_link_confirm, _client(), payload, gateway_request_id=validated_request_id
    )
    return {"status": "ACCEPTED"}
