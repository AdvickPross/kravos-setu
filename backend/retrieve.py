"""Query -> patient-scoped retrieval, plus the missingness check.

``backend.data_layer`` is the only module that touches fact storage; this
module drives it once per resolved entity to turn a ``ParsedQuery`` into a
``RetrievalResult``, and runs the pre-op missingness checklist
(``data/expected_facts.json``) as a set difference over the same store.

No routing decision tree here -- route is just the resolved entity's
``category``, already produced by NER.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

from backend.data_layer import get_patient_facts
from backend.models import (
    EntityFacts,
    MissingItem,
    MissingReason,
    NarrativeMatch,
    RetrievalOutcome,
    RetrievalResult,
    StageTimings,
)
from backend.vector_store import VectorStore, vector_search_enabled
from retrieval.models import MatchQuality, ParsedQuery

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXPECTED_FACTS_PATH = REPO_ROOT / "data" / "expected_facts.json"

# FUZZY and UNRESOLVED entities are not queried -- an honest unresolved span
# beats guessing. Safety-critical FUZZY handling (push to a vector fallback)
# belongs to a router this pass deliberately doesn't build.
_RESOLVED_QUALITIES = {MatchQuality.EXACT, MatchQuality.SYNONYM}


def _load_expected_facts(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw["items"]


def compute_missing(
    patient_id: str,
    as_of: date | None = None,
    expected_facts_path: Path = DEFAULT_EXPECTED_FACTS_PATH,
) -> list[MissingItem]:
    """Set difference over coded facts against the pre-op checklist.

    Not a model: each checklist item names a category/code set and is
    ``ABSENT`` when nothing on file matches, or ``STALE`` when the most
    recent match falls outside its recency window.
    """
    as_of = as_of or date.today()
    missing: list[MissingItem] = []

    for entry in _load_expected_facts(expected_facts_path):
        categories = entry["categories"]
        codes = entry.get("codes") or None
        window_days = entry.get("window_days")

        facts = get_patient_facts(patient_id, categories=categories, codes=codes)
        if not facts:
            missing.append(
                MissingItem(
                    key=entry["key"],
                    label=entry["label"],
                    reason=MissingReason.ABSENT,
                    categories=categories,
                    codes=codes or [],
                    window_days=window_days,
                )
            )
            continue

        if window_days is None:
            continue

        most_recent = facts[0].date  # get_patient_facts ranks most-recent-first
        cutoff = as_of - timedelta(days=window_days)
        if most_recent is None or most_recent.date() < cutoff:
            missing.append(
                MissingItem(
                    key=entry["key"],
                    label=entry["label"],
                    reason=MissingReason.STALE,
                    categories=categories,
                    codes=codes or [],
                    window_days=window_days,
                    last_seen=most_recent,
                )
            )

    return missing


def _narrative_fallback(patient_id: str, query_text: str) -> list[NarrativeMatch]:
    """REPORT-note vector search, attempted only when nothing coded resolved.

    Degrades to an empty list on any failure -- unconfigured Pinecone
    account, network error, bad response -- the same graceful-fallback
    contract as backend/llm_polish.py. This is a stretch fallback sitting
    behind the structured path, never something a request can fail on.
    """
    if not vector_search_enabled():
        return []
    try:
        store = VectorStore()
        hits = store.search(patient_id, query_text)
    except Exception:
        return []
    return [
        NarrativeMatch(
            fact_id=hit.fact_id,
            encounter_id=hit.encounter_id,
            date=hit.date,
            section=hit.section,
            text=hit.text,
            score=hit.score,
        )
        for hit in hits
    ]


def retrieve(parsed: ParsedQuery, patient_id: str) -> RetrievalResult:
    start = time.perf_counter()

    entities: list[EntityFacts] = []
    unresolved_spans: list[str] = []

    for entity in parsed.entities:
        if entity.quality not in _RESOLVED_QUALITIES:
            unresolved_spans.append(entity.span)
            continue

        # Overlapping entities (aspirin -> ALLERGY substance + MEDICATION
        # products) are separate ResolvedEntity objects sharing a span, so
        # they naturally get separate lookups and separate EntityFacts here.
        facts = get_patient_facts(
            patient_id,
            categories=[entity.category] if entity.category else None,
            codes=entity.codes or None,
            date_from=parsed.date_from,
            date_to=parsed.date_to,
        )
        entities.append(
            EntityFacts(
                entity=entity,
                facts=facts,
                outcome=RetrievalOutcome.FOUND if facts else RetrievalOutcome.FOUND_NONE,
                # Retrieved regardless of negation -- "no known allergies"
                # still needs the on-file allergy facts surfaced, not dropped.
                negated_in_query=entity.negated,
            )
        )

    missing = compute_missing(patient_id)

    # Fallback only, per the spec: attempted solely when nothing in the
    # query resolved to a code at all -- never in addition to a coded
    # match, and never in place of trying the structured path first.
    narrative_matches: list[NarrativeMatch] = []
    vector_ms: float | None = None
    if not entities:
        vector_start = time.perf_counter()
        narrative_matches = _narrative_fallback(patient_id, parsed.raw)
        if vector_search_enabled():
            vector_ms = (time.perf_counter() - vector_start) * 1000

    elapsed_ms = (time.perf_counter() - start) * 1000
    return RetrievalResult(
        patient_id=patient_id,
        query=parsed.raw,
        entities=entities,
        unresolved_spans=unresolved_spans,
        date_from=parsed.date_from,
        date_to=parsed.date_to,
        missing=missing,
        narrative_matches=narrative_matches,
        timings=StageTimings(retrieve_ms=elapsed_ms, vector_ms=vector_ms),
    )
