"""ABDM M2 HIP logic: transactions, care contexts, patient matching and
user-initiated linking (NHA USER_INIT_LINK_603-605).

User-initiated linking, from the HIP's side:

1. **discover**: the gateway sends the patient's ABHA address and verified
   identifiers. We find the patient and answer ``on-discover`` with the care
   contexts not yet linked.
2. **link/init**: the patient picks care contexts in their PHR app. We
   check they are ones we offered, send an OTP to the patient's mobile and
   answer ``on-init`` with a link reference.
3. **link/confirm**: the patient types the OTP into the PHR app. We check
   it and answer ``on-confirm`` with the now-linked care contexts.

Every reply goes back asynchronously, as a new call to the gateway that
carries ``response.requestId`` (the REQUEST-ID of the gateway's call to us).
The route returns 202 first, and these functions run as background tasks.
Background tasks never raise: a failure is logged and recorded against the
transaction, because there is no caller left to report it to.
"""

from __future__ import annotations

import difflib
import hashlib
import hmac
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from abha.m2_client import AbdmM2Client
from abha.m2_models import HipDiscoveryCallbackRequest, LinkConfirmRequest, LinkInitRequest
from backend.abdm_store import (
    claim_link_attempt,
    create_link_request,
    create_transaction,
    get_discovery,
    get_patient_mobile,
    linked_care_context_refs,
    patients_with_mobile,
    save_discovery,
    save_linked_care_context,
    set_link_request_status,
    update_transaction,
)
from backend.abha_link import AbhaLinkError, default_store
from backend.data_layer import connection, get_patient

log = logging.getLogger(__name__)

# Every encounter is offered as an OPConsultation: the corpus has no
# document type per encounter, and an OP consultation bundle can carry the
# conditions, medications, observations and procedures an encounter holds.
CARE_CONTEXT_HI_TYPE = "OPConsultation"

OTP_TTL_SECONDS = 600
OTP_MAX_ATTEMPTS = 3
# Name similarity needed for a mobile-number match, after digits and case
# are stripped. A mobile is shared within families, so a mobile hit alone is
# never enough: gender and year of birth must match exactly as well.
NAME_MATCH_THRESHOLD = 0.8

# Error codes sent back to the gateway. ABDM's v3 catalogue isn't published
# alongside the API spec, so these are descriptive rather than numeric. If
# the sandbox rejects them, this is the one place to change.
ERR_PATIENT_NOT_FOUND = ("PATIENT_NOT_FOUND", "No patient matched the details provided")
ERR_MULTIPLE_PATIENTS = ("MULTIPLE_PATIENTS_FOUND", "More than one patient matched; visit the facility")
ERR_NO_CARE_CONTEXTS = ("NO_CARE_CONTEXTS", "No unlinked records available at this facility")
ERR_UNKNOWN_TRANSACTION = ("UNKNOWN_TRANSACTION", "No discovery found for this transaction")
ERR_CARE_CONTEXT_NOT_OFFERED = ("CARE_CONTEXT_NOT_OFFERED", "Requested records were not offered for linking")
ERR_NO_MOBILE = ("NO_MOBILE", "No mobile number on record to send the OTP to")
ERR_OTP_INVALID = ("OTP_INVALID", "The OTP is incorrect")
ERR_OTP_EXPIRED = ("OTP_EXPIRED", "The OTP has expired")
ERR_OTP_ATTEMPTS = ("OTP_ATTEMPTS_EXCEEDED", "Too many incorrect OTP attempts")
ERR_LINK_REF_UNKNOWN = ("LINK_REFERENCE_UNKNOWN", "Unknown link reference")


def start_m2_transaction(
    *,
    patient_id: str | None,
    transaction_id: str | None,
    request_id: str,
    operation: str,
) -> int:
    """Create an internal transaction record for an M2 operation, status INITIATED."""
    return create_transaction(
        patient_id=patient_id,
        transaction_id=transaction_id,
        request_id=request_id,
        operation=operation,
        status="INITIATED",
    )


def _error(pair: tuple[str, str]) -> dict[str, str]:
    return {"code": pair[0], "message": pair[1]}


# -- care contexts -------------------------------------------------------------

_CATEGORY_LABELS = {
    "CONDITION": "Diagnoses",
    "MEDICATION": "Medications",
    "LAB": "Lab results",
    "REPORT": "Reports",
    "VITAL": "Vitals",
    "PROCEDURE": "Procedures",
    "IMMUNIZATION": "Immunizations",
}


def care_contexts_for(patient_id: str) -> list[dict[str, Any]]:
    """One care context per encounter, newest first.

    ``referenceNumber`` is the encounter id. It is stable across reloads and
    is what the data-transfer stage will resolve back to facts.
    """
    with connection().cursor() as cur:
        cur.execute(
            """
            SELECT encounter_id, min(date) AS started, array_agg(DISTINCT category)
            FROM facts
            WHERE patient_id = %s AND encounter_id IS NOT NULL
            GROUP BY encounter_id
            ORDER BY started DESC NULLS LAST
            """,
            (patient_id,),
        )
        rows = cur.fetchall()

    contexts = []
    for encounter_id, started, categories in rows:
        labels = [
            _CATEGORY_LABELS[c] for c in _CATEGORY_LABELS if c in (categories or [])
        ]
        when = started.strftime("%d %b %Y") if started else "undated"
        display = f"Visit on {when}" + (f": {', '.join(labels)}" if labels else "")
        contexts.append(
            {
                "referenceNumber": encounter_id,
                "display": display,
                "hiType": CARE_CONTEXT_HI_TYPE,
            }
        )
    return contexts


def _patient_groups(
    patient_id: str, display: str, contexts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Shape care contexts as ABDM v3 wants them: one entry per hiType."""
    by_type: dict[str, list[dict[str, Any]]] = {}
    for context in contexts:
        by_type.setdefault(context["hiType"], []).append(
            {"referenceNumber": context["referenceNumber"], "display": context["display"]}
        )
    return [
        {
            "referenceNumber": patient_id,
            "display": display,
            "careContexts": items,
            "hiType": hi_type,
            "count": len(items),
        }
        for hi_type, items in by_type.items()
    ]


# -- patient matching (USER_INIT_LINK_603) -------------------------------------


def normalize_mobile(value: str | None) -> str | None:
    """Last 10 digits: ``+91 98765-43210`` and ``9876543210`` are one number."""
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else None


def _name_tokens(name: str | None) -> str:
    # Synthea suffixes names with digits ("Abby752 Braun514"). Token order
    # varies between systems, so tokens are sorted before comparing.
    tokens = re.sub(r"[^a-z\s]", " ", re.sub(r"\d", "", (name or "").lower())).split()
    return " ".join(sorted(tokens))


def name_similarity(a: str | None, b: str | None) -> float:
    return difflib.SequenceMatcher(None, _name_tokens(a), _name_tokens(b)).ratio()


_GENDER_CODES = {"male": "M", "m": "M", "female": "F", "f": "F", "other": "O", "o": "O"}


def _gender_code(value: str | None) -> str | None:
    return _GENDER_CODES.get((value or "").strip().lower())


def _verified(request: HipDiscoveryCallbackRequest, kind: str) -> str | None:
    for identifier in request.patient.verified_identifiers:
        if identifier.type.upper() == kind:
            return identifier.value
    return None


def match_patient(
    request: HipDiscoveryCallbackRequest,
) -> tuple[str | None, list[str], tuple[str, str] | None]:
    """Return ``(patient_id, matched_by, error)``.

    The order follows the NHA test case: ABHA address first, then ABHA
    number. Both are verified identities, so they're trusted without a
    demographic check. After that comes the verified mobile, with a fuzzy
    demographic match that has to single out exactly one patient.
    """
    store = default_store()

    patient_id = store.patient_for_address(request.patient.id)
    if patient_id:
        return patient_id, ["ABHA_ADDRESS"], None

    abha_number = _verified(request, "ABHA_NUMBER")
    if abha_number:
        try:
            patient_id = store.patient_for(abha_number)
        except AbhaLinkError:
            patient_id = None
        if patient_id:
            return patient_id, ["ABHA_NUMBER"], None

    mobile = normalize_mobile(_verified(request, "MOBILE"))
    if not mobile:
        return None, [], ERR_PATIENT_NOT_FOUND

    wanted_gender = _gender_code(request.patient.gender)
    candidates = [
        row
        for row in patients_with_mobile(mobile)
        if _gender_code(row["gender"]) == wanted_gender
        and row["birth_date"] is not None
        and row["birth_date"].year == request.patient.year_of_birth
        and name_similarity(row["name"], request.patient.name) >= NAME_MATCH_THRESHOLD
    ]
    if len(candidates) > 1:
        return None, [], ERR_MULTIPLE_PATIENTS
    if not candidates:
        return None, [], ERR_PATIENT_NOT_FOUND
    return candidates[0]["patient_id"], ["MOBILE"], None


# -- background replies ------------------------------------------------------------


def _reply(
    send, body: dict[str, Any], *, request_id: str, transaction_request_id: str, ok_status: str
) -> None:
    """Send one reply to the gateway, recording the outcome.

    An error reply keeps its code on the transaction, so the log shows *why*
    the patient was refused and not only that a reply went out.
    """
    error = body.get("error") or {}
    try:
        send(body, request_id=request_id)
    except Exception as exc:  # noqa: BLE001 -- background task: log, never raise
        log.exception("M2 reply failed")
        update_transaction(
            request_id=transaction_request_id,
            status="REPLY_FAILED",
            error_code=type(exc).__name__,
            error_message=str(exc)[:500],
        )
        return
    update_transaction(
        request_id=transaction_request_id,
        status=ok_status,
        error_code=error.get("code"),
        error_message=error.get("message"),
    )


def handle_discovery(
    client: AbdmM2Client,
    request: HipDiscoveryCallbackRequest,
    *,
    gateway_request_id: str,
) -> None:
    """Match the patient and answer on-discover."""
    reply_id = str(uuid.uuid4())
    base = {"transactionId": request.transaction_id, "response": {"requestId": gateway_request_id}}

    try:
        patient_id, matched_by, error = match_patient(request)
        offered: list[dict[str, Any]] = []
        if patient_id:
            linked = linked_care_context_refs(patient_id=patient_id, hip_id=client.hip_id)
            offered = [c for c in care_contexts_for(patient_id) if c["referenceNumber"] not in linked]
            if not offered:
                error = ERR_NO_CARE_CONTEXTS
    except Exception as exc:  # noqa: BLE001
        log.exception("M2 discovery failed")
        update_transaction(
            request_id=gateway_request_id,
            status="FAILED",
            error_code=type(exc).__name__,
            error_message=str(exc)[:500],
        )
        return

    if error:
        _reply(
            client.on_discover,
            {**base, "error": _error(error)},
            request_id=reply_id,
            transaction_request_id=gateway_request_id,
            ok_status="DISCOVERY_ERROR_SENT",
        )
        return

    save_discovery(
        transaction_id=request.transaction_id,
        patient_id=patient_id,
        abha_address=request.patient.id,
        mobile=normalize_mobile(_verified(request, "MOBILE")),
        offered=[c["referenceNumber"] for c in offered],
    )
    body = {
        **base,
        "patient": _patient_groups(patient_id, request.patient.name, offered),
        "matchedBy": matched_by,
    }
    _reply(
        client.on_discover,
        body,
        request_id=reply_id,
        transaction_request_id=gateway_request_id,
        ok_status="DISCOVERY_RESPONSE_SENT",
    )


def _otp_hash(link_ref_number: str, otp: str) -> str:
    return hashlib.sha256(f"{link_ref_number}:{otp}".encode()).hexdigest()


def deliver_otp(*, mobile: str, otp: str, link_ref_number: str) -> None:
    """Send the linking OTP to the patient.

    ``ABDM_OTP_DELIVERY=log`` (the only mode for now) writes it to the server
    log. That's fine for sandbox demos, where the tester reads it from the
    console. It is **not** acceptable in production, where a real SMS
    provider has to be plugged in here.
    """
    mode = os.getenv("ABDM_OTP_DELIVERY", "log").strip().lower()
    if mode != "log":
        raise RuntimeError(f"ABDM_OTP_DELIVERY={mode!r} is not supported; only 'log'")
    log.warning(
        "M2 link OTP for ******%s (linkRef %s): %s", mobile[-4:], link_ref_number, otp
    )


def handle_link_init(
    client: AbdmM2Client,
    request: LinkInitRequest,
    *,
    gateway_request_id: str,
) -> None:
    """Check the requested care contexts, send an OTP and answer on-init."""
    reply_id = str(uuid.uuid4())
    base = {"transactionId": request.transaction_id, "response": {"requestId": gateway_request_id}}

    def fail(error: tuple[str, str]) -> None:
        _reply(
            client.on_init,
            {**base, "error": _error(error)},
            request_id=reply_id,
            transaction_request_id=gateway_request_id,
            ok_status="LINK_INIT_ERROR_SENT",
        )

    discovery = get_discovery(request.transaction_id)
    if discovery is None:
        fail(ERR_UNKNOWN_TRANSACTION)
        return

    offered = set(discovery["offered"])
    requested = [
        {"hiType": group.hi_type or CARE_CONTEXT_HI_TYPE, "referenceNumber": cc.reference_number}
        for group in request.patient
        for cc in group.care_contexts
    ]
    if any(group.reference_number != discovery["patient_id"] for group in request.patient) or any(
        cc["referenceNumber"] not in offered for cc in requested
    ):
        fail(ERR_CARE_CONTEXT_NOT_OFFERED)
        return

    mobile = get_patient_mobile(discovery["patient_id"]) or discovery["mobile"]
    if not mobile:
        fail(ERR_NO_MOBILE)
        return

    link_ref_number = str(uuid.uuid4())
    otp = f"{secrets.randbelow(10**6):06d}"
    create_link_request(
        link_ref_number=link_ref_number,
        transaction_id=request.transaction_id,
        patient_id=discovery["patient_id"],
        abha_address=request.abha_address or discovery["abha_address"],
        care_contexts=requested,
        otp_hash=_otp_hash(link_ref_number, otp),
        ttl_seconds=OTP_TTL_SECONDS,
    )
    try:
        deliver_otp(mobile=mobile, otp=otp, link_ref_number=link_ref_number)
    except Exception as exc:  # noqa: BLE001
        log.exception("M2 OTP delivery failed")
        set_link_request_status(link_ref_number, "OTP_DELIVERY_FAILED")
        fail(("OTP_DELIVERY_FAILED", str(exc)[:200]))
        return

    expiry = datetime.now(timezone.utc) + timedelta(seconds=OTP_TTL_SECONDS)
    body = {
        **base,
        "link": {
            "referenceNumber": link_ref_number,
            "authenticationType": "DIRECT",
            "meta": {
                "communicationMedium": "MOBILE",
                "communicationHint": "OTP",
                "communicationExpiry": expiry.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            },
        },
    }
    _reply(
        client.on_init,
        body,
        request_id=reply_id,
        transaction_request_id=gateway_request_id,
        ok_status="LINK_INIT_RESPONSE_SENT",
    )


def handle_link_confirm(
    client: AbdmM2Client,
    request: LinkConfirmRequest,
    *,
    gateway_request_id: str,
) -> None:
    """Verify the OTP, record the links and answer on-confirm."""
    reply_id = str(uuid.uuid4())
    link_ref_number = request.confirmation.link_ref_number
    base = {"response": {"requestId": gateway_request_id}}

    def fail(error: tuple[str, str]) -> None:
        _reply(
            client.on_confirm,
            {**base, "error": _error(error)},
            request_id=reply_id,
            transaction_request_id=gateway_request_id,
            ok_status="LINK_CONFIRM_ERROR_SENT",
        )

    link = claim_link_attempt(link_ref_number, max_attempts=OTP_MAX_ATTEMPTS)
    if link is None:
        fail(ERR_LINK_REF_UNKNOWN)
        return
    if link.get("exhausted"):
        fail(ERR_OTP_ATTEMPTS if link["status"] == "PENDING" else ERR_LINK_REF_UNKNOWN)
        return
    if link["expired"]:
        set_link_request_status(link_ref_number, "EXPIRED")
        fail(ERR_OTP_EXPIRED)
        return
    if not hmac.compare_digest(
        link["otp_hash"], _otp_hash(link_ref_number, request.confirmation.token.strip())
    ):
        if link["attempts"] >= OTP_MAX_ATTEMPTS:
            set_link_request_status(link_ref_number, "FAILED")
            fail(ERR_OTP_ATTEMPTS)
        else:
            fail(ERR_OTP_INVALID)
        return

    set_link_request_status(link_ref_number, "CONFIRMED")
    displays = {c["referenceNumber"]: c for c in care_contexts_for(link["patient_id"])}
    linked = []
    for item in link["care_contexts"]:
        context = displays.get(item["referenceNumber"], {})
        save_linked_care_context(
            patient_id=link["patient_id"],
            hip_id=client.hip_id,
            transaction_id=link["transaction_id"],
            reference_number=item["referenceNumber"],
            display=context.get("display"),
            hi_type=item["hiType"],
        )
        linked.append(
            {
                "referenceNumber": item["referenceNumber"],
                "display": context.get("display") or item["referenceNumber"],
                "hiType": item["hiType"],
            }
        )

    try:
        patient_name = get_patient(link["patient_id"]).name or link["patient_id"]
    except KeyError:
        patient_name = link["patient_id"]
    body = {**base, "patient": _patient_groups(link["patient_id"], patient_name, linked)}
    _reply(
        client.on_confirm,
        body,
        request_id=reply_id,
        transaction_request_id=gateway_request_id,
        ok_status="LINK_CONFIRMED",
    )
