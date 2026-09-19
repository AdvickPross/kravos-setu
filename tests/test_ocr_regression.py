"""Regression testing: old RapidOCR path vs. new 5-model pipeline on demo scan."""

from __future__ import annotations

from pathlib import Path

import pytest

from ocr.engine import read_document
from ocr.extract import extract_facts
from pipeline.orchestrator import PipelineOrchestrator

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_SCAN = REPO_ROOT / "demo" / "scans" / "discharge_summary.png"

needs_scan = pytest.mark.skipif(not DEMO_SCAN.exists(), reason="run scripts/make_demo_scan.py")


@needs_scan
def test_regression_comparison_old_vs_new():
    """Verify that the new 5-model pipeline preserves and matches clinical facts extracted by the old pipeline."""
    # 1. Run Old Pipeline
    old_doc = read_document(DEMO_SCAN)
    old_report = extract_facts(old_doc, patient_id="test-patient")

    # 2. Run New Pipeline
    orchestrator = PipelineOrchestrator()
    new_result = orchestrator.process_document(DEMO_SCAN, patient_id="test-patient")

    # Verify both extracted lines
    assert len(old_doc.lines) > 0
    assert len(new_result.ocr_results) > 0

    # Verify key clinical headings preserved
    old_headings = set(old_report.sections_seen)
    new_headings = set(new_result.sections_seen)
    for expected_section in ["ALLERGY", "MEDICATION", "CONDITION"]:
        assert expected_section in new_headings, f"New pipeline missed section: {expected_section}"

    # Verify key extracted facts exist in new pipeline
    new_codes = {f.code for f in new_result.entities if f.code}
    old_codes = {f.code for f in old_report.facts if f.code}

    # High-impact clinical facts (Penicillin, Metformin, etc.)
    common_codes = new_codes.intersection(old_codes)
    assert len(common_codes) >= 5, f"Expected at least 5 matching clinical codes, got {len(common_codes)}"

    # Check that confidence propagation remained valid
    for entity in new_result.entities:
        assert 0.0 < entity.confidence <= 1.0
        assert entity.provenance.document_id == "discharge_summary.png"
        assert entity.provenance.pipeline_version == "2.0.0"

    print(
        f"\nRegression check passed: Old extracted {len(old_report.facts)} facts, "
        f"New extracted {len(new_result.entities)} facts. Overlap: {len(common_codes)} codes."
    )
