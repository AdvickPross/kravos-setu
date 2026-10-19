"""Read routes for the M2 testing console (frontend ``/m2``).

M2 is mostly server-to-server, so a tester can't see what happened unless
something shows them. NHA's catalogue makes the same point ("the integrator
needs the exchange on screen, separately from the patient"). Everything here
reads state the M2 flows already record. The only exception is the bridge
URL update, an explicit action the tester takes.

Nothing returned here carries a secret. Link tokens are reported by expiry
only, OTPs never leave the server, and ABHA numbers and mobiles are masked.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from abha.config import AbhaConfig, AbhaConfigError
from abha.errors import AbhaApiError, AbhaTransportError
from backend.abdm_store import (
    get_patient_mobile,
    get_sms_notification,
    link_token_expiry,
    list_care_contexts,
    list_consents,
    list_data_transfers,
    list_transactions,
    patient_allergies,
)
from backend.abha_link import default_store
from backend.data_layer import connection, get_patient
from backend.m2_fhir import op_consultation_bundle
from backend.m2_hip_linking import abdm_gender, abdm_name
from backend.m2_service import care_contexts_for

console_router = APIRouter(prefix="/api/m2", tags=["ABDM M2 console"])

_EPOCH = datetime(1900, 1, 1, tzinfo=timezone.utc)
_FAR = datetime(2200, 1, 1, tzinfo=timezone.utc)
# When a care context has rows in several states, the most settled one wins.
_STATUS_RANK = {"LINKED": 3, "LINK_REQUESTED": 2, "LINK_FAILED": 1}


def _mask(value: str | None, keep: int = 4) -> str | None:
    if not value:
        return None
    return f"{'•' * max(0, len(value) - keep)}{value[-keep:]}"


def _config() -> AbhaConfig | None:
    if not AbhaConfig.is_configured():
        return None
    try:
        return AbhaConfig.from_env()
    except AbhaConfigError:
        return None


def _m2_client():
    from backend.m2_routes import _client

    return _client()


@console_router.get("/status")
def m2_status() -> dict[str, Any]:
    """What is configured, without calling ABDM."""
    config = _config()
    return {
        "abdmConfigured": config is not None,
        "environment": config.environment.value if config else None,
        "hipId": config.hip_id if config else "",
        "hipConfigured": bool(config and config.hip_id),
        "hipName": config.hip_name if config else None,
        "verifyGatewayTokens": config.verify_gateway_tokens if config else True,
        "otpDelivery": os.getenv("ABDM_OTP_DELIVERY", "log").strip().lower() or "log",
    }


@console_router.get("/bridge")
def m2_bridge() -> dict[str, Any]:
    """Our bridge as ABDM sees it: callback URL and linked services. Live call."""
    try:
        return _m2_client().bridge_services()
    except AbhaApiError as exc:
        raise HTTPException(status_code=502, detail={"code": exc.code, "message": exc.message}) from exc
    except AbhaTransportError as exc:
        raise HTTPException(status_code=502, detail="ABDM gateway could not be reached") from exc


class BridgeUrl(BaseModel):
    url: str = Field(..., min_length=8)


@console_router.patch("/bridge/url")
def m2_update_bridge_url(payload: BridgeUrl) -> dict[str, Any]:
    """Point ABDM's callbacks at this server (sandbox guide 3.2.4). Base URL only:
    ABDM appends /api/v3/... itself."""
    url = payload.url.strip().rstrip("/")
    if not url.startswith("https://"):
        raise HTTPException(status_code=422, detail="The bridge URL must be https")
    if "/api/" in url:
        raise HTTPException(status_code=422, detail="Give the base URL only; ABDM appends /api/v3/...")
    try:
        _m2_client().update_bridge_url(url)
    except AbhaApiError as exc:
        raise HTTPException(status_code=502, detail={"code": exc.code, "message": exc.message}) from exc
    except AbhaTransportError as exc:
        raise HTTPException(status_code=502, detail="ABDM gateway could not be reached") from exc
    return {"status": "UPDATED", "url": url}


@console_router.get("/patients/{patient_id}/state")
def m2_patient_state(patient_id: str) -> dict[str, Any]:
    """Everything M2 knows about one patient, for the console."""
    try:
        patient = get_patient(patient_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown patient_id: {patient_id}") from None

    config = _config()
    hip_id = config.hip_id if config else ""
    link = default_store().abha_for(patient_id)

    statuses: dict[str, dict[str, Any]] = {}
    for row in list_care_contexts(patient_id=patient_id):
        if hip_id and row.get("hip_id") not in (hip_id, None):
            continue
        ref = row["reference_number"]
        rank = _STATUS_RANK.get(row["status"], 0)
        if ref not in statuses or rank > _STATUS_RANK.get(statuses[ref]["status"], 0):
            statuses[ref] = row

    contexts = []
    for context in care_contexts_for(patient_id):
        row = statuses.get(context["referenceNumber"])
        contexts.append(
            {
                **context,
                "status": row["status"] if row else "UNLINKED",
                "linkedAt": row.get("linked_at") if row else None,
            }
        )

    expiry = (
        link_token_expiry(abha_address=link.abha_address, hip_id=hip_id)
        if link and link.abha_address and hip_id
        else None
    )
    sms = get_sms_notification(patient_id)
    return {
        "patientId": patient_id,
        "name": patient.name,
        "gender": patient.gender,
        "birthDate": patient.birth_date,
        # Exactly what generate-token will send (HIP_INTI_LINK_502). It has to
        # match the ABHA's own demographics, or ABDM refuses the token.
        "tokenDemographics": {
            "name": abdm_name(patient.name),
            "gender": abdm_gender(patient.gender),
            "yearOfBirth": patient.birth_date.year if patient.birth_date else None,
        },
        "abha": (
            {
                "abhaNumber": _mask(link.abha_number),
                "abhaAddress": link.abha_address,
                "taggedAt": link.linked_at,
            }
            if link
            else None
        ),
        "mobile": _mask(get_patient_mobile(patient_id)),
        "linkTokenExpiresAt": expiry,
        "careContexts": contexts,
        "sms": (
            {
                "status": sms["status"],
                "notifiedAt": sms["notified_at"],
                "recordsNotified": len(sms["notified"] or []),
            }
            if sms
            else None
        ),
    }


@console_router.get("/patients/{patient_id}/care-contexts/{reference}/fhir")
def m2_fhir_preview(patient_id: str, reference: str) -> dict[str, Any]:
    """The FHIR bundle this care context would be shared as (Health_RECORD_CREATION_101).

    A preview over the whole record, with no consent date clipping and no
    encryption. A real transfer narrows it to what the consent allows.
    """
    try:
        patient = get_patient(patient_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown patient_id: {patient_id}") from None
    with connection().cursor() as cur:
        cur.execute(
            """
            SELECT fact_id, category, code_system, code, display, value, unit, date, status, text
            FROM facts WHERE patient_id = %s AND encounter_id = %s
            ORDER BY date, fact_id
            """,
            (patient_id, reference),
        )
        columns = [d.name for d in cur.description]
        facts = [dict(zip(columns, row)) for row in cur.fetchall()]
    if not facts:
        raise HTTPException(status_code=404, detail="No records for this care context")

    config = _config()
    return op_consultation_bundle(
        patient={
            "patient_id": patient_id,
            "name": patient.name,
            "gender": patient.gender,
            "birth_date": patient.birth_date,
        },
        encounter_id=reference,
        facts=facts,
        allergies=patient_allergies(patient_id=patient_id, date_from=_EPOCH, date_to=_FAR),
        hip_id=(config.hip_id if config and config.hip_id else "HIP-ID-NOT-SET"),
        hip_name=(config.hip_name if config else "Kravos Setu"),
    )


@console_router.get("/transactions")
def m2_transactions(patient_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """The M2 activity log: every exchange with ABDM, newest first."""
    return list_transactions(patient_id=patient_id or None, limit=max(1, min(limit, 200)))


@console_router.get("/consents")
def m2_consents() -> list[dict[str, Any]]:
    return list_consents()


@console_router.get("/transfers")
def m2_transfers(limit: int = 50) -> list[dict[str, Any]]:
    return list_data_transfers(limit=max(1, min(limit, 200)))
