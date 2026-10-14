from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from abha.errors import AbhaApiError, AbhaTransportError
from abha.m2_client import AbdmM2Client

from abha.m2_models import (
    ConfirmationRequest,
    DiscoveryRequest,
    HipDiscoveryCallbackRequest,
    HmisLmisDiscoveryResponseRequest,
    LinkCareContextRequest,
    LinkInitRequest,
    LinkResponse,
    OnDiscoverRequest,
    TokenRequest,
    TokenResponse,
)
from backend.abdm_store import (
    save_care_context,
    update_transaction,
)
from backend.m2_service import start_m2_transaction


m2_router = APIRouter(
    prefix="/api/v3",
    tags=["ABDM M2"],
)


def _require_header(
    value: str | None,
    header_name: str,
) -> str:
    """
    Validate a required ABDM header.
    """

    if not value or not value.strip():
        raise HTTPException(
            status_code=400,
            detail=f"Missing {header_name} header",
        )

    return value.strip()


def _require_request_id(
    request_id: str | None,
) -> str:
    return _require_header(
        request_id,
        "REQUEST-ID",
    )


def _require_timestamp(
    timestamp: str | None,
) -> str:
    return _require_header(
        timestamp,
        "TIMESTAMP",
    )


def _require_cm_id(
    x_cm_id: str | None,
) -> str:
    return _require_header(
        x_cm_id,
        "X-CM-ID",
    )


def _require_authorization(
    authorization: str | None,
) -> str:
    return _require_header(
        authorization,
        "Authorization",
    )


def _check_transaction_update(
    updated_rows: int,
) -> None:
    """
    Ensure the callback corresponds to a known transaction.
    """

    if updated_rows == 0:
        raise HTTPException(
            status_code=404,
            detail="No matching ABDM transaction found",
        )


def _client() -> AbdmM2Client:
    """
    Create the M2 client using the existing M1 ABDM client.

    M1 authentication/session handling remains centralized in
    backend.abha_routes.
    """

    from backend.abha_routes import _client as get_abha_client

    return AbdmM2Client(get_abha_client())

@m2_router.post(
    "/hiecm/v3/token/generate-token",
    status_code=202,
)
def generate_link_token(
    payload: TokenRequest,
    x_hip_id: str | None = Header(
        default=None,
        alias="X-HIP-ID",
    ),
    x_cm_id: str | None = Header(
        default=None,
        alias="X-CM-ID",
    ),
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    request_id: str | None = Header(
        default=None,
        alias="REQUEST-ID",
    ),
    timestamp: str | None = Header(
        default=None,
        alias="TIMESTAMP",
    ),
):
    """
    Generate an ABDM link token for a patient's ABHA address.

    M2 endpoint:

        POST /api/v3/hiecm/v3/token/generate-token
    """

    validated_request_id = _require_request_id(
        request_id
    )

    validated_timestamp = _require_timestamp(
        timestamp
    )

    validated_hip_id = _require_header(
        x_hip_id,
        "X-HIP-ID",
    )

    validated_cm_id = _require_cm_id(
        x_cm_id
    )

    validated_authorization = _require_authorization(
        authorization
    )

    start_m2_transaction(
        patient_id=None,
        transaction_id=None,
        request_id=validated_request_id,
        operation="LINK_TOKEN_GENERATION",
    )

    body = payload.model_dump(
        by_alias=True,
        exclude_none=True,
    )

    try:
        response = _client().generate_link_token(
            body=body,
            extra_headers={
                "REQUEST-ID": validated_request_id,
                "TIMESTAMP": validated_timestamp,
                "Authorization": validated_authorization,
                "X-HIP-ID": validated_hip_id,
                "X-CM-ID": validated_cm_id,
            },
        )

    except AbhaApiError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code=exc.code,
            error_message=exc.message,
        )

        raise HTTPException(
            status_code=502,
            detail={
                "code": exc.code,
                "message": exc.message,
            },
        ) from exc

    except AbhaTransportError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code="TRANSPORT_ERROR",
            error_message=str(exc),
        )

        raise HTTPException(
            status_code=502,
            detail="ABDM service could not be reached",
        ) from exc

    return response

@m2_router.post(
    "/hip/token/on-generate-token",
    status_code=200,
)
def handle_link_token_callback(
    payload: TokenResponse,
    x_hip_id: str | None = Header(
        default=None,
        alias="X-HIP-ID",
    ),
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    request_id: str | None = Header(
        default=None,
        alias="REQUEST-ID",
    ),
    timestamp: str | None = Header(
        default=None,
        alias="TIMESTAMP",
    ),
):
    """
    Receive the HIE-CM callback containing the generated link token.

    M2 endpoint:

        POST /api/v3/hip/token/on-generate-token
    """

    _require_header(
        x_hip_id,
        "X-HIP-ID",
    )

    _require_authorization(
        authorization,
    )

    validated_request_id = _require_request_id(
        request_id
    )

    _require_timestamp(
        timestamp
    )

    if payload.error:
        error_code = str(
            payload.error.get(
                "code",
                "UNKNOWN_ERROR",
            )
        )

        error_message = str(
            payload.error.get(
                "message",
                "ABDM link token generation failed",
            )
        )

        updated_rows = update_transaction(
            request_id=validated_request_id,
            status="TOKEN_GENERATION_FAILED",
            error_code=error_code,
            error_message=error_message,
        )

        _check_transaction_update(
            updated_rows
        )

        return {
            "status": "ERROR",
            "requestId": validated_request_id,
        }

    if not payload.link_token:
        updated_rows = update_transaction(
            request_id=validated_request_id,
            status="TOKEN_GENERATION_FAILED",
            error_code="INVALID_CALLBACK",
            error_message="Link token missing from successful callback",
        )

        _check_transaction_update(
            updated_rows
        )

        raise HTTPException(
            status_code=400,
            detail="Link token missing from callback",
        )

    updated_rows = update_transaction(
        request_id=validated_request_id,
        status="TOKEN_GENERATED",
    )

    _check_transaction_update(
        updated_rows
    )

    return {
        "status": "SUCCESS",
        "requestId": validated_request_id,
    }

@m2_router.post(
    "/hip/patient/care-context/discover",
    status_code=200,
)
def handle_hip_care_context_discovery(
    payload: HipDiscoveryCallbackRequest,
    request_id: str | None = Header(
        default=None,
        alias="REQUEST-ID",
    ),
    timestamp: str | None = Header(
        default=None,
        alias="TIMESTAMP",
    ),
    x_hip_id: str | None = Header(
        default=None,
        alias="X-HIP-ID",
    ),
):
    """
    Receive HIE-CM's discovery request for a HIP.

    M2 endpoint:
        POST /api/v3/hip/patient/care-context/discover
    """
    validated_request_id = _require_request_id(request_id)
    _require_timestamp(timestamp)
    _require_header(x_hip_id, "X-HIP-ID")

    return {
        "status": "SUCCESS",
        "transactionId": payload.transaction_id,
        "requestId": validated_request_id,
    }

@m2_router.post(
    "/hiu/patient/care-context/on-discover",
    status_code=200,
)
def handle_care_context_discovery(
    payload: OnDiscoverRequest,
    x_cm_id: str | None = Header(default=None, alias="X-CM-ID"),
    request_id: str | None = Header(default=None, alias="REQUEST-ID"),
):
    _require_cm_id(x_cm_id)
    validated_request_id = _require_request_id(request_id)

    updated_rows = update_transaction(
        request_id=validated_request_id,
        transaction_id=payload.transaction_id,
        status="DISCOVERY_RECEIVED",
    )

    _check_transaction_update(updated_rows)

    for care_context in payload.care_contexts:
        save_care_context(
            patient_id=None,
            transaction_id=payload.transaction_id,
            reference_number=care_context.reference_number,
            display=care_context.display,
            hi_type=payload.hi_type,
            status="DISCOVERED",
        )

    return {
        "status": "SUCCESS",
        "transactionId": payload.transaction_id,
    }

@m2_router.post(
    "/hiu/patient/care-context/on-init",
    status_code=200,
)
def handle_link_initialization(
    payload: LinkResponse,
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    request_id: str | None = Header(
        default=None,
        alias="REQUEST-ID",
    ),
    timestamp: str | None = Header(
        default=None,
        alias="TIMESTAMP",
    ),
):
    """
    Receive the HIE-CM response for care-context link initialization.

    M2 endpoint:

        POST /api/v3/hiu/patient/care-context/on-init
    """

    _require_authorization(authorization)
    _require_request_id(request_id)
    _require_timestamp(timestamp)

    validated_request_id = _require_request_id(
        request_id
    )

    if payload.error:
        error_code = str(
            payload.error.get(
                "code",
                "UNKNOWN_ERROR",
            )
        )

        error_message = str(
            payload.error.get(
                "message",
                "ABDM link operation failed",
            )
        )

        updated_rows = update_transaction(
            request_id=validated_request_id,
            transaction_id=payload.transaction_id,
            status="LINK_FAILED",
            error_code=error_code,
            error_message=error_message,
        )

        _check_transaction_update(updated_rows)

        return {
            "status": "ERROR",
            "transactionId": payload.transaction_id,
        }

    updated_rows = update_transaction(
        request_id=validated_request_id,
        transaction_id=payload.transaction_id,
        status="LINK_CALLBACK_RECEIVED",
    )

    _check_transaction_update(updated_rows)

    return {
        "status": "SUCCESS",
        "transactionId": payload.transaction_id,
    }


@m2_router.post(
    "/hiecm/hip/v3/link/carecontext",
    status_code=202,
)
def link_care_context(
    payload: LinkCareContextRequest,
    x_hip_id: str | None = Header(
        default=None,
        alias="X-HIP-ID",
    ),
    x_cm_id: str | None = Header(
        default=None,
        alias="X-CM-ID",
    ),
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    request_id: str | None = Header(
        default=None,
        alias="REQUEST-ID",
    ),
    timestamp: str | None = Header(
        default=None,
        alias="TIMESTAMP",
    ),
):
    """
    Link discovered care contexts with the patient's ABHA address
    using a valid ABDM link token.

    M2 endpoint:

        POST /api/v3/hiecm/hip/v3/link/carecontext
    """

    validated_request_id = _require_request_id(
        request_id
    )

    validated_timestamp = _require_timestamp(
        timestamp
    )

    validated_hip_id = _require_header(
        x_hip_id,
        "X-HIP-ID",
    )

    validated_cm_id = _require_cm_id(
        x_cm_id
    )

    validated_authorization = _require_authorization(
        authorization
    )

    start_m2_transaction(
        patient_id=None,
        transaction_id=None,
        request_id=validated_request_id,
        operation="LINK_CARE_CONTEXT",
    )

    body = payload.model_dump(
        by_alias=True,
        exclude_none=True,
    )

    try:
        response = _client().link_care_context(
            body=body,
            extra_headers={
                "REQUEST-ID": validated_request_id,
                "TIMESTAMP": validated_timestamp,
                "Authorization": validated_authorization,
                "X-HIP-ID": validated_hip_id,
                "X-CM-ID": validated_cm_id,
            },
        )

    except AbhaApiError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code=exc.code,
            error_message=exc.message,
        )

        raise HTTPException(
            status_code=502,
            detail={
                "code": exc.code,
                "message": exc.message,
            },
        ) from exc

    except AbhaTransportError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code="TRANSPORT_ERROR",
            error_message=str(exc),
        )

        raise HTTPException(
            status_code=502,
            detail="ABDM service could not be reached",
        ) from exc

    return response


@m2_router.post(
    "/hiecm/user-initiated-linking/v3/link/care-context/init",
    status_code=202,
)
def initialize_care_context_link(
    payload: LinkInitRequest,
    x_cm_id: str | None = Header(
        default=None,
        alias="X-CM-ID",
    ),
    x_hiu_id: str | None = Header(
        default=None,
        alias="X-HIU-ID",
    ),
    x_auth_token: str | None = Header(
        default=None,
        alias="X-AUTH-TOKEN",
    ),
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    request_id: str | None = Header(
        default=None,
        alias="REQUEST-ID",
    ),
    timestamp: str | None = Header(
        default=None,
        alias="TIMESTAMP",
    ),
):
    """
    Initialize user-initiated linking for discovered care contexts.

    M2 endpoint:

        POST /api/v3/hiecm/user-initiated-linking/v3/link/care-context/init
    """

    validated_request_id = _require_request_id(
        request_id
    )

    validated_timestamp = _require_timestamp(
        timestamp
    )

    validated_cm_id = _require_cm_id(
        x_cm_id
    )

    validated_authorization = _require_authorization(
        authorization
    )

    if not x_hiu_id or not x_hiu_id.strip():
        raise HTTPException(
            status_code=400,
            detail="Missing X-HIU-ID header",
        )

    if not x_auth_token or not x_auth_token.strip():
        raise HTTPException(
            status_code=400,
            detail="Missing X-AUTH-TOKEN header",
        )

    start_m2_transaction(
        patient_id=None,
        transaction_id=payload.transaction_id,
        request_id=validated_request_id,
        operation="LINK_INIT",
    )

    body = payload.model_dump(
        by_alias=True,
        exclude_none=True,
    )

    try:
        response = _client().initialize_care_context_link(
            body=body,
            extra_headers={
                "REQUEST-ID": validated_request_id,
                "TIMESTAMP": validated_timestamp,
                "Authorization": validated_authorization,
                "X-AUTH-TOKEN": x_auth_token.strip(),
                "X-HIU-ID": x_hiu_id.strip(),
                "X-CM-ID": validated_cm_id,
            },
        )

    except AbhaApiError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code=exc.code,
            error_message=exc.message,
        )

        raise HTTPException(
            status_code=502,
            detail={
                "code": exc.code,
                "message": exc.message,
            },
        ) from exc

    except AbhaTransportError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code="TRANSPORT_ERROR",
            error_message=str(exc),
        )

        raise HTTPException(
            status_code=502,
            detail="ABDM service could not be reached",
        ) from exc

    return response

@m2_router.post(
    "/hiecm/user-initiated-linking/v3/link/care-context/on-init",
    status_code=202,
)
def respond_to_care_context_link_init(
    payload: LinkResponse,
    x_cm_id: str | None = Header(default=None, alias="X-CM-ID"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    request_id: str | None = Header(default=None, alias="REQUEST-ID"),
    timestamp: str | None = Header(default=None, alias="TIMESTAMP"),
):
    validated_request_id = _require_request_id(request_id)
    validated_timestamp = _require_timestamp(timestamp)
    validated_cm_id = _require_cm_id(x_cm_id)
    validated_authorization = _require_authorization(authorization)

    if not payload.transaction_id:
        raise HTTPException(
            status_code=400,
            detail="Missing transactionId",
        )

    start_m2_transaction(
        patient_id=None,
        transaction_id=payload.transaction_id,
        request_id=validated_request_id,
        operation="LINK_INIT_RESPONSE",
    )

    body = payload.model_dump(
        by_alias=True,
        exclude_none=True,
    )

    try:
        response = _client().respond_to_care_context_link_init(
            body=body,
            extra_headers={
                "Authorization": validated_authorization,
                "REQUEST-ID": validated_request_id,
                "TIMESTAMP": validated_timestamp,
                "X-CM-ID": validated_cm_id,
            },
        )
    except AbhaApiError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code=exc.code,
            error_message=exc.message,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": exc.code,
                "message": exc.message,
            },
        ) from exc
    except AbhaTransportError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code="TRANSPORT_ERROR",
            error_message=str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail="ABDM service could not be reached",
        ) from exc

    update_transaction(
        request_id=validated_request_id,
        transaction_id=payload.transaction_id,
        status="LINK_INIT_RESPONSE_SENT",
    )

    return response

@m2_router.post(
    "/hip/link/care-context/init",
    status_code=200,
)
def handle_link_care_context_init_callback(
    payload: LinkInitRequest,
    x_hip_id: str | None = Header(default=None, alias="X-HIP-ID"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    request_id: str | None = Header(default=None, alias="REQUEST-ID"),
    timestamp: str | None = Header(default=None, alias="TIMESTAMP"),
):
    validated_request_id = _require_request_id(request_id)
    validated_timestamp = _require_timestamp(timestamp)
    validated_authorization = _require_authorization(authorization)

    if not x_hip_id or not x_hip_id.strip():
        raise HTTPException(status_code=400, detail="Missing X-HIP-ID header")

    start_m2_transaction(
        patient_id=None,
        transaction_id=payload.transaction_id,
        request_id=validated_request_id,
        operation="LINK_INIT_CALLBACK",
    )

    update_transaction(
        request_id=validated_request_id,
        transaction_id=payload.transaction_id,
        status="LINK_INIT_CALLBACK_RECEIVED",
    )

    return {
        "status": "received",
        "transactionId": payload.transaction_id,
        "requestId": validated_request_id,
    }
@m2_router.post(
    "/hiecm/user-initiated-linking/v3/patient/care-context/discover",
    status_code=202,
)
def discover_care_contexts(
    payload: DiscoveryRequest,
    x_cm_id: str | None = Header(
        default=None,
        alias="X-CM-ID",
    ),
    x_hiu_id: str | None = Header(
        default=None,
        alias="X-HIU-ID",
    ),
    x_auth_token: str | None = Header(
        default=None,
        alias="X-AUTH-TOKEN",
    ),
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    request_id: str | None = Header(
        default=None,
        alias="REQUEST-ID",
    ),
):
    """
    Start an ABDM M2 patient care-context discovery request.

    M2 endpoint:

        POST /api/hiecm/user-initiated-linking/v3/patient/care-context/discover
    """

    validated_request_id = _require_request_id(
        request_id
    )

    validated_cm_id = _require_cm_id(
        x_cm_id
    )

    if not x_hiu_id or not x_hiu_id.strip():
        raise HTTPException(
            status_code=400,
            detail="Missing X-HIU-ID header",
        )

    if not x_auth_token or not x_auth_token.strip():
        raise HTTPException(
            status_code=400,
            detail="Missing X-AUTH-TOKEN header",
        )

    if not authorization or not authorization.strip():
        raise HTTPException(
            status_code=400,
            detail="Missing Authorization header",
        )

    start_m2_transaction(
        patient_id=None,
        transaction_id=None,
        request_id=validated_request_id,
        operation="DISCOVERY",
    )

    body = payload.model_dump(
        by_alias=True,
        exclude_none=True,
    )

    try:
        response = _client().discover_care_contexts(
            body=body,
            extra_headers={
                "REQUEST-ID": validated_request_id,
                "X-CM-ID": validated_cm_id,
                "X-HIU-ID": x_hiu_id.strip(),
                "X-AUTH-TOKEN": x_auth_token.strip(),
                "Authorization": authorization.strip(),
            },
        )

    except AbhaApiError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code=exc.code,
            error_message=exc.message,
        )

        raise HTTPException(
            status_code=502,
            detail={
                "code": exc.code,
                "message": exc.message,
            },
        ) from exc

    except AbhaTransportError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code="TRANSPORT_ERROR",
            error_message=str(exc),
        )

        raise HTTPException(
            status_code=502,
            detail="ABDM service could not be reached",
        ) from exc

    return response

@m2_router.post(
    "/hiecm/user-initiated-linking/v3/link/care-context/confirm",
    status_code=202,
)
def confirm_care_context_link(
    payload: ConfirmationRequest,
    x_auth_token: str | None = Header(default=None, alias="X-AUTH-TOKEN"),
    x_hiu_id: str | None = Header(default=None, alias="X-HIU-ID"),
    x_cm_id: str | None = Header(default=None, alias="X-CM-ID"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    request_id: str | None = Header(default=None, alias="REQUEST-ID"),
    timestamp: str | None = Header(default=None, alias="TIMESTAMP"),
):
    validated_request_id = _require_request_id(request_id)
    validated_timestamp = _require_timestamp(timestamp)
    validated_auth_token = _require_header(
        x_auth_token,
        "X-AUTH-TOKEN",
    )
    validated_hiu_id = _require_header(
        x_hiu_id,
        "X-HIU-ID",
    )
    validated_cm_id = _require_cm_id(x_cm_id)
    validated_authorization = _require_authorization(authorization)

    start_m2_transaction(
        patient_id=None,
        transaction_id=None,
        request_id=validated_request_id,
        operation="LINK_CONFIRM",
    )

    body = payload.model_dump(
    by_alias=True,
    exclude_none=True,
)

    try:
        response = _client().confirm_care_context_link(
            body=body,
            extra_headers={
                "Authorization": validated_authorization,
                "REQUEST-ID": validated_request_id,
                "TIMESTAMP": validated_timestamp,
                "X-AUTH-TOKEN": validated_auth_token,
                "X-HIU-ID": validated_hiu_id,
                "X-CM-ID": validated_cm_id,
            },
        )
    except AbhaApiError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code=exc.code,
            error_message=exc.message,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": exc.code,
                "message": exc.message,
            },
        ) from exc
    except AbhaTransportError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code="TRANSPORT_ERROR",
            error_message=str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail="ABDM service could not be reached",
        ) from exc

    update_transaction(
        request_id=validated_request_id,
        status="LINK_CONFIRMED",
    )

    return response

@m2_router.post(
    "/hiecm/user-initiated-linking/v3/patient/care-context/on-discover",
    status_code=202,
)
def respond_to_care_context_discovery(
    payload: HmisLmisDiscoveryResponseRequest,
    x_cm_id: str | None = Header(default=None, alias="X-CM-ID"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    request_id: str | None = Header(default=None, alias="REQUEST-ID"),
    timestamp: str | None = Header(default=None, alias="TIMESTAMP"),
):
    validated_request_id = _require_request_id(request_id)
    validated_timestamp = _require_timestamp(timestamp)
    validated_cm_id = _require_cm_id(x_cm_id)
    validated_authorization = _require_authorization(authorization)

    start_m2_transaction(
        patient_id=None,
        transaction_id=payload.transaction_id,
        request_id=validated_request_id,
        operation="DISCOVERY_RESPONSE",
    )

    body = payload.model_dump(
        by_alias=True,
        exclude_none=True,
    )

    try:
        response = _client().respond_to_care_context_discovery(
            body=body,
            extra_headers={
                "Authorization": validated_authorization,
                "REQUEST-ID": validated_request_id,
                "TIMESTAMP": validated_timestamp,
                "X-CM-ID": validated_cm_id,
            },
        )
    except AbhaApiError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code=exc.code,
            error_message=exc.message,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": exc.code,
                "message": exc.message,
            },
        ) from exc
    except AbhaTransportError as exc:
        update_transaction(
            request_id=validated_request_id,
            status="FAILED",
            error_code="TRANSPORT_ERROR",
            error_message=str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail="ABDM service could not be reached",
        ) from exc

    update_transaction(
        request_id=validated_request_id,
        transaction_id=payload.transaction_id,
        status="DISCOVERY_RESPONSE_SENT",
    )

    return response
