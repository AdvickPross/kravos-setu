"""Tests for OCR evaluation and benchmarking metrics."""

from __future__ import annotations

import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from tests.ocr_benchmark.evaluator import (
    OCRBenchmarkEvaluator,
    OCRErrorCategory,
    calculate_category_metrics,
    calculate_cer,
    calculate_wer,
    categorize_ocr_discrepancy,
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


def test_categorize_discrepancy_taxonomy():
    # 1. Detection error: text in ground truth completely missed
    err_det = categorize_ocr_discrepancy("Dr. Smith", "")
    assert err_det.category == OCRErrorCategory.DETECTION_ERROR
    assert "RT-DETR" in err_det.subsystem

    # 2. Numeric error: dropped decimal point (13.2 -> 132)
    err_num = categorize_ocr_discrepancy("13.2 mg", "132 mg", {"is_numeric": True})
    assert err_num.category == OCRErrorCategory.NUMERIC_ERROR
    assert "decimal" in err_num.reason.lower()

    # 3. Handwriting error
    err_hw = categorize_ocr_discrepancy("Refill 1 mo", "Refill 7 mo", {"is_handwritten": True})
    assert err_hw.category == OCRErrorCategory.HANDWRITING_ERROR
    assert "TrOCR" in err_hw.subsystem

    # 4. Table error
    err_tbl = categorize_ocr_discrepancy("120/80", "120", {"is_table": True})
    assert err_tbl.category == OCRErrorCategory.TABLE_ERROR

    # 5. Layout error
    err_lay = categorize_ocr_discrepancy("Col1 Col2", "Col2 Col1", {"is_layout": True})
    assert err_lay.category == OCRErrorCategory.LAYOUT_ERROR

    # 6. Preprocessing error
    err_prep = categorize_ocr_discrepancy("Washed out", "Wsshd out", {"is_preprocessing": True})
    assert err_prep.category == OCRErrorCategory.PREPROCESSING_ERROR

    # 7. Normalization error
    err_norm = categorize_ocr_discrepancy("Paracetamol", "ParacetamoI", {"is_medication": True})
    assert err_norm.category == OCRErrorCategory.NORMALIZATION_ERROR

    # 8. Recognition error
    err_rec = categorize_ocr_discrepancy("Hospital", "Hospita1")
    assert err_rec.category == OCRErrorCategory.RECOGNITION_ERROR


def test_category_metrics_calculation():
    items = [
        {"reference": "Paracetamol 500mg", "hypothesis": "Paracetamol 500mg", "is_handwritten": False, "is_medication": True},
        {"reference": "Amoxicillin", "hypothesis": "Amoxicil1in", "is_handwritten": False, "is_medication": True},
        {"reference": "Dr. Note", "hypothesis": "Dr. Note", "is_handwritten": True},
        {"reference": "13.2", "hypothesis": "13.2", "is_numeric": True},
        {"reference": "500", "hypothesis": "50", "is_numeric": True},
        {"reference": "Cell1", "hypothesis": "Cell1", "is_table": True},
    ]
    metrics = calculate_category_metrics(items)
    assert "printed_cer" in metrics
    assert "handwriting_cer" in metrics
    assert "numeric_accuracy" in metrics
    assert "medication_accuracy" in metrics
    assert "table_accuracy" in metrics

    assert metrics["handwriting_cer"] == 0.0
    assert metrics["numeric_accuracy"] == 0.5  # 1 of 2 matches
    assert metrics["medication_accuracy"] == 0.5  # 1 of 2 matches
    assert metrics["table_accuracy"] == 1.0


def test_ocr_benchmark_evaluator_class():
    evaluator = OCRBenchmarkEvaluator()
    assert evaluator.compute_cer("abc", "abc") == 0.0
    assert evaluator.compute_wer("hello world", "hello world") == 0.0

    err = evaluator.categorize_error("13.2", "132", {"is_numeric": True})
    assert err.category == OCRErrorCategory.NUMERIC_ERROR

    gt = {"drug": "Metformin", "dose": "500mg"}
    hyp = {"drug": "Metformin", "dose": "50mg"}
    res = evaluator.evaluate_fields(gt, hyp)
    assert res.field_accuracy == 0.5
    assert res.error_breakdown[OCRErrorCategory.NUMERIC_ERROR.value] == 1

