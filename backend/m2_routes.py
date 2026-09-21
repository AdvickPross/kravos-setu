from __future__ import annotations
from fastapi import APIRouter, Header, HTTPException, status
from abha.m2_models import LinkResponse, OnDiscoverRequest
from backend.abdm_store import save_care_context, update_transaction


m2_router = APIRouter(
    prefix="/api/v3/hiu/patient/care-context",
    tags=["ABDM M2"],
)



def _require_header(value: str | None, header_name: str) -> str:
    if not value or not value.strip():
        raise HTTPException(
            status_code=400,
            detail=f"Missing {header_name} header",
        )

    return value.strip()


def _require_request_id(request_id: str | None) -> str:
    return _require_header(request_id, "REQUEST-ID")


def _require_cm_id(x_cm_id: str | None) -> str:
    return _require_header(x_cm_id, "X-CM-ID")

def _check_transaction_update(updated_rows: int) -> None:
    """
    Reject callbacks that do not match a stored transaction.
    """
    if updated_rows == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No matching ABDM transaction found",
        )



@m2_router.post("/on-discover", status_code=200)
def handle_care_context_discovery(
    payload: OnDiscoverRequest,
    x_cm_id: str | None = Header(default=None, alias="X-CM-ID"),
    request_id: str | None = Header(default=None, alias="REQUEST-ID"),
):
    _require_cm_id(x_cm_id)
    validated_request_id = _require_request_id(request_id)

    # Verify that the callback belongs to a known transaction first.
    updated_rows = update_transaction(
        request_id=validated_request_id,
        transaction_id=payload.transaction_id,
        status="DISCOVERY_RECEIVED",
    )

    _check_transaction_update(updated_rows)

    # Save care contexts only after the transaction is verified.
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


@m2_router.post("/on-init", status_code=200)
def handle_link_initialization(
    payload: LinkResponse,
    x_cm_id: str | None = Header(default=None, alias="X-CM-ID"),
    request_id: str | None = Header(default=None, alias="REQUEST-ID"),
):
    _require_cm_id(x_cm_id)
    validated_request_id = _require_request_id(request_id)

    if not payload.transaction_id:
        raise HTTPException(
            status_code=400,
            detail="Missing transactionId in link callback",
        )

    if payload.error:
        error_code = str(payload.error.get("code", "UNKNOWN_ERROR"))
        error_message = str(
            payload.error.get("message", "ABDM link operation failed")
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