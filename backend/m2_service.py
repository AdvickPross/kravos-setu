from __future__ import annotations
from backend.abdm_store import create_transaction


def start_m2_transaction(
    *,
    patient_id: str | None,
    transaction_id: str | None,
    request_id: str,
    operation: str,
) -> int:
    """
    Create a local record for an ABDM M2 operation.

    The transaction must be stored before the corresponding
    ABDM callback is expected.
    """
    return create_transaction(
        patient_id=patient_id,
        transaction_id=transaction_id,
        request_id=request_id,
        operation=operation,
        status="INITIATED",
    )