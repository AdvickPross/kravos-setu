"""Upload a scanned record, read it, review it, keep it.

Two endpoints and a deliberate gap between them. `/extract` recognises and
proposes; `/commit` writes. Nothing reaches the corpus without a second,
explicit call carrying the facts a human actually approved.

That split is the whole design. OCR output is a recognition model's guess
about a clinical document, and the failure mode is not a crash -- it is a
plausible wrong drug name entering a patient's record and being trusted
forever after by every query that follows. A single "upload and ingest"
endpoint makes that one click. Two endpoints make it a decision.

**No copy of the upload outlives the request.** RapidOCR and PyMuPDF both
want a path, so it is spooled into a throwaway directory that an `ExitStack`
removes on the way out, however the request ends. A scanned discharge summary
is a patient's health record; leaving copies in an upload directory is how a
demo becomes a breach.
"""

from __future__ import annotations

import logging
import tempfile
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.data_layer import get_patient

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

# A scanned page is ~0.5-2 MB; a long PDF a few tens. Past this something is
# wrong with the request, and OCR of it would tie up a worker for minutes.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".pdf"}


class OcrLineOut(BaseModel):
    text: str
    confidence: float
    page: int
    line_no: int
    reliable: bool


class OcrFactOut(BaseModel):
    """A proposed fact. Carries its own evidence so the UI can show why."""

    fact_id: str
    category: str
    display: str | None
    code: str | None
    code_system: str | None
    value: str | None
    unit: str | None
    confidence: float
    page: int
    line_no: int
    line_text: str
    # Codes the same phrase also reached. Shown so a reviewer can see the
    # extractor made a choice rather than finding one obvious answer.
    also_in: list[str] = Field(default_factory=list)


class ExtractResponse(BaseModel):
    source_file: str
    engine: str
    patient_id: str
    mean_confidence: float
    sections_seen: list[str]
    lines: list[OcrLineOut]
    facts: list[OcrFactOut]
    unmatched: list[str]
    ambiguous: list[str]
    low_confidence: list[str]


class CommitResponse(BaseModel):
    inserted: int
    requested: int
    already_present: int


def _document_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).astimezone()
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"documentDate must be ISO-8601, got {raw!r}"
        ) from None


def _require_patient(patient_id: str) -> None:
    try:
        get_patient(patient_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"unknown patient_id: {patient_id}"
        ) from None


async def _to_temp_file(upload: UploadFile, stack: "ExitStack") -> Path:
    """Spool the upload to a temp file that keeps its original basename.

    The name matters more than it looks. Fact ids are derived from (file,
    page, line, code), so a temp name like `tmp8f3a2c.png` makes them differ
    between the /extract call a reviewer approved and the /commit call meant
    to honour that approval -- every commit then failed with "none of the
    approved facts were found". It also put `tmp8f3a2c.png#p1L15` into
    `source_resource_id`, which is provenance pointing at a file that no
    longer exists.

    So the file goes into a throwaway *directory* under its real basename.
    `Path(...).name` strips any directory component the client sent, which is
    the traversal guard: an upload called `../../etc/passwd` becomes
    `passwd`.
    """
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported file type {suffix or '(none)'}; expected one of "
            f"{', '.join(sorted(ALLOWED_SUFFIXES))}",
        )
    payload = await upload.read()
    if not payload:
        raise HTTPException(status_code=422, detail="the uploaded file is empty")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file is {len(payload) / 1e6:.1f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB",
        )
    safe_name = Path(upload.filename or f"upload{suffix}").name or f"upload{suffix}"
    directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
    path = directory / safe_name
    path.write_bytes(payload)
    return path


@router.post("/extract")
async def api_extract(
    file: Annotated[UploadFile, File()],
    patient_id: Annotated[str, Form(alias="patientId")],
    document_date: Annotated[str | None, Form(alias="documentDate")] = None,
) -> ExtractResponse:
    """Recognise and propose. Writes nothing."""
    from ocr.engine import OcrUnavailable, read_document
    from ocr.extract import extract_facts

    _require_patient(patient_id)
    when = _document_date(document_date)

    # The one copy of a patient's scanned record on this machine is removed
    # when the stack closes, whatever happens in between.
    with ExitStack() as stack:
        temp = await _to_temp_file(file, stack)
        try:
            document = read_document(temp)
        except OcrUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc

        report = extract_facts(document, patient_id, document_date=when)

    original = temp.name
    return ExtractResponse(
        source_file=original,
        engine=document.engine,
        patient_id=patient_id,
        mean_confidence=round(document.mean_confidence, 3),
        sections_seen=report.sections_seen,
        lines=[
            OcrLineOut(
                text=line.text,
                confidence=round(line.confidence, 3),
                page=line.page,
                line_no=line.line_no,
                reliable=line.is_reliable,
            )
            for line in document.lines
        ],
        facts=[
            OcrFactOut(
                fact_id=fact.fact_id,
                category=fact.category,
                display=fact.display,
                code=fact.code,
                code_system=fact.code_system,
                value=fact.value,
                unit=fact.unit,
                confidence=round(fact.confidence, 3),
                page=fact.page,
                line_no=fact.line_no,
                line_text=fact.line_text,
                also_in=list(fact.also_in),
            )
            for fact in report.facts
        ],
        unmatched=[line.text for line in report.unmatched],
        ambiguous=[line.text for line in report.ambiguous],
        low_confidence=[line.text for line in report.low_confidence],
    )


@router.post("/commit")
async def api_commit(
    file: Annotated[UploadFile, File()],
    patient_id: Annotated[str, Form(alias="patientId")],
    fact_ids: Annotated[str, Form(alias="factIds")],
    document_date: Annotated[str | None, Form(alias="documentDate")] = None,
) -> CommitResponse:
    """Re-read the same file and write only the approved facts.

    The file is uploaded again rather than cached between the two calls. That
    costs a second OCR pass and buys statelessness: there is no server-side
    extraction sitting around half-approved, and no cache key to get wrong in
    a way that commits one patient's facts against another's id.

    Fact ids are deterministic over (file, page, line, code), so the second
    pass reproduces exactly the ids the reviewer saw -- and any that it does
    not reproduce are reported rather than written.
    """
    from ocr.engine import read_document
    from ocr.extract import extract_facts

    _require_patient(patient_id)
    approved = {value for value in fact_ids.split(",") if value.strip()}
    if not approved:
        raise HTTPException(status_code=422, detail="no factIds were approved")

    when = _document_date(document_date)
    with ExitStack() as stack:
        temp = await _to_temp_file(file, stack)
        document = read_document(temp)
        report = extract_facts(document, patient_id, document_date=when)

    rows = [
        fact.as_fact_row() for fact in report.facts if fact.fact_id in approved
    ]
    if not rows:
        raise HTTPException(
            status_code=409,
            detail="none of the approved facts were found on a re-read of this "
            "file -- re-run the extraction and review it again",
        )

    inserted = _insert(rows)
    return CommitResponse(
        inserted=inserted,
        requested=len(rows),
        already_present=len(rows) - inserted,
    )


def _insert(rows: list[dict[str, Any]]) -> int:
    from psycopg.types.json import Jsonb

    from retrieval.db import DbSettings, FACTS_TABLE

    settings = DbSettings.load()
    inserted = 0
    try:
        with settings.connect() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        f"""
                        INSERT INTO {FACTS_TABLE}
                            (fact_id, patient_id, category, safety_class, text,
                             code_system, code, display, value, unit, date,
                             status, source_resource_type, source_resource_id,
                             encounter_id, also_in)
                        VALUES (%(fact_id)s, %(patient_id)s, %(category)s,
                                %(safety_class)s, %(text)s, %(code_system)s,
                                %(code)s, %(display)s, %(value)s, %(unit)s,
                                %(date)s, %(status)s, %(source_resource_type)s,
                                %(source_resource_id)s, %(encounter_id)s,
                                %(also_in)s)
                        ON CONFLICT (fact_id) DO NOTHING
                        """,
                        {**row, "also_in": Jsonb(row.get("also_in") or [])},
                    )
                    inserted += cur.rowcount
            conn.commit()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - must not escape as a bare 500
        log.exception("OCR commit failed")
        raise HTTPException(
            status_code=503,
            detail=f"could not write to the corpus: {exc}. Is Postgres up? "
            f"`docker compose up -d postgres`",
        ) from exc
    return inserted
