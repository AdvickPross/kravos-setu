"""backend.brief -- deterministic template rendering.

Builds RetrievalResult / EntityFacts / Fact objects directly rather than
going through retrieve() or the corpus, so section grouping, ordering, and
formatting are checked in isolation.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from backend.brief import build_brief
from backend.models import (
    BriefSource,
    EntityFacts,
    Fact,
    MissingItem,
    MissingReason,
    PatientSummary,
    RetrievalOutcome,
    RetrievalResult,
)
from retrieval.models import MatchQuality, ResolvedEntity

PATIENT_ID = "patient-1"


def _fact(fact_id: str, **overrides) -> Fact:
    base = dict(
        fact_id=fact_id,
        patient_id=PATIENT_ID,
        category="LAB",
        display="display",
        value=None,
        unit=None,
        date=None,
        status=None,
        code="1234-5",
    )
    base.update(overrides)
    return Fact(**base)


def _entity(category: str, **overrides) -> ResolvedEntity:
    base = dict(
        span="span",
        start=0,
        end=4,
        category=category,
        codes=["1234-5"],
        display="span",
        quality=MatchQuality.EXACT,
    )
    base.update(overrides)
    return ResolvedEntity(**base)


def _entity_facts(category: str, facts: list[Fact], **overrides) -> EntityFacts:
    outcome = RetrievalOutcome.FOUND if facts else RetrievalOutcome.FOUND_NONE
    base = dict(entity=_entity(category), facts=facts, outcome=outcome)
    base.update(overrides)
    return EntityFacts(**base)


def _patient(**overrides) -> PatientSummary:
    base = dict(patient_id=PATIENT_ID, name="Test Patient", birth_date=date(1990, 1, 1), gender="female")
    base.update(overrides)
    return PatientSummary(**base)


def _result(entities: list[EntityFacts], **overrides) -> RetrievalResult:
    base = dict(patient_id=PATIENT_ID, query="a query", entities=entities)
    base.update(overrides)
    return RetrievalResult(**base)


def test_medication_line_includes_status_and_date():
    fact = _fact(
        "med-1",
        category="MEDICATION",
        display="clopidogrel 75 MG Oral Tablet",
        status="stopped",
        date=datetime(2016, 3, 5, tzinfo=timezone.utc),
    )
    result = _result([_entity_facts("MEDICATION", [fact])])

    brief = build_brief(result, _patient())

    section = next(s for s in brief.sections if s.title == "Medications")
    assert section.lines[0].text == "clopidogrel 75 MG Oral Tablet — stopped 2016-03-05"


def test_lab_line_includes_value_and_unit():
    fact = _fact(
        "lab-1",
        category="LAB",
        display="Glucose [Mass/volume] in Serum or Plasma",
        value="91.3",
        unit="mg/dL",
        status="final",
        date=datetime(2020, 6, 1, tzinfo=timezone.utc),
    )
    result = _result([_entity_facts("LAB", [fact])])

    brief = build_brief(result, _patient())

    line = brief.sections[0].lines[0]
    assert "91.3 mg/dL" in line.text
    assert line.date == fact.date


def test_allergy_section_gets_explicit_empty_note_when_none_on_file():
    result = _result([_entity_facts("ALLERGY", [])])

    brief = build_brief(result, _patient())

    section = next(s for s in brief.sections if s.title == "Allergies")
    assert section.is_empty
    assert section.empty_note == "No allergy records on file."


def test_labs_sorted_most_recent_first():
    older = _fact("older", date=datetime(2019, 1, 1, tzinfo=timezone.utc))
    newer = _fact("newer", date=datetime(2021, 1, 1, tzinfo=timezone.utc))
    result = _result([_entity_facts("LAB", [older, newer])])

    brief = build_brief(result, _patient())

    assert [line.fact_ids[0] for line in brief.sections[0].lines] == ["newer", "older"]


def test_every_line_carries_its_fact_id():
    fact = _fact("f1")
    result = _result([_entity_facts("LAB", [fact])])

    brief = build_brief(result, _patient())

    assert brief.sections[0].lines[0].fact_ids == ["f1"]
    assert brief.fact_ids == ["f1"]


def test_sections_ordered_by_clinical_priority_not_input_order():
    result = _result(
        [
            _entity_facts("LAB", [_fact("lab-1")]),
            _entity_facts("ALLERGY", [_fact("allergy-1", category="ALLERGY")]),
            _entity_facts("MEDICATION", [_fact("med-1", category="MEDICATION")]),
        ]
    )

    brief = build_brief(result, _patient())

    titles = [s.title for s in brief.sections]
    assert titles.index("Allergies") < titles.index("Medications") < titles.index("Labs")


def test_facts_shared_across_entities_in_same_category_are_deduped():
    shared = _fact("shared")
    result = _result(
        [
            _entity_facts("LAB", [shared]),
            _entity_facts("LAB", [shared]),
        ]
    )

    brief = build_brief(result, _patient())

    assert len(brief.sections[0].lines) == 1


def test_missing_items_pass_through_to_brief():
    missing = [MissingItem(key="cbc", label="CBC", reason=MissingReason.ABSENT)]
    result = _result([], missing=missing)

    brief = build_brief(result, _patient())

    assert brief.missing == missing


def test_source_is_template_and_prose_is_unset():
    brief = build_brief(_result([]), _patient())
    assert brief.source is BriefSource.TEMPLATE
    assert brief.prose is None


def test_headline_mentions_patient_name_and_fact_count():
    fact = _fact("f1")
    result = _result([_entity_facts("LAB", [fact])])

    brief = build_brief(result, _patient(name="Jane Doe"))

    assert "Jane Doe" in brief.headline
    assert "1 fact" in brief.headline
