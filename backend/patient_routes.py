"""Creating a patient record by hand.

The corpus arrives from Synthea and everything else in this system reads it.
This is the one place that writes a patient, and it exists for two situations
that are really the same one: a demo with no ABHA to sign in as, and a real
desk with a walk-in whose history is a paper folder.

**Manually created patients are marked.** `source_file` is set to `manual`,
which is the same column the loader fills with a Synthea bundle name, so the
distinction survives into every read without a schema change. It matters
because the two are not equally trustworthy: a Synthea patient has a
generated history behind it, and one created here has whatever a human typed
plus whatever OCR read off a scan. `list_patients` sorts them first for the
practical reason that someone who just created a patient is about to look for
them in a list of 952.

**Ids are generated here, never accepted from the client.** A caller-supplied
id is how you overwrite someone else's record by guessing a UUID, and there
is no reason a client needs to choose one.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.models import PatientSummary

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patients", tags=["patients"])

MANUAL_SOURCE = "manual"

# Loose on purpose: names in India carry spaces, dots, hyphens and
# apostrophes, and a validator that rejects them is a validator that rejects
# people. This only excludes control characters and the angle brackets that
# would make a name awkward to render.
_NAME = re.compile(r"^[^\x00-\x1f<>]{2,120}$")


class NewPatient(BaseModel):
    name: str
    birth_date: date | None = Field(default=None, alias="birthDate")
    gender: str | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        value = value.strip()
        if not _NAME.match(value):
            raise ValueError("name must be 2-120 characters and not contain <, > or control characters")
        return value

    @field_validator("birth_date")
    @classmethod
    def _not_in_the_future(cls, value: date | None) -> date | None:
        # A future date of birth silently breaks every age calculation and
        # recency window downstream, and reads as a typo nobody notices.
        if value and value > date.today():
            raise ValueError("birth_date cannot be in the future")
        return value

    @field_validator("gender")
    @classmethod
    def _known_gender(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        # The corpus uses these; `other` and `unknown` are FHIR's own values
        # and refusing them would force a wrong answer.
        if value not in {"male", "female", "other", "unknown"}:
            raise ValueError("gender must be male, female, other or unknown")
        return value


@router.post("", status_code=201)
def api_create_patient(request: NewPatient) -> PatientSummary:
    """Create an empty patient record, ready for records to be attached."""
    from backend.data_layer import connection

    patient_id = str(uuid.uuid4())
    # NOT `with connection() as conn:`. `connection()` hands back the shared,
    # lazily-reconnected instance every read in this app uses, and psycopg's
    # context manager *closes* the connection on exit -- which would take the
    # whole API down with it on the first patient created. It is autocommit,
    # so there is nothing to commit either.
    try:
        with connection().cursor() as cur:
            cur.execute(
                """
                INSERT INTO patients
                    (patient_id, name, birth_date, gender, deceased,
                     n_facts, source_file)
                VALUES (%s, %s, %s, %s, NULL, 0, %s)
                """,
                (
                    patient_id,
                    request.name,
                    request.birth_date,
                    request.gender,
                    MANUAL_SOURCE,
                ),
            )
    except Exception as exc:  # noqa: BLE001 - never escape as a bare 500
        log.exception("could not create patient")
        raise HTTPException(
            status_code=503,
            detail=f"could not write to the corpus: {exc}. Is Postgres up? "
            f"`docker compose up -d postgres`",
        ) from exc

    log.info("created manual patient %s", patient_id)
    # Built by hand rather than re-read, so `source_file` has to be set here
    # too -- a response claiming `null` would make a manual patient look like
    # a corpus one to every client that reads it back.
    return PatientSummary.model_validate(
        {
            "patient_id": patient_id,
            "name": request.name,
            "birth_date": request.birth_date,
            "gender": request.gender,
            "deceased": None,
            "_n_facts": 0,
            "_source_file": MANUAL_SOURCE,
        }
    )
