"""Unit tests for OCR multi-candidate fusion engine."""

from __future__ import annotations

import pytest

from pipeline.fusion import OCRCandidate, OCRFusionEngine
from pipeline.schemas.canonical import BoundingBox


@pytest.fixture
def fusion_engine() -> OCRFusionEngine:
    return OCRFusionEngine()


def test_fuse_empty_candidates_returns_empty(fusion_engine: OCRFusionEngine):
    result = fusion_engine.fuse_candidates([])
    assert result.text == ""
    assert result.confidence == 0.0


def test_fuse_single_candidate(fusion_engine: OCRFusionEngine):
    cand = OCRCandidate(
        text="Paracetamol 500mg",
        confidence=0.92,
        model="ppocr",
        preprocessing="original",
    )
    result = fusion_engine.fuse_candidates([cand])
    assert result.text == "Paracetamol 500mg"
    assert result.confidence >= 0.80
    assert result.model == "ppocr"


def test_fuse_selects_higher_scoring_candidate(fusion_engine: OCRFusionEngine):
    # Candidate 1: low confidence noisy text
    c1 = OCRCandidate(
        text="Paracetamo1 5O0mg",
        confidence=0.45,
        model="ppocr",
        preprocessing="original",
    )
    # Candidate 2: high confidence clean medical text
    c2 = OCRCandidate(
        text="Paracetamol 500mg",
        confidence=0.88,
        model="ppocr",
        preprocessing="adaptive_low_quality",
    )
    result = fusion_engine.fuse_candidates([c1, c2])
    assert result.text == "Paracetamol 500mg"
    assert result.confidence > 0.80


def test_numeric_protection_in_fusion(fusion_engine: OCRFusionEngine):
    # Candidate 1 has decimal point dropped (e.g. 132 instead of 13.2)
    # but slightly higher raw model confidence
    c1 = OCRCandidate(
        text="Hemoglobin 132 g/dL",
        confidence=0.85,
        model="trocr",
        preprocessing="original",
    )
    # Candidate 2 has physiologically valid decimal point
    c2 = OCRCandidate(
        text="Hemoglobin 13.2 g/dL",
        confidence=0.80,
        model="trocr",
        preprocessing="adaptive_handwriting",
    )
    # Plausibility check favors c2 because 132 g/dL Hb is physiologically implausible
    res = fusion_engine.fuse_candidates([c1, c2])
    assert res.text == "Hemoglobin 13.2 g/dL"
