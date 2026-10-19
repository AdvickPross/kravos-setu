"""Consent and data transfer: the HIP's side of sharing records (NHA
HIP_INIT_SHARE_CARECONTEXT, and the consent grant/revoke/expire cases).

**Consent.** ABDM notifies ``/api/v3/consent/request/hip/notify`` when a
patient grants, revokes, denies or lets expire a consent covering our
records. A granted artefact is stored. Anything else deletes it, which is
what the NHA revoke/expire cases check. Each notification is acknowledged on
``consent/v3/request/hip/on-notify``.

**Transfer.** ABDM posts ``/api/v3/hip/health-information/request`` with a
consent id, a date range, the HIU's ``dataPushUrl`` and its key material.
The HIP must:

1. verify a valid consent covers it (granted, ours, not erased) and refuse
   otherwise, since data must never go out under a revoked or expired consent;
2. acknowledge on ``health-information/hip/on-request``;
3. build each consented care context as a FHIR bundle, restricted to the
   date range the consent *and* the request allow, and only for consented
   HI types;
4. encrypt every bundle for the HIU (``abha.fidelius``) with one entry per
   page and fresh key material per page, and push it to ``dataPushUrl``
   with our gateway token;
5. report the outcome per care context on ``health-information/notify``.

All of this runs as a background task right after the request, well inside
ABDM's two-hour limit.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from abha import fidelius
from abha.m2_client import AbdmM2Client
from abha.transport import base_headers
from backend.abdm_store import (
    care_context_refs,
    delete_consent,
    encounter_facts,
    finish_data_transfer,
    get_consent,
    patient_allergies,
    record_data_transfer,
    save_consent,
    update_transaction,
)
from backend.data_layer import get_patient
from backend.m2_fhir import op_consultation_bundle
from backend.m2_service import CARE_CONTEXT_HI_TYPE, GATEWAY_ERROR_CODE

log = logging.getLogger(__name__)

PUSH_MEDIA_TYPE = "application/fhir+json"
# Our dhPublicKey expiry, as sent to the HIU. It's informational: the key
# is single-use and discarded after the page is encrypted.
KEY_EXPIRY = timedelta(hours=2)


def _utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# -- consent notifications -------------------------------------------------------


def handle_consent_notification(
    client: AbdmM2Client, payload: dict[str, Any], *, gateway_request_id: str
) -> None:
    """Store or delete the artefact, then acknowledge. Never raises."""
    notification = payload.get("notification") or {}
    consent_id = notification.get("consentId") or (notification.get("consentDetail") or {}).get("consentId")
    status = str(notification.get("status") or "").upper()

    try:
        if not consent_id:
            raise ValueError("notification carries no consentId")
        detail = notification.get("consentDetail") or {}
        if status == "GRANTED":
            hip = detail.get("hip") or {}
            if hip.get("id") and hip["id"] != client.hip_id:
                raise ValueError("consent is for another HIP")
            permission = detail.get("permission") or {}
            date_range = permission.get("dateRange") or {}
            save_consent(
                consent_id=consent_id,
                abha_address=(detail.get("patient") or {}).get("id"),
                hip_id=client.hip_id,
                hiu_id=(detail.get("hiu") or {}).get("id"),
                hi_types=list(detail.get("hiTypes") or []),
                care_contexts=list(detail.get("careContexts") or []),
                date_from=_utc(date_range.get("from")),
                date_to=_utc(date_range.get("to")),
                data_erase_at=_utc(permission.get("dataEraseAt")),
                artefact=detail,
            )
            outcome = "CONSENT_STORED"
        else:
            # REVOKED, EXPIRED, DENIED: nothing may be shared under it again.
            delete_consent(consent_id)
            outcome = f"CONSENT_{status or 'UNKNOWN'}_DELETED"
        update_transaction(request_id=gateway_request_id, status=outcome)
    except Exception as exc:  # noqa: BLE001 -- background task
        log.exception("M2 consent notification could not be applied")
        update_transaction(
            request_id=gateway_request_id,
            status="FAILED",
            error_code=type(exc).__name__,
            error_message=str(exc)[:500],
        )
        if not consent_id:
            return

    try:
        client.consent_on_notify(
            {
                "acknowledgement": {"status": "OK", "consentId": consent_id},
                "response": {"requestId": gateway_request_id},
            },
            request_id=str(uuid.uuid4()),
        )
    except Exception:  # noqa: BLE001
        log.exception("M2 consent acknowledgement failed")


# -- health information requests ---------------------------------------------------


class _Refused(Exception):
    """The request can't be served; the message goes to the gateway."""


def _validate(client: AbdmM2Client, request: dict[str, Any]) -> tuple[dict, datetime, datetime, dict]:
    hi_request = request.get("hiRequest") or {}
    consent_id = (hi_request.get("consent") or {}).get("id")
    consent = get_consent(consent_id) if consent_id else None
    if consent is None:
        raise _Refused("No active consent artefact found for this request")
    if consent["hip_id"] != client.hip_id:
        raise _Refused("Consent artefact is not for this HIP")
    if consent["erased"]:
        raise _Refused("Consent artefact has expired")

    requested = hi_request.get("dateRange") or {}
    start = max(filter(None, [_utc(requested.get("from")), consent["date_from"]]))
    end = min(filter(None, [_utc(requested.get("to")), consent["date_to"]]))
    if start > end:
        raise _Refused("Requested date range is outside the consented range")

    key_material = hi_request.get("keyMaterial") or {}
    if str(key_material.get("cryptoAlg", "")).upper() != "ECDH" or str(
        key_material.get("curve", "")
    ).lower() != "curve25519":
        raise _Refused("Only ECDH on Curve25519 is supported")
    try:
        fidelius.decode_public_key((key_material.get("dhPublicKey") or {}).get("keyValue") or "")
        nonce = base64.b64decode(key_material.get("nonce") or "")
    except (fidelius.FideliusError, ValueError) as exc:
        raise _Refused(f"Unusable key material: {exc}") from exc
    if len(nonce) != 32:
        raise _Refused("Key material nonce must be 32 bytes")
    expiry = _utc((key_material.get("dhPublicKey") or {}).get("expiry"))
    if expiry is not None and expiry < _now():
        raise _Refused("Requester's public key has expired")

    push_url = str(hi_request.get("dataPushUrl") or "")
    if not push_url.startswith("https://"):
        raise _Refused("dataPushUrl must be an https URL")
    return consent, start, end, key_material


def _encrypt_page(
    bundle_json: bytes, requester_key: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Encrypt one bundle with fresh key material. Returns (content, our keyMaterial)."""
    requester_public = requester_key["dhPublicKey"]["keyValue"]
    _, requester_x509 = fidelius.decode_public_key(requester_public)
    ours = fidelius.generate_key_material()
    content = fidelius.encrypt(
        bundle_json,
        sender_private_key=ours.private_key,
        sender_nonce=ours.nonce,
        requester_public_key=requester_public,
        requester_nonce=base64.b64decode(requester_key["nonce"]),
    )
    return content, {
        "cryptoAlg": "ECDH",
        "curve": "Curve25519",
        "dhPublicKey": {
            "expiry": _iso_z(_now() + KEY_EXPIRY),
            "parameters": "Curve25519/32byte random key",
            # Answer in the encoding the requester used.
            "keyValue": ours.public_key(x509=requester_x509),
        },
        "nonce": ours.nonce_b64,
    }


def _push(client: AbdmM2Client, url: str, page: dict[str, Any]) -> None:
    abha = client.client
    abha.transport.send_json(
        "POST",
        url,
        headers=base_headers(extra={"Authorization": abha.session.authorization()}),
        json_body=page,
    )


def handle_health_information_request(
    client: AbdmM2Client, request: dict[str, Any], *, gateway_request_id: str
) -> None:
    """Validate, acknowledge, build, encrypt, push, notify. Never raises."""
    transaction_id = str(request.get("transactionId") or "")
    hi_request = request.get("hiRequest") or {}
    consent_id = str((hi_request.get("consent") or {}).get("id") or "")
    push_url = hi_request.get("dataPushUrl")
    record_data_transfer(
        transaction_id=transaction_id,
        consent_id=consent_id,
        request_id=gateway_request_id,
        status="RECEIVED",
        data_push_url=push_url,
    )

    def acknowledge(session_status: str, error: str | None = None) -> None:
        body: dict[str, Any] = {
            "hiRequest": {"transactionId": transaction_id, "sessionStatus": session_status},
            "response": {"requestId": gateway_request_id},
        }
        if error:
            body["error"] = {"code": GATEWAY_ERROR_CODE, "message": error}
        try:
            client.health_information_on_request(body, request_id=str(uuid.uuid4()))
        except Exception:  # noqa: BLE001
            log.exception("M2 on-request acknowledgement failed")

    try:
        consent, start, end, key_material = _validate(client, request)
    except _Refused as exc:
        acknowledge("ERRORED", str(exc))
        finish_data_transfer(transaction_id, status="REFUSED", error_message=str(exc))
        update_transaction(
            request_id=gateway_request_id,
            status="REFUSED",
            error_code="DATA_REQUEST_REFUSED",
            error_message=str(exc)[:500],
        )
        return

    acknowledge("ACKNOWLEDGED")

    statuses: list[dict[str, Any]] = []
    entries: list[tuple[str, bytes]] = []
    hi_type_allowed = CARE_CONTEXT_HI_TYPE in (consent["hi_types"] or [])
    for item in consent["care_contexts"] or []:
        patient_id = item.get("patientReference")
        reference = item.get("careContextReference")
        if not patient_id or not reference:
            continue
        status = {"careContextReference": reference, "hiStatus": "ERRORED"}
        if not hi_type_allowed:
            status["description"] = f"{CARE_CONTEXT_HI_TYPE} is not a consented HI type"
        elif reference not in care_context_refs(
            patient_id=patient_id, hip_id=client.hip_id, statuses=("LINKED",)
        ):
            status["description"] = "Care context is not linked at this HIP"
        else:
            try:
                patient = get_patient(patient_id)
                facts = encounter_facts(
                    patient_id=patient_id, encounter_id=reference, date_from=start, date_to=end
                )
                if not facts:
                    status["description"] = "No records in the consented date range"
                else:
                    bundle = op_consultation_bundle(
                        patient={
                            "patient_id": patient_id,
                            "name": patient.name,
                            "gender": patient.gender,
                            "birth_date": patient.birth_date,
                        },
                        encounter_id=reference,
                        facts=facts,
                        allergies=patient_allergies(
                            patient_id=patient_id, date_from=start, date_to=end
                        ),
                        hip_id=client.hip_id,
                        hip_name=client.client.config.hip_name,
                    )
                    entries.append((reference, json.dumps(bundle, separators=(",", ":")).encode("utf-8")))
                    status = None
            except Exception as exc:  # noqa: BLE001
                log.exception("M2 bundle build failed")
                status["description"] = f"Could not build record: {type(exc).__name__}"
        if status is not None:
            statuses.append(status)

    page_count = len(entries)
    for page_number, (reference, bundle_json) in enumerate(entries, start=1):
        try:
            content, our_key = _encrypt_page(bundle_json, key_material)
            _push(
                client,
                push_url,
                {
                    "pageNumber": page_number,
                    "pageCount": page_count,
                    "transactionId": transaction_id,
                    "entries": [
                        {
                            "content": content,
                            "media": PUSH_MEDIA_TYPE,
                            "checksum": hashlib.md5(bundle_json).hexdigest(),
                            "careContextReference": reference,
                        }
                    ],
                    "keyMaterial": our_key,
                },
            )
            statuses.append({"careContextReference": reference, "hiStatus": "DELIVERED", "description": "Delivered"})
        except Exception as exc:  # noqa: BLE001
            log.exception("M2 data push failed")
            statuses.append(
                {
                    "careContextReference": reference,
                    "hiStatus": "ERRORED",
                    "description": f"Push failed: {type(exc).__name__}",
                }
            )

    delivered = any(s["hiStatus"] == "DELIVERED" for s in statuses)
    session_status = "TRANSFERRED" if delivered else "FAILED"
    try:
        client.health_information_notify(
            {
                "notification": {
                    "consentId": consent["consent_id"],
                    "transactionId": transaction_id,
                    "doneAt": _iso_z(_now()),
                    "notifier": {"type": "HIP", "id": client.hip_id},
                    "statusNotification": {
                        "sessionStatus": session_status,
                        "hipId": client.hip_id,
                        "statusResponses": statuses,
                    },
                }
            },
            request_id=str(uuid.uuid4()),
        )
    except Exception:  # noqa: BLE001
        log.exception("M2 data-flow notify failed")
    finish_data_transfer(transaction_id, status=session_status, statuses=statuses)
    update_transaction(request_id=gateway_request_id, status=session_status)
