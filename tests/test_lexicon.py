"""Index-construction tests, and the guarantee that synonyms.csv is honest."""

from __future__ import annotations

import csv

import pytest

from retrieval.lexicon import DEFAULT_LEXICON_PATH, DEFAULT_SYNONYMS_PATH, Lexicon
from retrieval.models import MatchQuality


@pytest.fixture(scope="module")
def lexicon() -> Lexicon:
    return Lexicon()


def test_every_synonym_code_exists_in_the_lexicon(lexicon: Lexicon) -> None:
    """The seed list was mined from lexicon.csv, not recalled from memory.

    This is the check that keeps it that way: when the corpus is regenerated
    and a code disappears, this fails instead of silently dropping a concept.
    """
    assert lexicon.unknown_synonym_codes == []


def test_synonym_rows_are_concept_shaped() -> None:
    """concept, surface_forms, category, code_system, codes -- pipe-delimited,
    so one concept can accumulate codes across systems later."""
    with DEFAULT_SYNONYMS_PATH.open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if not r["concept"].startswith("#")]
    assert rows
    for row in rows:
        assert row["concept"].strip()
        assert row["surface_forms"].strip()
        assert row["codes"].strip()
        assert row["code_system"].strip().startswith("http")
        assert "," not in row["surface_forms"], "surface forms are pipe-delimited"


def test_multi_code_forms_are_lists(lexicon: Lexicon) -> None:
    """Measured, not defensive: these analytes really do span several codes."""
    expected = {
        "glucose": 4,
        "bilirubin.total": 4,
        "creatinine": 2,
        "potassium": 2,
        "sodium": 2,
        "hemoglobin": 2,
    }
    for form, count in expected.items():
        assert len({h.entry.code for h in lexicon.lookup(form)}) == count, form


def test_medication_rollup(lexicon: Lexicon) -> None:
    """Querying an ingredient hits every product containing it."""
    codes = {h.entry.code for h in lexicon.lookup("oxycodone")}
    assert len(codes) >= 4
    assert {h.entry.code for h in lexicon.lookup("percocet")} <= codes


def test_form_can_span_categories(lexicon: Lexicon) -> None:
    """aspirin is an ALLERGY substance and a MEDICATION product."""
    categories = {h.entry.category for h in lexicon.lookup("aspirin")}
    assert categories == {"ALLERGY", "MEDICATION"}


def test_no_duplicate_codes_within_a_form(lexicon: Lexicon) -> None:
    """Synthea emits some products under two casings with one code."""
    for form in ("aspirin", "simvastatin", "paclitaxel", "warfarin"):
        hits = lexicon.lookup(form)
        identities = [(h.entry.category, h.entry.code_system, h.entry.code) for h in hits]
        assert len(identities) == len(set(identities)), form


def test_quality_is_tagged_by_source(lexicon: Lexicon) -> None:
    assert all(h.quality is MatchQuality.EXACT for h in lexicon.lookup("pneumonia"))
    assert all(h.quality is MatchQuality.SYNONYM for h in lexicon.lookup("htn"))


def test_stoplisted_form_is_not_indexed(lexicon: Lexicon) -> None:
    assert lexicon.lookup("total score") == []
    # the full display still is, keyed through normalize_query_text
    assert lexicon.lookup("total score mmse")


def test_display_collisions_are_reported_not_swallowed(lexicon: Lexicon) -> None:
    """Two entries normalizing identically with *different* codes is a genuine
    finding, and the diagnostic must actually surface it.

    This previously asserted the list was empty, on the belief that no
    collisions existed. They did -- ~98 of them -- and the diagnostic was
    keyed on `normalized.full`, which two rows share only if their raw
    displays are byte-identical, which never happens. So the check reported
    zero forever and the test passed by agreeing with the blind spot rather
    than by the corpus being clean.
    """
    assert lexicon.duplicate_displays, "collision diagnostic reported nothing -- it is blind again"


def test_cross_specimen_collisions_are_visible(lexicon: Lexicon) -> None:
    """The collisions that matter clinically: one word reaching a blood
    analyte and a urine dipstick, because normalize_loinc() strips the
    specimen phrase from both displays.

    Not asserted as *wrong* -- multi-code resolution is deliberate here, and
    `data/synonyms.csv` carries curated blood-only concepts for the ones that
    matter. Asserted as *visible*, so the trade-off stays a decision someone
    made rather than one nobody could see.
    """
    hemoglobin_codes = {h.entry.code for h in lexicon.lookup("hemoglobin")}
    assert {"718-7", "5794-3"} <= hemoglobin_codes

    reported = "\n".join(lexicon.duplicate_displays)
    for form in ("hemoglobin", "leukocytes", "glucose"):
        assert f"'{form}' normalizes from" in reported


def test_paths_are_constructor_arguments(tmp_path) -> None:
    """The lexicon is regenerated whenever the corpus changes; nothing may
    hardcode its location."""
    path = tmp_path / "lex.csv"
    path.write_text(
        "category,code_system,code,display\n"
        "CONDITION,http://snomed.info/sct,123,Widget disease (disorder)\n",
        encoding="utf-8",
    )
    small = Lexicon(lexicon_path=path, synonyms_path=None)
    assert small.stats()["lexicon_rows"] == 1
    assert [h.entry.code for h in small.lookup("widget disease")] == ["123"]
    assert small.lookup("pneumonia") == []


def test_reload_picks_up_changes(tmp_path) -> None:
    path = tmp_path / "lex.csv"
    header = "category,code_system,code,display\n"
    path.write_text(header + "CONDITION,http://snomed.info/sct,1,Alpha (disorder)\n", encoding="utf-8")
    lex = Lexicon(lexicon_path=path, synonyms_path=None)
    assert lex.lookup("beta") == []

    path.write_text(header + "CONDITION,http://snomed.info/sct,2,Beta (disorder)\n", encoding="utf-8")
    lex.reload()
    assert [h.entry.code for h in lex.lookup("beta")] == ["2"]
    assert lex.lookup("alpha") == []


def test_lexicon_file_is_the_generated_shape() -> None:
    with DEFAULT_LEXICON_PATH.open(encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle))
    assert header == ["category", "code_system", "code", "display"]
