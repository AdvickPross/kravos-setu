"""HIP-initiated linking (NHA HIP_INTI_LINK_501-506), care-context update
notify, and the deep-link SMS (HIP_INIT_NOTIFY_HIECM).

HIP-initiated linking needs no click from the patient or the desk:

1. A patient tagged to an ABHA address (M1 link store) who has care contexts
   not yet linked needs a **link token**. We send their demographics to
   ``generate-token``, and ABDM checks them against the ABHA (HIP_INTI_LINK_503).
2. The token arrives on ``on-generate-token`` and is stored for 6 months.
   That callback re-runs step 3 for the patient straight away.
3. ``link/carecontext`` sends the unlinked care contexts with the token in
   ``X-LINK-TOKEN``. Rows are marked ``LINK_REQUESTED`` **before** the call,
   so a fast ``on_carecontext`` callback always finds them.
4. ``on_carecontext`` settles them as ``LINKED`` or ``LINK_FAILED``. A failed
   row is retried by the next batch run.

``run_linking_batch`` walks every tagged patient. It is "the request for
linking pushed from the backend using the batch process" that
HIP_INTI_LINK_505 asks for, and is run by ``scripts/m2_link_batch.py`` or
``POST /api/m2/link-batch``.

The deep-link SMS is for the opposite case. A patient has a mobile on file
but no ABHA address, so nothing can be linked. ``sms/notify2`` asks ABDM to
text them a link to a PHR app, from which they discover the records
themselves (user-initiated linking).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from abha.m2_client import AbdmM2Client
from backend.abdm_store import (
    care_context_refs,
    get_link_token,
    get_sms_notification,
    mark_care_contexts_requested,
    patients_with_mobiles,
    save_sms_notification,
    set_sms_notification_status,
    settle_care_context_request,
    token_request_in_flight,
    update_transaction,
)
from backend.abha_link import default_store
from backend.data_layer import get_patient
from backend.m2_service import _patient_groups, care_contexts_for, start_m2_transaction

log = logging.getLogger(__name__)

# ABDM publishes no cap on care contexts per link call. Bounded calls keep
# one bad record from failing a whole history (the largest patient has 394).
CARE_CONTEXTS_PER_LINK_CALL = 50
# ABDM remembers a token request, including a refused one, and answers an
# identical retry with ABDM-1092 "duplicate" for at least ~70s. Waiting
# longer than that before asking again keeps the first refusal, which is the
# one that names the cause.
TOKEN_RETRY_AFTER_SECONDS = 600

_GENDERS = {"male": "M", "m": "M", "female": "F", "f": "F", "other": "O", "o": "O"}


def abdm_name(name: str | None) -> str:
    """The name as the generate-token schema accepts it.

    Letters, spaces and ``',.-`` only. Synthea appends digits to every name
    part ("Jane752 Doe514"), and the schema rejects those outright.
    """
    cleaned = re.sub(r"[^A-Za-z',.\- ]", "", re.sub(r"\d", "", name or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def abdm_gender(gender: str | None) -> str:
    return _GENDERS.get((gender or "").strip().lower(), "U")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _fail_transaction(request_id: str, exc: Exception) -> None:
    update_transaction(
        request_id=request_id,
        status="FAILED",
        error_code=getattr(exc, "code", None) or type(exc).__name__,
        error_message=(getattr(exc, "message", None) or str(exc))[:500],
    )


# -- HIP-initiated linking ------------------------------------------------------


def _request_link_token(client: AbdmM2Client, patient_id: str, link) -> str:
    try:
        patient = get_patient(patient_id)
    except KeyError:
        return "UNKNOWN_PATIENT"
    if patient.birth_date is None or not abdm_name(patient.name):
        return "INCOMPLETE_DEMOGRAPHICS"

    body: dict[str, Any] = {
        "abhaAddress": link.abha_address,
        "name": abdm_name(patient.name),
        "gender": abdm_gender(patient.gender),
        "yearOfBirth": patient.birth_date.year,
    }
    if link.abha_number:
        body["abhaNumber"] = int(link.abha_number)

    request_id = str(uuid.uuid4())
    start_m2_transaction(
        patient_id=patient_id,
        transaction_id=None,
        request_id=request_id,
        operation="LINK_TOKEN_GENERATION",
    )
    try:
        client.generate_link_token(body, request_id=request_id)
    except Exception as exc:  # noqa: BLE001 -- recorded; the batch carries on
        _fail_transaction(request_id, exc)
        return "TOKEN_REQUEST_FAILED"
    return "TOKEN_REQUESTED"


def hip_link_patient(client: AbdmM2Client, patient_id: str) -> str:
    """Link every unlinked care context of one patient. Returns the outcome.

    Safe to call repeatedly: linked and in-flight care contexts are skipped,
    and a token request is not repeated while one is pending.
    """
    link = default_store().abha_for(patient_id)
    if link is None or not link.abha_address:
        return "NO_ABHA_ADDRESS"

    hip_id = client.hip_id
    settled = care_context_refs(
        patient_id=patient_id, hip_id=hip_id, statuses=("LINKED", "LINK_REQUESTED")
    )
    pending = [c for c in care_contexts_for(patient_id) if c["referenceNumber"] not in settled]
    if not pending:
        return "NOTHING_TO_LINK"

    token = get_link_token(abha_address=link.abha_address, hip_id=hip_id)
    if token is None:
        if token_request_in_flight(patient_id, within_seconds=TOKEN_RETRY_AFTER_SECONDS):
            return "TOKEN_PENDING"
        return _request_link_token(client, patient_id, link)

    try:
        display = abdm_name(get_patient(patient_id).name) or patient_id
    except KeyError:
        display = patient_id

    for start in range(0, len(pending), CARE_CONTEXTS_PER_LINK_CALL):
        chunk = pending[start : start + CARE_CONTEXTS_PER_LINK_CALL]
        request_id = str(uuid.uuid4())
        start_m2_transaction(
            patient_id=patient_id,
            transaction_id=None,
            request_id=request_id,
            operation="LINK_CARE_CONTEXT",
        )
        # Before the call: a fast on_carecontext must find these rows.
        mark_care_contexts_requested(
            patient_id=patient_id, hip_id=hip_id, request_id=request_id, contexts=chunk
        )
        body: dict[str, Any] = {
            "abhaAddress": link.abha_address,
            "patient": _patient_groups(patient_id, display, chunk),
        }
        # The token was requested with abhaNumber when we hold one, and the
        # spec says to send both again on the link call.
        if link.abha_number:
            body["abhaNumber"] = int(link.abha_number)
        try:
            client.link_care_context(body, link_token=token, request_id=request_id)
        except Exception as exc:  # noqa: BLE001
            settle_care_context_request(request_id, linked=False)
            _fail_transaction(request_id, exc)
            return "LINK_REQUEST_FAILED"
    return "LINK_REQUESTED"


def settle_link_callback(payload: dict[str, Any]) -> tuple[str, int]:
    """Apply an ``on_carecontext`` callback. Returns (request id, rows settled)."""
    request_id = (payload.get("response") or {}).get("requestId")
    if not request_id:
        raise ValueError("response.requestId is required")
    error = payload.get("error")
    status_text = str(payload.get("status") or "")
    linked = not error and "success" in status_text.lower()
    rows = settle_care_context_request(request_id, linked=linked)
    update_transaction(
        request_id=request_id,
        status="LINKED" if linked else "LINK_FAILED",
        error_code=None if linked else str((error or {}).get("code") or "LINK_FAILED"),
        error_message=None if linked else str((error or {}).get("message") or status_text)[:500],
    )
    return request_id, rows


# -- care-context update notify ------------------------------------------------------


def notify_care_context_update(
    client: AbdmM2Client, patient_id: str, care_context_reference: str, hi_types: list[str]
) -> str:
    """Tell ABDM that a linked care context gained new records. Returns the
    request id, which ``links/context/on-notify`` echoes."""
    link = default_store().abha_for(patient_id)
    if link is None or not link.abha_address:
        raise LookupError("patient has no ABHA address")
    if care_context_reference not in care_context_refs(
        patient_id=patient_id, hip_id=client.hip_id, statuses=("LINKED",)
    ):
        raise LookupError("care context is not linked")

    request_id = str(uuid.uuid4())
    start_m2_transaction(
        patient_id=patient_id,
        transaction_id=None,
        request_id=request_id,
        operation="CONTEXT_NOTIFY",
    )
    body = {
        "notification": {
            "patient": {"id": link.abha_address},
            "careContext": {
                "patientReference": patient_id,
                "careContextReference": care_context_reference,
            },
            "hiTypes": hi_types,
            "date": _now_iso(),
            "hip": {"id": client.hip_id},
        }
    }
    try:
        client.context_notify(body, request_id=request_id)
    except Exception as exc:
        _fail_transaction(request_id, exc)
        raise
    return request_id


def settle_acknowledgement(payload: dict[str, Any], *, ok_status: str) -> str:
    """Apply an ``acknowledgement`` callback (context on-notify, SMS on-notify)."""
    request_id = (payload.get("response") or {}).get("requestId")
    if not request_id:
        raise ValueError("response.requestId is required")
    ack = str((payload.get("acknowledgement") or {}).get("status") or "")
    error = payload.get("error") or {}
    success = ack.upper() == "SUCCESS" and not error
    update_transaction(
        request_id=request_id,
        status=ok_status if success else "ERRORED",
        error_code=None if success else str(error.get("code") or ack or "ERRORED"),
        error_message=None if success else str(error.get("message") or "")[:500] or None,
    )
    return "ACCEPTED" if success else "ERRORED"


# -- deep-link SMS (HIP_INIT_NOTIFY_HIECM) ---------------------------------------------


def sms_notify_patient(client: AbdmM2Client, patient_id: str, mobile: str) -> str:
    """Text a patient without an ABHA address about records they can link.

    Only when they hold records they haven't been told about yet, so
    repeated batch runs don't text them again for the same records.
    """
    if default_store().abha_for(patient_id) is not None:
        return "HAS_ABHA"
    current = [c["referenceNumber"] for c in care_contexts_for(patient_id)]
    if not current:
        return "NO_RECORDS"
    previous = get_sms_notification(patient_id)
    if previous and previous["status"] != "ERRORED" and not set(current) - set(previous["notified"]):
        return "ALREADY_NOTIFIED"

    request_id = str(uuid.uuid4())
    start_m2_transaction(
        patient_id=patient_id, transaction_id=None, request_id=request_id, operation="SMS_NOTIFY"
    )
    save_sms_notification(
        patient_id=patient_id, mobile=mobile, notified=current, request_id=request_id
    )
    body = {
        "notification": {
            "phoneNo": mobile,
            "hip": {"id": client.hip_id, "name": client.client.config.hip_name},
        }
    }
    try:
        client.sms_notify(body, request_id=request_id)
    except Exception as exc:  # noqa: BLE001
        _fail_transaction(request_id, exc)
        set_sms_notification_status(request_id, "ERRORED")
        return "SMS_FAILED"
    return "SMS_REQUESTED"


# -- the batch --------------------------------------------------------------------


def run_linking_batch(client: AbdmM2Client) -> dict[str, int]:
    """Link every tagged patient's records and text untagged patients with a
    mobile on file. Returns a count per outcome. One patient's failure never
    stops the run."""
    outcomes: dict[str, int] = {}

    def count(outcome: str) -> None:
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    for link in default_store().all_links():
        try:
            count(hip_link_patient(client, link.patient_id))
        except Exception:  # noqa: BLE001
            log.exception("M2 batch: linking failed for one patient")
            count("ERROR")

    for row in patients_with_mobiles():
        try:
            count(sms_notify_patient(client, row["patient_id"], row["mobile"]))
        except Exception:  # noqa: BLE001
            log.exception("M2 batch: SMS notify failed for one patient")
            count("ERROR")

    log.info("M2 linking batch: %s", outcomes)
    return outcomes
