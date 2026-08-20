"""backend.retrieve -- entity-driven retrieval and the missingness checklist.

Isolated from the real corpus the same way test_data_layer.py is: each test
points backend.data_layer at a tmp_path fixture so results are deterministic.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from backend import data_layer, retrieve
from backend.models import MissingReason, NarrativeMatch, RetrievalOutcome
from retrieval.models import MatchQuality, ParsedQuery, ResolvedEntity

PATIENT = "patient-1"


def _fact(fact_id: str, **overrides) -> dict:
    base = {
        "fact_id": fact_id,
        "patient_id": PATIENT,
        "category": "LAB",
        "safety_class": "CLINICAL",
        "text": "text",
        "code_system": "http://loinc.org",
        "code": "1234-5",
        "display": "display",
        "value": None,
        "unit": None,
        "date": None,
        "status": "final",
        "source_resource_type": "Observation",
        "source_resource_id": "res-1",
        "encounter_id": "enc-1",
        "also_in": [],
    }
    base.update(overrides)
    return base


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _entity(span: str, quality: MatchQuality, **overrides) -> ResolvedEntity:
    base = dict(
        span=span,
        start=0,
        end=len(span),
        category="LAB",
        code_system="http://loinc.org",
        codes=["1234-5"],
        display=span,
        quality=quality,
        negated=False,
    )
    base.update(overrides)
    return ResolvedEntity(**base)


@pytest.fixture(autouse=True)
def _isolated_storage(monkeypatch, tmp_path):
    facts_dir = tmp_path / "facts_by_patient"
    facts_dir.mkdir()
    patients_path = tmp_path / "patients.jsonl"
    _write_jsonl(patients_path, [])

    monkeypatch.setattr(data_layer, "FACTS_BY_PATIENT_DIR", facts_dir)
    monkeypatch.setattr(data_layer, "PATIENTS_PATH", patients_path)
    monkeypatch.setattr(data_layer, "_patient_facts_cache", {})
    monkeypatch.setattr(data_layer, "_patients_cache", None)
    return facts_dir, patients_path


def _put_patient_facts(facts_dir: Path, patient_id: str, records: list[dict]) -> None:
    _write_jsonl(facts_dir / f"{patient_id}.jsonl", records)


# -------------------------------------------------------------------- retrieve


def test_resolved_entity_returns_facts_with_found_outcome(_isolated_storage):
    facts_dir, _ = _isolated_storage
    _put_patient_facts(facts_dir, PATIENT, [_fact("f1")])
    parsed = ParsedQuery(raw="glucose?", entities=[_entity("glucose", MatchQuality.EXACT)])

    result = retrieve.retrieve(parsed, PATIENT)

    assert len(result.entities) == 1
    assert result.entities[0].outcome is RetrievalOutcome.FOUND
    assert [f.fact_id for f in result.entities[0].facts] == ["f1"]
    assert result.unresolved_spans == []
    assert result.resolved is True


def test_resolved_entity_with_no_matching_facts_is_found_none(_isolated_storage):
    parsed = ParsedQuery(raw="glucose?", entities=[_entity("glucose", MatchQuality.EXACT)])

    result = retrieve.retrieve(parsed, PATIENT)

    assert result.entities[0].outcome is RetrievalOutcome.FOUND_NONE
    assert result.entities[0].facts == []


def test_fuzzy_and_unresolved_entities_are_not_queried(_isolated_storage):
    facts_dir, _ = _isolated_storage
    _put_patient_facts(facts_dir, PATIENT, [_fact("f1")])
    parsed = ParsedQuery(
        raw="glucoze thingy?",
        entities=[
            _entity("glucoze", MatchQuality.FUZZY),
            _entity("thingy", MatchQuality.UNRESOLVED, codes=[], category=None),
        ],
    )

    result = retrieve.retrieve(parsed, PATIENT)

    assert result.entities == []
    assert set(result.unresolved_spans) == {"glucoze", "thingy"}
    assert result.resolved is False


def test_negated_entity_still_retrieves_facts_and_is_flagged(_isolated_storage):
    facts_dir, _ = _isolated_storage
    _put_patient_facts(
        facts_dir, PATIENT, [_fact("f1", category="ALLERGY", code="1191")]
    )
    parsed = ParsedQuery(
        raw="no known allergies",
        entities=[
            _entity(
                "allergies",
                MatchQuality.SYNONYM,
                category="ALLERGY",
                codes=["1191"],
                negated=True,
            )
        ],
    )

    result = retrieve.retrieve(parsed, PATIENT)

    assert result.entities[0].negated_in_query is True
    assert [f.fact_id for f in result.entities[0].facts] == ["f1"]


def test_overlapping_entities_at_same_span_get_separate_results(_isolated_storage):
    """aspirin -> ALLERGY substance + MEDICATION products, two entities
    sharing a span; each gets its own EntityFacts, not a merged pile."""
    facts_dir, _ = _isolated_storage
    _put_patient_facts(
        facts_dir,
        PATIENT,
        [
            _fact("allergy-1", category="ALLERGY", code="1191"),
            _fact("med-1", category="MEDICATION", code="243670"),
        ],
    )
    parsed = ParsedQuery(
        raw="aspirin",
        entities=[
            _entity("aspirin", MatchQuality.EXACT, category="ALLERGY", codes=["1191"], start=0, end=7),
            _entity(
                "aspirin",
                MatchQuality.EXACT,
                category="MEDICATION",
                codes=["243670"],
                start=0,
                end=7,
            ),
        ],
    )

    result = retrieve.retrieve(parsed, PATIENT)

    assert len(result.entities) == 2
    by_category = {e.entity.category: [f.fact_id for f in e.facts] for e in result.entities}
    assert by_category == {"ALLERGY": ["allergy-1"], "MEDICATION": ["med-1"]}


def test_date_window_from_parsed_query_is_applied(_isolated_storage):
    facts_dir, _ = _isolated_storage
    _put_patient_facts(
        facts_dir,
        PATIENT,
        [
            _fact("old", date="2019-01-01T00:00:00+05:30"),
            _fact("recent", date="2021-06-01T00:00:00+05:30"),
        ],
    )
    parsed = ParsedQuery(
        raw="glucose last year",
        entities=[_entity("glucose", MatchQuality.EXACT)],
        date_from=date(2021, 1, 1),
        date_to=date(2021, 12, 31),
    )

    result = retrieve.retrieve(parsed, PATIENT)

    assert [f.fact_id for f in result.entities[0].facts] == ["recent"]


def test_timings_are_populated(_isolated_storage):
    parsed = ParsedQuery(raw="glucose?", entities=[_entity("glucose", MatchQuality.EXACT)])
    result = retrieve.retrieve(parsed, PATIENT)
    assert result.timings.retrieve_ms is not None
    assert result.timings.retrieve_ms >= 0


def test_all_facts_deduplicates_across_entities_sharing_a_fact(_isolated_storage):
    facts_dir, _ = _isolated_storage
    _put_patient_facts(facts_dir, PATIENT, [_fact("shared", code="1234-5")])
    parsed = ParsedQuery(
        raw="glucose glucose",
        entities=[
            _entity("glucose", MatchQuality.EXACT, start=0, end=7),
            _entity("glucose", MatchQuality.SYNONYM, start=8, end=15),
        ],
    )
    result = retrieve.retrieve(parsed, PATIENT)
    assert len(result.all_facts()) == 1


# -------------------------------------------------------------- compute_missing


def _expected_facts_file(tmp_path: Path, items: list[dict]) -> Path:
    path = tmp_path / "expected_facts.json"
    path.write_text(json.dumps({"items": items}), encoding="utf-8")
    return path


def test_missing_item_is_absent_when_nothing_on_file(tmp_path):
    expected_path = _expected_facts_file(
        tmp_path,
        [{"key": "cbc", "label": "CBC", "categories": ["LAB"], "codes": ["1234-5"], "window_days": 90}],
    )
    missing = retrieve.compute_missing(PATIENT, expected_facts_path=expected_path)
    assert len(missing) == 1
    assert missing[0].reason is MissingReason.ABSENT


def test_missing_item_absent_for_category_only_check_with_no_facts(tmp_path, _isolated_storage):
    expected_path = _expected_facts_file(
        tmp_path,
        [{"key": "meds", "label": "Meds", "categories": ["MEDICATION"], "codes": [], "window_days": None}],
    )
    missing = retrieve.compute_missing(PATIENT, expected_facts_path=expected_path)
    assert len(missing) == 1
    assert missing[0].reason is MissingReason.ABSENT


def test_missing_item_not_flagged_when_category_only_check_has_any_fact(
    tmp_path, _isolated_storage
):
    facts_dir, _ = _isolated_storage
    _put_patient_facts(facts_dir, PATIENT, [_fact("f1", category="MEDICATION")])
    expected_path = _expected_facts_file(
        tmp_path,
        [{"key": "meds", "label": "Meds", "categories": ["MEDICATION"], "codes": [], "window_days": None}],
    )
    missing = retrieve.compute_missing(PATIENT, expected_facts_path=expected_path)
    assert missing == []


def test_missing_item_is_stale_when_outside_recency_window(tmp_path, _isolated_storage):
    facts_dir, _ = _isolated_storage
    _put_patient_facts(
        facts_dir,
        PATIENT,
        [_fact("old", category="LAB", code="1234-5", date="2020-01-01T00:00:00+05:30")],
    )
    expected_path = _expected_facts_file(
        tmp_path,
        [{"key": "cbc", "label": "CBC", "categories": ["LAB"], "codes": ["1234-5"], "window_days": 90}],
    )
    missing = retrieve.compute_missing(
        PATIENT, as_of=date(2026, 1, 1), expected_facts_path=expected_path
    )
    assert len(missing) == 1
    assert missing[0].reason is MissingReason.STALE
    assert missing[0].last_seen is not None


def test_missing_item_not_flagged_when_recent_fact_exists(tmp_path, _isolated_storage):
    facts_dir, _ = _isolated_storage
    _put_patient_facts(
        facts_dir,
        PATIENT,
        [_fact("recent", category="LAB", code="1234-5", date="2025-12-15T00:00:00+05:30")],
    )
    expected_path = _expected_facts_file(
        tmp_path,
        [{"key": "cbc", "label": "CBC", "categories": ["LAB"], "codes": ["1234-5"], "window_days": 90}],
    )
    missing = retrieve.compute_missing(
        PATIENT, as_of=date(2026, 1, 1), expected_facts_path=expected_path
    )
    assert missing == []


def test_real_expected_facts_file_loads_and_is_well_shaped():
    """Guards the shipped data/expected_facts.json against a bad edit --
    this file is data, not code, so nothing else would catch a typo."""
    items = retrieve._load_expected_facts(retrieve.DEFAULT_EXPECTED_FACTS_PATH)
    assert items
    for item in items:
        assert item["key"]
        assert item["label"]
        assert item["categories"]


# ---------------------------------------------------------- narrative fallback


def test_vector_fallback_not_attempted_when_something_resolved(monkeypatch, _isolated_storage):
    """Fallback only, per the spec -- never runs alongside a coded match."""
    facts_dir, _ = _isolated_storage
    _put_patient_facts(facts_dir, PATIENT, [_fact("f1")])
    called = []
    monkeypatch.setattr(retrieve, "vector_search_enabled", lambda: True)
    monkeypatch.setattr(retrieve, "_narrative_fallback", lambda *a, **k: called.append(1) or [])

    parsed = ParsedQuery(raw="glucose?", entities=[_entity("glucose", MatchQuality.EXACT)])
    result = retrieve.retrieve(parsed, PATIENT)

    assert called == []
    assert result.narrative_matches == []
    assert result.timings.vector_ms is None


def test_vector_fallback_not_attempted_when_flag_disabled(monkeypatch, _isolated_storage):
    monkeypatch.setattr(retrieve, "vector_search_enabled", lambda: False)

    def _boom(*a, **k):
        raise AssertionError("VectorStore should never be constructed when the flag is off")

    monkeypatch.setattr(retrieve, "VectorStore", _boom)
    parsed = ParsedQuery(raw="something unresolved", entities=[_entity("x", MatchQuality.UNRESOLVED, category=None, codes=[])])

    result = retrieve.retrieve(parsed, PATIENT)

    assert result.narrative_matches == []
    assert result.timings.vector_ms is None


def test_vector_fallback_attempted_when_nothing_resolved_and_flag_on(monkeypatch, _isolated_storage):
    monkeypatch.setattr(retrieve, "vector_search_enabled", lambda: True)

    class _FakeHit:
        fact_id = "report-1"
        encounter_id = "enc-1"
        date = None
        section = "History of Present Illness"
        text = "patient mentioned intermittent chest pain"
        score = 0.83

    class _FakeStore:
        def __init__(self, *a, **k):
            pass

        def search(self, patient_id, query_text, top_k=5):
            assert patient_id == PATIENT
            return [_FakeHit()]

    monkeypatch.setattr(retrieve, "VectorStore", _FakeStore)
    parsed = ParsedQuery(
        raw="has he ever mentioned chest pain",
        entities=[_entity("chest pain", MatchQuality.UNRESOLVED, category=None, codes=[])],
    )

    result = retrieve.retrieve(parsed, PATIENT)

    assert len(result.narrative_matches) == 1
    match = result.narrative_matches[0]
    assert isinstance(match, NarrativeMatch)
    assert match.fact_id == "report-1"
    assert match.score == 0.83
    assert result.timings.vector_ms is not None


def test_vector_fallback_degrades_gracefully_when_store_raises(monkeypatch, _isolated_storage):
    monkeypatch.setattr(retrieve, "vector_search_enabled", lambda: True)

    class _RaisingStore:
        def __init__(self, *a, **k):
            raise RuntimeError("no Pinecone account configured")

    monkeypatch.setattr(retrieve, "VectorStore", _RaisingStore)
    parsed = ParsedQuery(
        raw="anything",
        entities=[_entity("anything", MatchQuality.UNRESOLVED, category=None, codes=[])],
    )

    result = retrieve.retrieve(parsed, PATIENT)

    assert result.narrative_matches == []


def test_vector_fallback_attempted_when_query_has_no_entities_at_all(monkeypatch, _isolated_storage):
    """Empty entities list (nothing even attempted to resolve) is still
    'nothing resolved' -- the fallback should still get a chance."""
    monkeypatch.setattr(retrieve, "vector_search_enabled", lambda: True)
    called = []

    class _FakeStore:
        def __init__(self, *a, **k):
            pass

        def search(self, patient_id, query_text, top_k=5):
            called.append((patient_id, query_text))
            return []

    monkeypatch.setattr(retrieve, "VectorStore", _FakeStore)
    parsed = ParsedQuery(raw="how is he doing overall", entities=[])

    retrieve.retrieve(parsed, PATIENT)

    assert called == [(PATIENT, "how is he doing overall")]
