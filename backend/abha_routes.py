"""HTTP surface for the ABHA identity flows (M1).

Thin by design, in the same way `backend/main.py` is thin: these routes
validate input, call one function in `abha/`, and shape the response. No flow
logic lives here -- if a route contains a second ABDM call, it belongs in the
flow module instead.

Three decisions are load-bearing:

**The client is a process-wide singleton.** Not per-request. The session
token is cached on it, and rebuilding the client per request would fetch a
new token per request -- doubling the call count against a rate-limited
sandbox, for no benefit. `_client()` builds it once under a lock.

**Transaction tokens travel in headers, never in bodies.** `X-token` is a
patient credential; request bodies end up in access logs, error trackers and
browser devtools far more readily than headers do. The flow functions take it
as an argument, so the routes are the only place this is enforced, which is
why it is written down here.

**An unconfigured deployment answers 503, not 404.** The rest of Kravos Setu
works with no ABDM credentials, so these routes must not break the app when
the credentials are absent -- but a 404 would tell the frontend the feature
does not exist rather than that it is not switched on. `/api/abha/status`
exists so the UI can ask before offering the flow at all.

Nothing here logs a request body. Aadhaar numbers, OTPs and mobile numbers
pass through these handlers, and `abha/transport.py` redacts them at the
outbound edge -- that guarantee is worth nothing if this layer prints them
first.
"""

from __future__ import annotations

import threading
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from abha import faceauth, login as login_flow
from abha.client import AbhaClient
from abha.config import AbhaConfig, AbhaConfigError
from abha.enrollment import (
    enrol_by_aadhaar,
    request_aadhaar_otp,
    request_mobile_otp,
    set_abha_address,
    suggest_abha_addresses,
    verify_mobile_otp,
)
from abha.errors import AbhaApiError, AbhaError, AbhaFlowError, AbhaTransportError
from abha.models import AbhaAccount, AbhaProfile, EnrolmentResult, LoginResult, OtpChallenge
from abha.profile import get_abha_card, get_profile, get_qr_code

router = APIRouter(prefix="/api/abha", tags=["abha"])

_client_lock = threading.Lock()
_client_instance: AbhaClient | None = None


def _client() -> AbhaClient:
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            if _client_instance is None:
                try:
                    _client_instance = AbhaClient(AbhaConfig.from_env())
                except AbhaConfigError as exc:
                    # 503, not 500: the service is fine, this feature is not
                    # switched on. The message names the fix.
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _client_instance


def reset_client() -> None:
    """Drop the cached client. For tests and for credential rotation."""
    global _client_instance
    with _client_lock:
        if _client_instance is not None:
            _client_instance.close()
        _client_instance = None


# --------------------------------------------------------------------------
# request bodies
# --------------------------------------------------------------------------


class AadhaarOtpRequest(BaseModel):
    aadhaar: str = Field(min_length=12, max_length=14)


class AadhaarEnrolRequest(BaseModel):
    txn_id: str = Field(alias="txnId")
    otp: str
    mobile: str | None = None


class MobileOtpRequest(BaseModel):
    txn_id: str = Field(alias="txnId")
    mobile: str


class MobileVerifyRequest(BaseModel):
    txn_id: str = Field(alias="txnId")
    otp: str


class AbhaAddressRequest(BaseModel):
    txn_id: str = Field(alias="txnId")
    abha_address: str = Field(alias="abhaAddress")
    preferred: bool = True


class FaceStartRequest(BaseModel):
    aadhaar: str = Field(min_length=12, max_length=14)
    for_login: bool = Field(default=False, alias="forLogin")


class FaceCompleteRequest(BaseModel):
    txn_id: str = Field(alias="txnId")
    mobile: str | None = None


class LoginOtpRequest(BaseModel):
    identifier: str
    method: login_flow.LoginMethod = login_flow.LoginMethod.AADHAAR


class LoginVerifyRequest(BaseModel):
    txn_id: str = Field(alias="txnId")
    otp: str
    method: login_flow.LoginMethod = login_flow.LoginMethod.AADHAAR


class AccountSelectRequest(BaseModel):
    txn_id: str = Field(alias="txnId")
    index: str


# --------------------------------------------------------------------------
# error boundary
# --------------------------------------------------------------------------


def _handle(call: "Any") -> Any:
    """Run one flow call, translating ABDM failures into HTTP.

    The status code mapping is the point. A flow error is the *caller's*
    sequencing mistake (422), an API error is ABDM's refusal passed through
    with its own code intact (so a UI can branch on "OTP expired" versus
    "already enrolled"), and a transport error is an upstream outage (502) --
    three situations a single 500 would flatten into one unactionable
    response.
    """
    try:
        return call()
    except AbhaFlowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AbhaApiError as exc:
        # 4xx is passed through; 5xx from ABDM becomes 502, because it is not
        # this service that failed.
        status = exc.status_code if 400 <= exc.status_code < 500 else 502
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code, "message": exc.message, "source": "abdm"},
        ) from exc
    except AbhaTransportError as exc:
        raise HTTPException(status_code=502, detail=f"ABDM unreachable: {exc}") from exc
    except AbhaError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _require_x_token(token: str | None) -> str:
    if not token:
        raise HTTPException(
            status_code=401,
            detail="X-token header required -- obtain it from enrolment or login",
        )
    return token


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


@router.get("/status")
def abha_status() -> dict[str, Any]:
    """Whether ABHA flows are available, without attempting a call.

    Deliberately does not touch ABDM: the frontend asks this on load to
    decide whether to render the identity flow at all, and that decision
    should not depend on the sandbox being up.
    """
    configured = AbhaConfig.is_configured()
    environment = None
    if configured:
        try:
            environment = AbhaConfig.from_env().environment.value
        except AbhaConfigError:
            configured = False
    return {"configured": configured, "environment": environment}


# --------------------------------------------------------------------------
# enrolment
# --------------------------------------------------------------------------


@router.post("/enrol/aadhaar/otp")
def api_enrol_aadhaar_otp(request: AadhaarOtpRequest) -> OtpChallenge:
    return _handle(lambda: request_aadhaar_otp(_client(), request.aadhaar))


@router.post("/enrol/aadhaar/verify")
def api_enrol_aadhaar_verify(request: AadhaarEnrolRequest) -> EnrolmentResult:
    return _handle(
        lambda: enrol_by_aadhaar(
            _client(),
            txn_id=request.txn_id,
            otp=request.otp,
            mobile=request.mobile,
        )
    )


@router.post("/enrol/mobile/otp")
def api_enrol_mobile_otp(
    request: MobileOtpRequest,
    x_token: Annotated[str | None, Header(alias="X-token")] = None,
) -> OtpChallenge:
    token = _require_x_token(x_token)
    return _handle(
        lambda: request_mobile_otp(
            _client(), txn_id=request.txn_id, mobile=request.mobile, token=token
        )
    )


@router.post("/enrol/mobile/verify")
def api_enrol_mobile_verify(
    request: MobileVerifyRequest,
    x_token: Annotated[str | None, Header(alias="X-token")] = None,
) -> dict[str, Any]:
    token = _require_x_token(x_token)
    return _handle(
        lambda: verify_mobile_otp(
            _client(), txn_id=request.txn_id, otp=request.otp, token=token
        )
    )


@router.get("/enrol/address/suggestions")
def api_address_suggestions(
    txn_id: Annotated[str, Query(alias="txnId")],
) -> list[str]:
    return _handle(lambda: suggest_abha_addresses(_client(), txn_id=txn_id))


@router.post("/enrol/address")
def api_set_address(request: AbhaAddressRequest) -> dict[str, Any]:
    return _handle(
        lambda: set_abha_address(
            _client(),
            txn_id=request.txn_id,
            abha_address=request.abha_address,
            preferred=request.preferred,
        )
    )


# --------------------------------------------------------------------------
# face auth
# --------------------------------------------------------------------------


@router.post("/face/start")
def api_face_start(request: FaceStartRequest) -> Any:
    return _handle(
        lambda: faceauth.start_face_auth(
            _client(), aadhaar=request.aadhaar, for_login=request.for_login
        )
    )


@router.get("/face/status")
def api_face_status(txn_id: Annotated[str, Query(alias="txnId")]) -> dict[str, str]:
    """One poll, not a blocking wait.

    `faceauth.await_capture` blocks until the patient acts, which is right
    for a script and wrong for an HTTP handler -- it would hold a worker for
    up to ten minutes per patient. The browser polls this instead.
    """
    status = _handle(lambda: faceauth.poll_once(_client(), txn_id=txn_id))
    return {"status": status.value, "terminal": str(status.is_terminal).lower()}


@router.post("/face/complete")
def api_face_complete(request: FaceCompleteRequest) -> EnrolmentResult:
    return _handle(
        lambda: faceauth.complete_face_enrolment(
            _client(), txn_id=request.txn_id, mobile=request.mobile
        )
    )


# --------------------------------------------------------------------------
# login and search
# --------------------------------------------------------------------------


@router.post("/login/otp")
def api_login_otp(request: LoginOtpRequest) -> OtpChallenge:
    return _handle(
        lambda: login_flow.request_login_otp(
            _client(), identifier=request.identifier, method=request.method
        )
    )


@router.post("/login/verify")
def api_login_verify(request: LoginVerifyRequest) -> LoginResult:
    return _handle(
        lambda: login_flow.verify_login_otp(
            _client(), txn_id=request.txn_id, otp=request.otp, method=request.method
        )
    )


@router.post("/login/select")
def api_login_select(
    request: AccountSelectRequest,
    t_token: Annotated[str | None, Header(alias="T-token")] = None,
) -> LoginResult:
    if not t_token:
        raise HTTPException(
            status_code=401,
            detail="T-token header required -- it is returned by /login/verify "
            "when one credential maps to several ABHA accounts",
        )
    return _handle(
        lambda: login_flow.select_account(
            _client(), txn_id=request.txn_id, account=request.index, t_token=t_token
        )
    )


@router.get("/search")
def api_search(
    abha_address: Annotated[str, Query(alias="abhaAddress")],
) -> list[AbhaAccount]:
    return _handle(lambda: login_flow.find_abha(_client(), abha_address=abha_address))


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------


@router.get("/profile")
def api_profile(
    x_token: Annotated[str | None, Header(alias="X-token")] = None,
    via_abha_address: Annotated[bool, Query(alias="viaAbhaAddress")] = False,
) -> AbhaProfile:
    token = _require_x_token(x_token)
    return _handle(
        lambda: get_profile(_client(), token=token, via_abha_address=via_abha_address)
    )


@router.get("/qr")
def api_qr(
    x_token: Annotated[str | None, Header(alias="X-token")] = None,
) -> dict[str, Any]:
    token = _require_x_token(x_token)
    return _handle(lambda: get_qr_code(_client(), token=token))


@router.get("/card")
def api_card(
    x_token: Annotated[str | None, Header(alias="X-token")] = None,
    via_abha_address: Annotated[bool, Query(alias="viaAbhaAddress")] = False,
) -> Any:
    from fastapi.responses import Response

    token = _require_x_token(x_token)
    png = _handle(
        lambda: get_abha_card(_client(), token=token, via_abha_address=via_abha_address)
    )
    return Response(content=png, media_type="image/png")
