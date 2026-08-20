"""Vector search over REPORT free-text notes, via Pinecone's hosted embedding.

The only place vector search exists in this system: REPORT is the ~58,000
free-text clinical notes (~5% of the corpus) with no code to filter on --
everything else is coded and belongs in ``backend/data_layer.py``. Wired
into ``retrieve()`` as a fallback only, attempted solely when nothing in a
query resolves to a code. Never a replacement for coded retrieval.

**Namespace-per-patient, not a metadata filter, is what makes cross-patient
leakage structurally impossible.** Every method that touches an index
requires ``patient_id`` and uses it as the Pinecone namespace, so there is
no code path here that can search or write across patients even by
accident. A metadata filter is applied on top, as defense in depth, but the
namespace boundary is the one that can't be silently dropped in a future
edit.

``pinecone`` is imported lazily inside the methods that need it, so this
module -- and everything that imports it, including ``backend/retrieve.py``
-- stays importable with no Pinecone account configured. Same pattern as
``psycopg`` in ``retrieval/db.py`` and ``anthropic`` in
``backend/llm_polish.py``.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Iterator

from pydantic import BaseModel

_API_KEY_ENV_VAR = "PINECONE_API_KEY"
_INDEX_NAME_ENV_VAR = "PINECONE_INDEX_NAME"
DEFAULT_INDEX_NAME = "kravos-reports"

# Synthea's generated notes use consistent single-`#` Markdown section
# headers (# Chief Complaint, # History of Present Illness, # Allergies,
# # Medications, # Assessment and Plan, ...). Splitting on those keeps each
# chunk to one clinical topic instead of embedding a whole multi-section
# note as one vector. Deliberately does not match `##` sub-headers (e.g.
# the "## Plan" under Assessment and Plan) -- those roll up into their
# parent section's chunk rather than fragmenting further.
_SECTION_HEADER_RE = re.compile(r"^#[ \t]+(.+)$", re.MULTILINE)


class ReportChunk(BaseModel):
    """One section of one REPORT fact, ready to upsert."""

    chunk_id: str
    patient_id: str
    fact_id: str
    encounter_id: str | None = None
    date: datetime | None = None
    section: str
    text: str


class VectorMatch(BaseModel):
    """One hit from a vector search, with enough to cite and re-fetch."""

    fact_id: str
    encounter_id: str | None = None
    date: datetime | None = None
    section: str
    text: str
    score: float


def vector_search_enabled() -> bool:
    return bool(os.environ.get(_API_KEY_ENV_VAR, "").strip())


def chunk_report_text(text: str) -> list[tuple[str, str]]:
    """Split one REPORT fact's free text into ``(section, body)`` pairs.

    Splits on the note's own ``# Section`` headers. Text before the first
    header (just the date line, in this corpus) is dropped -- it carries no
    clinical content. A note with no headers at all is returned as one
    chunk under "Note" so nothing is silently lost.
    """
    headers = list(_SECTION_HEADER_RE.finditer(text))
    if not headers:
        stripped = text.strip()
        return [("Note", stripped)] if stripped else []

    chunks: list[tuple[str, str]] = []
    for i, match in enumerate(headers):
        section = match.group(1).strip()
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[start:end].strip()
        if body:
            chunks.append((section, body))
    return chunks


def iter_report_chunks(fact: dict) -> Iterator[ReportChunk]:
    """``fact`` is one parsed facts.jsonl record with ``category == "REPORT"``."""
    for i, (section, body) in enumerate(chunk_report_text(fact.get("text") or "")):
        yield ReportChunk(
            chunk_id=f"{fact['fact_id']}-{i}",
            patient_id=fact["patient_id"],
            fact_id=fact["fact_id"],
            encounter_id=fact.get("encounter_id"),
            date=fact.get("date"),
            section=section,
            text=body,
        )


class VectorStore:
    """Thin wrapper over one Pinecone index, namespaced by ``patient_id``."""

    def __init__(self, api_key: str | None = None, index_name: str | None = None):
        self._api_key = api_key or os.environ.get(_API_KEY_ENV_VAR, "")
        self._index_name = index_name or os.environ.get(_INDEX_NAME_ENV_VAR, DEFAULT_INDEX_NAME)
        if not self._api_key:
            raise RuntimeError(f"{_API_KEY_ENV_VAR} not set (see .env.example)")
        self._index = None

    def _get_index(self):
        if self._index is None:
            import pinecone  # lazy: see module docstring

            client = pinecone.Pinecone(api_key=self._api_key)
            # pc.index() (lowercase) resolves the index host via a describe
            # call; pc.Index() is a legacy compatibility shim for the same.
            self._index = client.index(name=self._index_name)
        return self._index

    def upsert_chunks(self, chunks: list[ReportChunk]) -> None:
        """Upsert chunks, grouped by patient so each patient's chunks land
        in that patient's own namespace -- never a shared one."""
        index = self._get_index()
        by_patient: dict[str, list[dict]] = {}
        for chunk in chunks:
            record = {
                "_id": chunk.chunk_id,
                "chunk_text": chunk.text,
                "patient_id": chunk.patient_id,
                "fact_id": chunk.fact_id,
                "encounter_id": chunk.encounter_id or "",
                "date": chunk.date.isoformat() if chunk.date else "",
                "section": chunk.section,
            }
            by_patient.setdefault(chunk.patient_id, []).append(record)
        for patient_id, records in by_patient.items():
            index.upsert_records(records=records, namespace=patient_id)

    def search(self, patient_id: str, query_text: str, top_k: int = 5) -> list[VectorMatch]:
        """Search one patient's REPORT chunks.

        ``patient_id`` is required and always becomes the namespace -- there
        is no parameter combination that searches across patients.
        """
        if not patient_id:
            raise ValueError("patient_id is required")
        index = self._get_index()
        response = index.search(
            namespace=patient_id,
            top_k=top_k,
            inputs={"text": query_text},
            # Redundant by construction (the namespace above already scopes
            # every chunk to this patient) -- kept as defense in depth, not
            # as the mechanism this relies on.
            filter={"patient_id": patient_id},
            fields=["chunk_text", "fact_id", "encounter_id", "date", "section"],
        )
        matches: list[VectorMatch] = []
        for hit in response.result.hits:
            fields = hit.fields
            matches.append(
                VectorMatch(
                    fact_id=fields["fact_id"],
                    encounter_id=fields.get("encounter_id") or None,
                    date=fields.get("date") or None,
                    section=fields.get("section", ""),
                    text=fields.get("chunk_text", ""),
                    score=hit.score,
                )
            )
        return matches
