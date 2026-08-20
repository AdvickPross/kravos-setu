"""Resolver behaviour tests.

Behaviour only -- no accuracy numbers. The probe queries in queries.md are a
debugging instrument, and the gold eval set is a separate artifact.
"""

from __future__ import annotations

from datetime import date

import pytest

from retrieval.models import MatchQuality
from retrieval.ner import QueryNER

TODAY = date(2026, 8, 19)


@pytest.fixture(scope="module")
def ner() -> QueryNER:
    return QueryNER()


def entities(ner: QueryNER, query: str, **kwargs):
    return ner.parse(query, today=TODAY, **kwargs).entities


def spans(ner: QueryNER, query: str) -> list[str]:
    return [e.span for e in entities(ner, query)]


def find(ner: QueryNER, query: str, span: str):
    for entity in entities(ner, query):
        if entity.span.lower() == span.lower():
            return entity
    raise AssertionError(f"{span!r} not found in {spans(ner, query)}")


# ------------------------------------------------------------------ spans --


def test_longest_span_wins(ner: QueryNER) -> None:
    """`blood pressure` beats `pressure`, `cholesterol in hdl` beats `cholesterol`."""
    entity = find(ner, "show me blood pressure readings", "blood pressure")
    assert entity.category == "VITAL"
    assert set(entity.codes) == {"8480-6", "8462-4", "8478-0"}

    entity = find(ner, "cholesterol in HDL please", "cholesterol in HDL")
    assert entity.codes == ["2085-9"]


def test_spans_point_back_at_the_raw_query(ner: QueryNER) -> None:
    query = "any drug allergies before I prescribe amoxicillin"
    for entity in entities(ner, query):
        assert query[entity.start : entity.end] == entity.span


# --------------------------------------------------------------- quality --


def test_exact_match(ner: QueryNER) -> None:
    assert find(ner, "history of pneumonia", "pneumonia").quality is MatchQuality.EXACT


def test_synonym_match(ner: QueryNER) -> None:
    entity = find(ner, "HTN and T2DM meds?", "HTN")
    assert entity.quality is MatchQuality.SYNONYM
    assert entity.codes == ["59621000"]


def test_fuzzy_is_last_resort(ner: QueryNER) -> None:
    entity = find(ner, "amoxicilin allergy", "amoxicilin")
    assert entity.quality is MatchQuality.FUZZY
    assert "308182" in entity.codes


def test_unresolved_beats_a_confident_wrong_code(ner: QueryNER) -> None:
    """Drug classes are not in the corpus vocabulary; say so."""
    for entity in entities(ner, "any beta blockers or ACE inhibitors on the list"):
        assert entity.quality is MatchQuality.UNRESOLVED
        assert entity.codes == []


def test_nonsense_does_not_fuzzy_match(ner: QueryNER) -> None:
    for entity in entities(ner, "flibbertigibbet and wockyjabber"):
        assert entity.quality is MatchQuality.UNRESOLVED


def test_adjacent_unresolved_tokens_merge(ner: QueryNER) -> None:
    assert "PICC line" in spans(ner, "does the patient have a PICC line")


# -------------------------------------------------------------- code sets --


def test_bare_analyte_returns_the_whole_code_set(ner: QueryNER) -> None:
    """A bare "glucose" must return all four so SQL can filter code IN (...)."""
    entity = find(ner, "glucose over the last year", "glucose")
    assert set(entity.codes) == {"2339-0", "2345-7", "25428-4", "5792-7"}


def test_one_entity_per_category_group(ner: QueryNER) -> None:
    """aspirin is an ALLERGY substance and a MEDICATION product; collapsing
    them would force a lie into `category` or into `codes`."""
    found = [e for e in entities(ner, "is he on aspirin") if e.span.lower() == "aspirin"]
    assert {e.category for e in found} == {"ALLERGY", "MEDICATION"}
    assert all(e.start == found[0].start for e in found)


def test_ingredient_rollup_through_the_query(ner: QueryNER) -> None:
    entity = find(ner, "any oxycodone in the last month", "oxycodone")
    assert len(entity.codes) >= 4


# ---------------------------------------------------------------- negation --


def test_negation_window(ner: QueryNER) -> None:
    assert find(ner, "any patient with no known allergies", "no known allergies").negated
    assert find(ner, "denies chest pain and wheezing", "wheezing").negated
    assert find(ner, "no history of MI", "MI").negated
    assert find(ner, "negative for COVID", "COVID").negated


def test_positive_mention_is_not_negated(ner: QueryNER) -> None:
    assert not find(ner, "allergic to penicillin", "penicillin").negated
    assert not find(ner, "started on warfarin last week", "warfarin").negated


def test_blocker_resets_the_window(ner: QueryNER) -> None:
    query = "no history of MI but has CKD"
    assert find(ner, query, "MI").negated
    assert not find(ner, query, "CKD").negated


def test_self_negating_form(ner: QueryNER) -> None:
    """NKDA carries its negation inside the span, not before it."""
    entity = find(ner, "NKDA?", "NKDA")
    assert entity.negated
    assert entity.codes == ["609328004"]


def test_negated_and_positive_allergy_do_not_resolve_identically(ner: QueryNER) -> None:
    negated = find(ner, "no known drug allergies", "no known drug allergies")
    positive = find(ner, "allergic to penicillin", "penicillin")
    assert negated.negated and not positive.negated


# -------------------------------------------------------------------- dates --


@pytest.mark.parametrize(
    ("query", "expected_from", "expected_to"),
    [
        ("glucose over the last year", date(2025, 8, 19), TODAY),
        ("hba1c trend over the last 2 years", date(2024, 8, 19), TODAY),
        ("creatinine in the past 3 months", date(2026, 5, 21), TODAY),
        ("K+ since 2019", date(2019, 1, 1), TODAY),
        ("BP readings in 2024", date(2024, 1, 1), date(2024, 12, 31)),
        ("LFT this year", date(2026, 1, 1), TODAY),
        ("recent hba1c", date(2026, 2, 20), TODAY),
    ],
)
def test_date_windows(ner: QueryNER, query, expected_from, expected_to) -> None:
    parsed = ner.parse(query, today=TODAY)
    assert parsed.date_from == expected_from
    assert parsed.date_to == expected_to


def test_before_surgery_is_an_upper_bound_only(ner: QueryNER) -> None:
    parsed = ner.parse("hb and hct before surgery", today=TODAY)
    assert parsed.date_from is None
    assert parsed.date_to == TODAY


def test_no_date_phrase_leaves_the_window_open(ner: QueryNER) -> None:
    parsed = ner.parse("does he have COPD or asthma", today=TODAY)
    assert parsed.date_from is None and parsed.date_to is None


def test_date_tokens_do_not_become_entities(ner: QueryNER) -> None:
    assert spans(ner, "glucose over the last year") == ["glucose"]


# ------------------------------------------------------------------- misc --


def test_punctuation_is_kept_where_it_carries_meaning(ner: QueryNER) -> None:
    assert find(ner, "what's the K+ trend", "K+").codes == ["2823-3", "6298-4"]


def test_empty_query(ner: QueryNER) -> None:
    parsed = ner.parse("", today=TODAY)
    assert parsed.entities == [] and parsed.date_from is None


def test_query_is_echoed_verbatim(ner: QueryNER) -> None:
    query = "  Any drug ALLERGIES?  "
    assert QueryNER(lexicon=ner.lexicon).parse(query, today=TODAY).raw == query
