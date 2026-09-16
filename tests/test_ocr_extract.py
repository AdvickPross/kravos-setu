"""OCR text -> coded facts.

Built on hand-written `OcrLine`s rather than on a real image, so these run in
milliseconds and a failure points at the extraction rule that broke rather
than at the recognition model having a bad day. `test_ocr_engine.py` covers
the pixels.

The safety rules are the reason this file exists. A scan is a lossy source
and the extractor's job is to refuse the guesses it cannot justify -- so most
of what is asserted here is what does *not* become a fact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ocr.engine import OcrDocument, OcrLine
from ocr.extract import clean_ocr_text, extract_facts

PATIENT = "patient-1"
WHEN = datetime(2026, 3, 9, tzinfo=timezone.utc)


def doc(*lines: str | tuple[str, float]) -> OcrDocument:
    parsed = []
    for index, entry in enumerate(lines, start=1):
        text, confidence = entry if isinstance(entry, tuple) else (entry, 0.95)
        parsed.append(
            OcrLine(text=text, confidence=confidence, page=1, line_no=index)
        )
    return OcrDocument(source=Path("scan.png"), lines=parsed, engine="test")


def run(*lines):
    return extract_facts(doc(*lines), PATIENT, document_date=WHEN)


# --------------------------------------------------------------------------
# the safety rule: a section decides what a drug name means
# --------------------------------------------------------------------------


def test_the_same_drug_name_means_opposite_things_in_two_sections():
    """The rule the whole module is built around.

    "Aspirin" under ALLERGIES is a drug the patient must never receive.
    Under MEDICATIONS it is one they take daily. Nothing in the token says
    which, and getting it backwards inverts the clinical meaning.
    """
    allergy = run("ALLERGIES:", "Aspirin")
    medication = run("MEDICATIONS:", "Aspirin")

    assert [f.category for f in allergy.facts] == ["ALLERGY"]
    assert [f.category for f in medication.facts] == ["MEDICATION"]
    assert allergy.facts[0].safety_class == "SAFETY"


def test_a_drug_outside_any_section_is_reported_not_guessed():
    """With no heading there is no basis to choose, so no fact is emitted --
    and the line is surfaced rather than dropped silently."""
    report = run("Aspirin")
    assert report.facts == []
    assert [line.text for line in report.ambiguous] == ["Aspirin"]


def test_a_concept_of_the_wrong_kind_for_its_section_is_also_refused():
    """A lab name under MEDICATIONS is someone's formatting accident, not a
    prescription."""
    report = run("MEDICATIONS:", "Hemoglobin")
    assert report.facts == []
    assert report.ambiguous


# --------------------------------------------------------------------------
# OCR noise
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Essential.hypertension", "Essential hypertension"),
        ("Type .2: diabetes. mellitus", "Type 2 diabetes mellitus"),
        ("Atorvastatin20mg", "Atorvastatin 20mg"),
        ("Metformin' 500'mg", "Metformin 500 mg"),
        ("DISCHARGE.MEDICATIONS", "DISCHARGE MEDICATIONS"),
    ],
)
def test_scanner_punctuation_is_cleaned_before_lookup(raw: str, expected: str):
    """Real output from the demo scan. An exact-form index matches none of
    these as written, though a human reads every one of them."""
    assert clean_ocr_text(raw) == expected


def test_a_heading_survives_the_dot_ocr_puts_in_it():
    """The demo scan reads "DISCHARGE.MEDICATIONS:". A stricter pattern loses
    the heading, and then every drug under it falls out as ambiguous -- the
    whole medication block silently missing."""
    report = run("DISCHARGE.MEDICATIONS:", "Furosemide 40 mg")
    assert "MEDICATION" in report.sections_seen
    assert [f.category for f in report.facts] == ["MEDICATION"]


def test_several_items_on_one_line_are_split():
    report = run("MEDICATIONS:", "Furosemide 40 mg, Amlodipine 5 mg")
    assert len(report.facts) == 2


def test_dose_is_recorded_when_present_and_never_invented():
    report = run("MEDICATIONS:", "Furosemide 40 mg", "Amlodipine")
    doses = {(f.value, f.unit) for f in report.facts}
    assert ("40", "mg") in doses
    assert (None, None) in doses


# --------------------------------------------------------------------------
# confidence
# --------------------------------------------------------------------------


def test_a_low_confidence_line_never_becomes_a_fact():
    """A misread drug name is exactly the error this project exists to
    surface rather than launder into the corpus."""
    report = run("ALLERGIES:", ("Penicillin", 0.30))
    assert report.facts == []
    assert [line.text for line in report.low_confidence] == ["Penicillin"]


def test_confidence_reaches_the_fact():
    report = run("ALLERGIES:", ("Penicillin", 0.78))
    assert report.facts[0].confidence == pytest.approx(0.78)
    assert "78%" in report.facts[0].text


# --------------------------------------------------------------------------
# code selection
# --------------------------------------------------------------------------


def test_a_blood_analyte_does_not_resolve_to_the_urine_dipstick_code():
    """The collision the README documents as intended on the *query* side.

    Reading is one thing -- a human shown both the serum result and the urine
    dipstick can tell them apart. Writing is not: a fact gets one code, and
    the first-hit-wins version of this picked "Hemoglobin [Presence] in Urine
    by Test strip" for a line that said "Hemoglobin" under INVESTIGATIONS,
    putting a wrong code into the corpus for every later query to inherit.
    """
    report = run("INVESTIGATIONS:", "Hemoglobin")
    assert report.facts, "hemoglobin should resolve under a LAB heading"
    display = report.facts[0].display or ""
    assert "Urine" not in display and "strip" not in display
    assert "Blood" in display


def test_rejected_codes_are_kept_rather_than_discarded():
    """A reviewer judging whether the extraction was right needs the
    alternatives it turned down."""
    report = run("INVESTIGATIONS:", "Hemoglobin")
    assert report.facts[0].also_in, "the urine code should be recorded, not lost"


# --------------------------------------------------------------------------
# provenance and the row contract
# --------------------------------------------------------------------------


def test_every_fact_cites_its_page_and_line():
    report = run("ALLERGIES:", "Penicillin")
    fact = report.facts[0]
    assert fact.source_resource_id == "scan.png#p1L2"
    assert fact.line_text == "Penicillin"


def test_the_row_matches_the_corpus_schema():
    from backend.models import Fact

    report = run("ALLERGIES:", "Penicillin")
    row = report.facts[0].as_fact_row()
    assert set(row) == set(Fact.model_fields)
    assert row["source_resource_type"] == "ScannedDocument"
    Fact.model_validate(row)


def test_re_ingesting_the_same_scan_produces_the_same_ids():
    """Fact ids key on file, page, line and code, so a second run of an
    unchanged document is a no-op rather than a duplicate."""
    first = run("ALLERGIES:", "Penicillin")
    second = run("ALLERGIES:", "Penicillin")
    assert [f.fact_id for f in first.facts] == [f.fact_id for f in second.facts]


def test_two_scans_sharing_a_line_stay_distinct():
    a = extract_facts(doc("ALLERGIES:", "Penicillin"), PATIENT, document_date=WHEN)
    b_doc = OcrDocument(
        source=Path("other.png"), lines=doc("ALLERGIES:", "Penicillin").lines, engine="test"
    )
    b = extract_facts(b_doc, PATIENT, document_date=WHEN)
    assert a.facts[0].fact_id != b.facts[0].fact_id


def test_boilerplate_lines_are_ignored():
    report = run("ALLERGIES:", "Penicillin", "Page 1", "--------", "Signature")
    assert len(report.facts) == 1


def test_the_report_names_what_it_could_not_extract():
    """A scan yielding eight facts while silently dropping four drug names is
    worse than one that yields eight and says so."""
    report = run("ALLERGIES:", "Penicillin", "Ward 4B Bed 12")
    assert report.unmatched
    assert "matched no concept" in report.summary()


def test_the_same_scan_for_two_patients_produces_different_facts():
    """The bug that made a second patient's upload silently do nothing.

    Fact ids did not include the patient, so uploading one discharge summary
    for patient B produced ids already written for patient A. `ON CONFLICT DO
    NOTHING` swallowed every row and the API reported "inserted 0 of 12" while
    B's chart stayed empty. A shared document is not exotic -- a family brings
    in one folder, the same template is scanned twice -- and the failure is
    silent on exactly the side that matters.
    """
    a = extract_facts(doc("ALLERGIES:", "Penicillin"), "patient-a", document_date=WHEN)
    b = extract_facts(doc("ALLERGIES:", "Penicillin"), "patient-b", document_date=WHEN)

    assert a.facts[0].fact_id != b.facts[0].fact_id
    assert a.facts[0].patient_id == "patient-a"
    assert b.facts[0].patient_id == "patient-b"
