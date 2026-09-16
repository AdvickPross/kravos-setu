"""FastAPI routes for Kravos Setu.

Wires ``parse_query -> retrieve -> build_brief`` behind three endpoints. No
retrieval or brief logic lives here -- this module only times each stage and
shapes HTTP responses.

    pwsh> uvicorn backend.main:app --reload
"""

from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.env_file import load_dotenv

# Before any import below reads os.environ. The feature flags in
# backend/llm_polish.py and backend/vector_store.py are evaluated at call
# time, but AbhaConfig.is_configured() and the Pinecone key check are read
# early enough that ordering matters -- and a reader should not have to work
# out which.
load_dotenv()

from backend.abha_routes import router as abha_router  # noqa: E402
from backend.ocr_routes import router as ocr_router  # noqa: E402
from backend.brief import build_brief  # noqa: E402
from backend.conversation import ConversationNotFoundError, answer as answer_chat
from backend.data_layer import get_patient, list_patients
from backend.llm_polish import polish_brief
from backend.models import (
    BriefSource,
    ChatRequest,
    ChatResponse,
    PatientSummary,
    QueryRequest,
    QueryResponse,
    StageTimings,
)
from backend.retrieve import retrieve
from retrieval.ner import parse_query

app = FastAPI(title="Kravos Setu API")

# Next.js dev server. Widen when a real deployment origin exists; this is
# not meant to stay this permissive-by-default in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ABHA identity (M1). Mounted unconditionally: the routes answer 503 with
# a fixable message when ABDM credentials are absent, which is more useful to
# the frontend than a 404 that reads as "this feature does not exist".
app.include_router(abha_router)

# Scanned-record ingestion. /extract proposes, /commit writes -- nothing
# reaches the corpus without a second explicit call. See backend/ocr_routes.py.
app.include_router(ocr_router)


@app.get("/api/patients")
def api_list_patients(q: str | None = None) -> list[PatientSummary]:
    """List patients, optionally filtered by a case-insensitive name substring."""
    patients = list_patients()
    if q:
        needle = q.lower()
        patients = [p for p in patients if p.name and needle in p.name.lower()]
    return patients


@app.get("/api/patients/{patient_id}")
def api_get_patient(patient_id: str) -> PatientSummary:
    try:
        return get_patient(patient_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown patient_id: {patient_id}")


@app.post("/api/query")
def api_query(request: QueryRequest) -> QueryResponse:
    try:
        patient = get_patient(request.patient_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"unknown patient_id: {request.patient_id}"
        )

    parse_start = time.perf_counter()
    parsed = parse_query(request.query)
    parse_ms = (time.perf_counter() - parse_start) * 1000

    result = retrieve(parsed, request.patient_id)

    brief_start = time.perf_counter()
    brief = build_brief(result, patient)
    brief_ms = (time.perf_counter() - brief_start) * 1000

    llm_start = time.perf_counter()
    polished = polish_brief(brief)
    llm_elapsed_ms = (time.perf_counter() - llm_start) * 1000
    # None (not 0) when the flag is off or the call failed -- the polish
    # stage genuinely did not run, distinct from running fast.
    llm_ms = llm_elapsed_ms if polished.source is BriefSource.LLM_POLISHED else None
    brief = polished

    retrieve_ms = result.timings.retrieve_ms or 0.0
    timings = StageTimings(
        parse_ms=parse_ms,
        retrieve_ms=result.timings.retrieve_ms,
        brief_ms=brief_ms,
        vector_ms=result.timings.vector_ms,
        llm_ms=llm_ms,
        total_ms=parse_ms + retrieve_ms + brief_ms + (llm_ms or 0.0),
    )

    return QueryResponse(
        patient=patient,
        query=request.query,
        parsed=parsed,
        brief=brief,
        result=result,
        timings=timings,
    )


@app.post("/api/chat")
def api_chat(request: ChatRequest) -> ChatResponse:
    """Conversational RAG: NER + retrieval, same as /api/query, plus
    conversation memory and a Groq-generated reply instead of the fixed
    template. See backend/conversation.py."""
    try:
        return answer_chat(request.patient_id, request.message, request.conversation_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"unknown patient_id: {request.patient_id}"
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
