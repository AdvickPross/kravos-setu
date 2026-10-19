"""FHIR R4 document bundles for ABDM data transfer (NHA Health_RECORD_CREATION_101).

One care context is one encounter, and ABDM shares it as one
**OPConsultRecord** document bundle, structured after the NRCeS ABDM FHIR
implementation guide (https://nrces.in/ndhm/fhir/r4/). That means a
``document`` Bundle whose first entry is a Composition, the Composition's
sections referencing the clinical resources, and every resource carried in
the bundle under a ``urn:uuid`` full URL.

Each corpus fact becomes the resource it came from:

| category | resource | Composition section (SNOMED) |
|---|---|---|
| CONDITION | Condition | Medical history (371529009) |
| MEDICATION | MedicationRequest | Medications (721912009) |
| PROCEDURE | Procedure | Procedure (371525003) |
| LAB, VITAL | Observation | Other observations (404684003) |
| IMMUNIZATION | Immunization | Immunization record (41000179103) |
| REPORT | DocumentReference (note text) | Document reference (371530004) |
| ALLERGY | AllergyIntolerance (patient-level) | Allergies (722446000) |

The bundles haven't been run through the NRCeS validator. The structure
follows the profiles, but a validator pass is still to do before
certification. The immunization section has no slot in OPConsultRecord, so
a strict validator will flag it. Every coding keeps the corpus' own
system/code pair (SNOMED, LOINC, RxNorm, CVX), so no meaning is invented in
translation.
"""

from __future__ import annotations

import base64
import uuid
from datetime import date, datetime, timezone
from typing import Any

SNOMED = "http://snomed.info/sct"
_PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/"

_SECTIONS = {
    "CONDITION": ("Medical History", "371529009", "History and physical report"),
    "MEDICATION": ("Medications", "721912009", "Medication summary document"),
    "PROCEDURE": ("Procedure", "371525003", "Clinical procedure report"),
    "LAB": ("Other Observations", "404684003", "Clinical finding"),
    "VITAL": ("Other Observations", "404684003", "Clinical finding"),
    "IMMUNIZATION": ("Immunization", "41000179103", "Immunization record"),
    "REPORT": ("Document Reference", "371530004", "Clinical consultation report"),
    "ALLERGY": ("Allergies", "722446000", "Allergy record"),
}
_SECTION_ORDER = ["ALLERGY", "CONDITION", "MEDICATION", "PROCEDURE", "LAB", "IMMUNIZATION", "REPORT"]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _ref(full_url: str) -> dict[str, str]:
    return {"reference": full_url}


def _concept(fact: dict[str, Any]) -> dict[str, Any]:
    coding = {"system": fact.get("code_system"), "code": fact.get("code"), "display": fact.get("display")}
    concept: dict[str, Any] = {"text": fact.get("display") or fact.get("code") or "Unknown"}
    if coding["system"] and coding["code"]:
        concept["coding"] = [{k: v for k, v in coding.items() if v}]
    return concept


def _fhir_gender(value: str | None) -> str:
    return {"male": "male", "m": "male", "female": "female", "f": "female", "other": "other"}.get(
        (value or "").strip().lower(), "unknown"
    )


def _numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resource_for(fact: dict[str, Any], patient_ref: dict, encounter_ref: dict | None) -> dict[str, Any] | None:
    category = fact.get("category")
    when = _iso(fact.get("date"))
    status = (fact.get("status") or "").lower()

    if category == "CONDITION":
        clinical = "resolved" if status == "resolved" else "active"
        resource = {
            "resourceType": "Condition",
            "meta": {"profile": [_PROFILE + "Condition"]},
            "clinicalStatus": {
                "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": clinical}]
            },
            "code": _concept(fact),
            "subject": patient_ref,
            "recordedDate": when,
        }
    elif category == "MEDICATION":
        resource = {
            "resourceType": "MedicationRequest",
            "meta": {"profile": [_PROFILE + "MedicationRequest"]},
            "status": "completed" if status == "completed" else "active",
            "intent": "order",
            "medicationCodeableConcept": _concept(fact),
            "subject": patient_ref,
            "authoredOn": when,
        }
    elif category == "PROCEDURE":
        resource = {
            "resourceType": "Procedure",
            "meta": {"profile": [_PROFILE + "Procedure"]},
            "status": "completed",
            "code": _concept(fact),
            "subject": patient_ref,
            "performedDateTime": when,
        }
    elif category in ("LAB", "VITAL"):
        resource = {
            "resourceType": "Observation",
            "meta": {"profile": [_PROFILE + ("ObservationVitalSigns" if category == "VITAL" else "Observation")]},
            "status": "final",
            "category": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                            "code": "vital-signs" if category == "VITAL" else "laboratory",
                        }
                    ]
                }
            ],
            "code": _concept(fact),
            "subject": patient_ref,
            "effectiveDateTime": when,
        }
        number = _numeric(fact.get("value"))
        if number is not None:
            quantity: dict[str, Any] = {"value": number}
            if fact.get("unit"):
                quantity.update(
                    {"unit": fact["unit"], "system": "http://unitsofmeasure.org", "code": fact["unit"]}
                )
            resource["valueQuantity"] = quantity
        elif fact.get("value"):
            resource["valueString"] = str(fact["value"])
    elif category == "IMMUNIZATION":
        resource = {
            "resourceType": "Immunization",
            "meta": {"profile": [_PROFILE + "Immunization"]},
            "status": "completed",
            "vaccineCode": _concept(fact),
            "patient": patient_ref,
            "occurrenceDateTime": when,
        }
    elif category == "REPORT":
        resource = {
            "resourceType": "DocumentReference",
            "meta": {"profile": [_PROFILE + "DocumentReference"]},
            "status": "current",
            "type": _concept(fact),
            "subject": patient_ref,
            "date": when,
            "content": [
                {
                    "attachment": {
                        "contentType": "text/plain",
                        "language": "en-IN",
                        "title": fact.get("display") or "Clinical note",
                        "data": base64.b64encode((fact.get("text") or "").encode("utf-8")).decode("ascii"),
                    }
                }
            ],
        }
    elif category == "ALLERGY":
        resource = {
            "resourceType": "AllergyIntolerance",
            "meta": {"profile": [_PROFILE + "AllergyIntolerance"]},
            "clinicalStatus": {
                "coding": [
                    {"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}
                ]
            },
            "verificationStatus": {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                        "code": "confirmed",
                    }
                ]
            },
            "code": _concept(fact),
            "patient": patient_ref,
            "recordedDate": when,
        }
        if fact.get("value") in ("low", "high", "unable-to-assess"):
            resource["criticality"] = fact["value"]
        return {k: v for k, v in resource.items() if v is not None}
    else:
        return None

    if encounter_ref is not None and resource["resourceType"] in (
        "Condition",
        "MedicationRequest",
        "Procedure",
        "Observation",
        "Immunization",
    ):
        resource["encounter"] = encounter_ref
    return {k: v for k, v in resource.items() if v is not None}


def op_consultation_bundle(
    *,
    patient: dict[str, Any],
    encounter_id: str,
    facts: list[dict[str, Any]],
    allergies: list[dict[str, Any]],
    hip_id: str,
    hip_name: str,
) -> dict[str, Any]:
    """One encounter as an ABDM OPConsultRecord document Bundle.

    ``patient`` needs ``patient_id``, ``name``, ``gender`` and ``birth_date``.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    urls = {kind: f"urn:uuid:{uuid.uuid4()}" for kind in ("composition", "patient", "organization", "encounter")}
    patient_ref, org_ref, enc_ref = _ref(urls["patient"]), _ref(urls["organization"]), _ref(urls["encounter"])

    dates = [f["date"] for f in facts if f.get("date") is not None]
    started = _iso(min(dates)) if dates else None

    patient_resource = {
        "resourceType": "Patient",
        "meta": {"profile": [_PROFILE + "Patient"]},
        "identifier": [
            {
                "type": {
                    "coding": [
                        {"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MR", "display": "Medical record number"}
                    ]
                },
                "system": f"https://kravos-setu/{hip_id}/patients",
                "value": patient["patient_id"],
            }
        ],
        "name": [{"text": patient.get("name") or patient["patient_id"]}],
        "gender": _fhir_gender(patient.get("gender")),
        "birthDate": _iso(patient.get("birth_date")),
    }
    organization = {
        "resourceType": "Organization",
        "meta": {"profile": [_PROFILE + "Organization"]},
        "identifier": [
            {
                "type": {
                    "coding": [
                        {"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "PRN", "display": "Provider number"}
                    ]
                },
                "system": "https://facility.abdm.gov.in",
                "value": hip_id,
            }
        ],
        "name": hip_name,
    }
    encounter = {
        "resourceType": "Encounter",
        "meta": {"profile": [_PROFILE + "Encounter"]},
        "identifier": [{"system": f"https://kravos-setu/{hip_id}/encounters", "value": encounter_id}],
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB", "display": "ambulatory"},
        "subject": patient_ref,
    }
    if started:
        encounter["period"] = {"start": started}

    clinical: list[tuple[str, dict[str, Any]]] = []
    for fact in [*allergies, *facts]:
        resource = _resource_for(fact, patient_ref, None if fact.get("category") == "ALLERGY" else enc_ref)
        if resource is not None:
            clinical.append((fact["category"], resource))

    entries_by_section: dict[str, list[str]] = {}
    clinical_entries = []
    for category, resource in clinical:
        full_url = f"urn:uuid:{uuid.uuid4()}"
        title = _SECTIONS[category][0]
        entries_by_section.setdefault(title, []).append(full_url)
        clinical_entries.append({"fullUrl": full_url, "resource": resource})

    sections = []
    seen = set()
    for category in _SECTION_ORDER:
        title, code, display = _SECTIONS[category]
        if title in seen or title not in entries_by_section:
            continue
        seen.add(title)
        sections.append(
            {
                "title": title,
                "code": {"coding": [{"system": SNOMED, "code": code, "display": display}]},
                "entry": [_ref(u) for u in entries_by_section[title]],
            }
        )

    composition = {
        "resourceType": "Composition",
        "meta": {"profile": [_PROFILE + "OPConsultRecord"]},
        "identifier": {"system": f"https://kravos-setu/{hip_id}/documents", "value": str(uuid.uuid4())},
        "status": "final",
        "type": {
            "coding": [{"system": SNOMED, "code": "371530004", "display": "Clinical consultation report"}],
            "text": "Clinical consultation report",
        },
        "subject": patient_ref,
        "encounter": enc_ref,
        "date": started or now,
        "author": [org_ref],
        "title": "Consultation Report",
        "custodian": org_ref,
        "section": sections,
    }

    return {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "meta": {
            "versionId": "1",
            "lastUpdated": now,
            "profile": [_PROFILE + "DocumentBundle"],
            "security": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
                    "code": "V",
                    "display": "very restricted",
                }
            ],
        },
        "identifier": {"system": f"https://kravos-setu/{hip_id}/bundles", "value": str(uuid.uuid4())},
        "type": "document",
        "timestamp": now,
        "entry": [
            {"fullUrl": urls["composition"], "resource": composition},
            {"fullUrl": urls["patient"], "resource": patient_resource},
            {"fullUrl": urls["organization"], "resource": organization},
            {"fullUrl": urls["encounter"], "resource": encounter},
            *clinical_entries,
        ],
    }
