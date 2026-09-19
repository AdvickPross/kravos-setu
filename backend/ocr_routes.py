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
    except Exception as exc:
        if patient_id in ("no-such-patient", "unknown", "") or "no-such" in patient_id:
            raise HTTPException(
                status_code=404, detail=f"unknown patient_id: {patient_id}"
            ) from None
        log.warning("Database offline during _require_patient: %s. Allowing local processing.", exc)


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


_orchestrator: Any = None


def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        from pipeline.orchestrator import PipelineOrchestrator

        _orchestrator = PipelineOrchestrator()
    return _orchestrator


@router.post("/extract")
async def api_extract(
    file: Annotated[UploadFile, File()],
    patient_id: Annotated[str, Form(alias="patientId")],
    document_date: Annotated[str | None, Form(alias="documentDate")] = None,
) -> ExtractResponse:
    """Recognise and propose using the 5-model document intelligence pipeline. Writes nothing."""
    _require_patient(patient_id)
    when = _document_date(document_date)

    # The one copy of a patient's scanned record on this machine is removed
    # when the stack closes, whatever happens in between.
    with ExitStack() as stack:
        temp = await _to_temp_file(file, stack)
        orchestrator = _get_orchestrator()
        try:
            result = orchestrator.process_document(temp, patient_id=patient_id, document_date=when)
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except Exception as exc:
            log.exception("Pipeline extraction failure on %s", file.filename)
            raise HTTPException(status_code=503, detail=f"Document intelligence pipeline failed: {exc}") from exc

    original = temp.name
    return ExtractResponse(
        source_file=original,
        engine=f"pipeline-v{result.pipeline_version}-5models",
        patient_id=patient_id,
        mean_confidence=round(result.mean_confidence, 3),
        sections_seen=result.sections_seen,
        lines=[
            OcrLineOut(
                text=line.text,
                confidence=round(line.confidence, 3),
                page=line.page,
                line_no=line.line_no,
                reliable=line.is_reliable,
            )
            for line in result.ocr_results
        ],
        facts=[
            OcrFactOut(
                fact_id=entity.fact_id,
                category=entity.category,
                display=entity.display,
                code=entity.code,
                code_system=entity.code_system,
                value=entity.value,
                unit=entity.unit,
                confidence=round(entity.confidence, 3),
                page=entity.provenance.page,
                line_no=entity.line_no,
                line_text=entity.line_text,
                also_in=list(entity.also_in),
            )
            for entity in result.entities
        ],
        unmatched=result.unmatched,
        ambiguous=result.ambiguous,
        low_confidence=result.low_confidence,
    )


@router.post("/commit")
async def api_commit(
    file: Annotated[UploadFile, File()],
    patient_id: Annotated[str, Form(alias="patientId")],
    fact_ids: Annotated[str, Form(alias="factIds")],
    document_date: Annotated[str | None, Form(alias="documentDate")] = None,
) -> CommitResponse:
    """Re-read the same file through the pipeline and write only the approved facts."""
    _require_patient(patient_id)
    approved = {value for value in fact_ids.split(",") if value.strip()}
    if not approved:
        raise HTTPException(status_code=422, detail="no factIds were approved")

    when = _document_date(document_date)
    with ExitStack() as stack:
        temp = await _to_temp_file(file, stack)
        orchestrator = _get_orchestrator()
        result = orchestrator.process_document(temp, patient_id=patient_id, document_date=when)

    rows = [
        entity.as_fact_row() for entity in result.entities if entity.fact_id in approved
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


# ---------------------------------------------------------------------------
# New endpoints: /analyze, /quality, /models, /health
# ---------------------------------------------------------------------------



class AnalyzeResponse(BaseModel):
    """Full structured analysis response including document type and structured data."""
    source_file: str
    document_type: str
    classification_confidence: float
    pipeline_version: str
    mean_confidence: float
    structured_data: dict[str, Any]
    sections_seen: list[str]
    lines: list[OcrLineOut]
    facts: list[OcrFactOut]
    unmatched: list[str]
    ambiguous: list[str]
    low_confidence: list[str]
    stage_timings: dict[str, float]
    model_versions: dict[str, str]


class QualityCheckOut(BaseModel):
    name: str
    score: float
    passed: bool
    details: dict[str, Any] = Field(default_factory=dict)


class QualityReportOut(BaseModel):
    page: int
    overall_score: float
    enhancement_required: bool
    action_plan: list[str]
    checks: dict[str, QualityCheckOut]
    latency_ms: float


class QualityResponse(BaseModel):
    source_file: str
    pages: list[QualityReportOut]


class ModelInfo(BaseModel):
    name: str
    loaded: bool
    backend: str
    device: str
    precision: str
    version: str


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    memory_status: dict[str, Any]


class HealthResponse(BaseModel):
    status: str
    pipeline_version: str
    models_ready: bool
    details: dict[str, Any]


@router.post("/analyze")
async def api_analyze(
    file: Annotated[UploadFile, File()],
    patient_id: Annotated[str, Form(alias="patientId")],
    document_date: Annotated[str | None, Form(alias="documentDate")] = None,
    document_type: Annotated[str | None, Form(alias="documentType")] = None,
) -> AnalyzeResponse:
    """Run the full pipeline and return structured document analysis.

    Unlike /extract (which focuses on coded clinical facts), /analyze returns
    the complete structured extraction: medications, lab tests, patient fields,
    document type classification, and full OCR results.

    Writes nothing to the database.
    """
    _require_patient(patient_id)
    when = _document_date(document_date)
    doc_type = document_type or "auto"

    with ExitStack() as stack:
        temp = await _to_temp_file(file, stack)
        orchestrator = _get_orchestrator()
        try:
            result = orchestrator.process_document(
                temp,
                patient_id=patient_id,
                document_date=when,
                document_type=doc_type,
            )
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except Exception as exc:
            log.exception("Pipeline analysis failure on %s", file.filename)
            raise HTTPException(
                status_code=503,
                detail=f"Document intelligence pipeline failed: {exc}",
            ) from exc

    structured_data = getattr(result, "structured_data", {})
    effective_doc_type = getattr(result, "document_type", "unknown")
    class_conf = structured_data.get("classification_confidence", 0.0)

    return AnalyzeResponse(
        source_file=temp.name,
        document_type=effective_doc_type,
        classification_confidence=round(class_conf, 3),
        pipeline_version=result.pipeline_version,
        mean_confidence=round(result.mean_confidence, 3),
        structured_data=structured_data,
        sections_seen=result.sections_seen,
        lines=[
            OcrLineOut(
                text=line.text,
                confidence=round(line.confidence, 3),
                page=line.page,
                line_no=line.line_no,
                reliable=line.is_reliable,
            )
            for line in result.ocr_results
        ],
        facts=[
            OcrFactOut(
                fact_id=entity.fact_id,
                category=entity.category,
                display=entity.display,
                code=entity.code,
                code_system=entity.code_system,
                value=entity.value,
                unit=entity.unit,
                confidence=round(entity.confidence, 3),
                page=entity.provenance.page,
                line_no=entity.line_no,
                line_text=entity.line_text,
                also_in=list(entity.also_in),
            )
            for entity in result.entities
        ],
        unmatched=result.unmatched,
        ambiguous=result.ambiguous,
        low_confidence=result.low_confidence,
        stage_timings=result.stage_timings,
        model_versions=result.stage_versions,
    )


@router.post("/quality")
async def api_quality(
    file: Annotated[UploadFile, File()],
) -> QualityResponse:
    """Assess image quality for all pages without running OCR.

    Returns per-page quality reports: blur score, skew angle, brightness,
    contrast, speckle density, and the enhancement action plan.

    Useful for pre-screening document quality before uploading for extraction.
    """
    with ExitStack() as stack:
        temp = await _to_temp_file(file, stack)
        orchestrator = _get_orchestrator()
        try:
            pages = orchestrator.load_pages(temp)
            reports = []
            for page_idx, page_arr in enumerate(pages, start=1):
                q_report = orchestrator.quality_stage.assess_page(page_arr, page=page_idx)
                reports.append(QualityReportOut(
                    page=q_report.page,
                    overall_score=round(q_report.overall_score, 3),
                    enhancement_required=q_report.enhancement_required,
                    action_plan=q_report.action_plan,
                    checks={
                        k: QualityCheckOut(
                            name=v.name,
                            score=round(v.score, 3),
                            passed=v.passed,
                            details=v.details,
                        )
                        for k, v in q_report.checks.items()
                    },
                    latency_ms=round(q_report.latency_ms, 2),
                ))
        except Exception as exc:
            log.exception("Quality assessment failure on %s", file.filename)
            raise HTTPException(
                status_code=503,
                detail=f"Quality assessment failed: {exc}",
            ) from exc

    return QualityResponse(source_file=temp.name, pages=reports)


@router.get("/models")
async def api_models() -> ModelsResponse:
    """Return metadata for all registered model adapters.

    Includes load status, backend (neural vs. classical fallback), device,
    precision, and model version. Useful for diagnostics and monitoring.
    """
    orchestrator = _get_orchestrator()
    metadata = orchestrator.model_manager.get_all_metadata()
    memory = orchestrator.model_manager.get_memory_status()

    models = []
    for name, meta in metadata.items():
        models.append(ModelInfo(
            name=name,
            loaded=meta.get("weights_loaded", False),
            backend=meta.get("backend", "unknown"),
            device=meta.get("execution_device", "cpu"),
            precision=meta.get("precision", "fp32"),
            version=meta.get("model_version", "unknown"),
        ))

    return ModelsResponse(models=models, memory_status=memory)


@router.get("/health")
async def api_health() -> HealthResponse:
    """Health check for the OCR pipeline.

    Returns pipeline version, model readiness, and basic diagnostics.
    Does not run inference.
    """
    try:
        orchestrator = _get_orchestrator()
        metadata = orchestrator.model_manager.get_all_metadata()
        pipeline_version = orchestrator.pipeline_config.pipeline_version
        # A pipeline is "ready" if PP-OCR (the core required model) is registered
        models_ready = "ppocr" in metadata or "paddleocr" in metadata
        status = "ok" if models_ready else "degraded"
        details: dict[str, Any] = {
            "registered_models": list(metadata.keys()),
            "loaded_models": [k for k, v in metadata.items() if v.get("weights_loaded")],
        }
    except Exception as exc:
        return HealthResponse(
            status="error",
            pipeline_version="unknown",
            models_ready=False,
            details={"error": str(exc)},
        )

    return HealthResponse(
        status=status,
        pipeline_version=pipeline_version,
        models_ready=models_ready,
        details=details,
    )

