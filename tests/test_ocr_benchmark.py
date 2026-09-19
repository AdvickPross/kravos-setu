"""Tests for OCR evaluation and benchmarking metrics."""

from __future__ import annotations

import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from tests.ocr_benchmark.evaluator import (
    calculate_cer,
    calculate_wer,
    evaluate_structured_fields,
    levenshtein_distance,
)


def test_levenshtein_distance():
    assert levenshtein_distance("kitten", "sitting") == 3
    assert levenshtein_distance("", "") == 0
    assert levenshtein_distance("abc", "") == 3
    assert levenshtein_distance("", "abc") == 3
    assert levenshtein_distance("same", "same") == 0


def test_cer_calculation():
    # Exactly matching
    assert calculate_cer("Amoxicillin", "Amoxicillin") == 0.0
    # 1 substitution out of 11 characters
    cer = calculate_cer("Amoxicillin", "Amoxicil1in")
    assert round(cer, 4) == round(1 / 11, 4)
    # Empty reference
    assert calculate_cer("", "") == 0.0
    assert calculate_cer("", "abc") == 1.0


def test_wer_calculation():
    ref = "Take Tab Pan 40mg before food"
    hyp = "Take Tab Pan 40mg after food"
    # 1 word changed out of 6
    assert round(calculate_wer(ref, hyp), 4) == round(1 / 6, 4)
    assert calculate_wer(ref, ref) == 0.0


def test_field_accuracy_evaluation():
    gt = {
        "doctor": "Dr. N. Sanjira",
        "hospital_number": "6901207",
        "document_type": "prescription",
    }
    extracted = {
        "doctor": "dr. n. sanjira",  # Case-insensitive match
        "hospital_number": "6901207",
        "document_type": "prescription",
    }
    eval_res = evaluate_structured_fields(gt, extracted)
    assert eval_res.field_accuracy == 1.0
    assert eval_res.summary["matched_fields"] == 3
