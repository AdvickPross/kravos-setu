"""Unit tests for the medical OCR correction and numeric validation layer."""

from __future__ import annotations

import pytest
from ocr.medical_corrector import (
    MedicalOCRCorrector,
    correct_line,
    validate_numerics,
    MedicalCorrectionResult,
    NumericValidationResult,
)


class TestMedicalOCRCorrector:
    def test_no_correction_on_clean_text(self):
        result = correct_line("Patient Name: John Doe")
        # Should not crash, may or may not modify
        assert isinstance(result, MedicalCorrectionResult)
        assert result.raw_ocr_text == "Patient Name: John Doe"

    def test_direct_replacement_paracetamol(self):
        result = correct_line("ParacetamoI 500 mg")
        assert "Paracetamol" in result.corrected_text
        assert result.was_modified
        assert result.raw_ocr_text == "ParacetamoI 500 mg"  # original preserved

    def test_unit_normalization_mg_dl(self):
        result = correct_line("Glucose: 95 mg/dl")
        assert "mg/dL" in result.corrected_text
        assert result.raw_ocr_text == "Glucose: 95 mg/dl"

    def test_unit_normalization_mg_with_spaces(self):
        result = correct_line("HbA1c: 6.5 mg / dL")
        assert "mg/dL" in result.corrected_text

    def test_hba1c_correction(self):
        result = correct_line("HbA1 c: 6.5")
        assert "HbA1c" in result.corrected_text

    def test_empty_string_does_not_crash(self):
        result = correct_line("")
        assert result.raw_ocr_text == ""
        assert result.corrected_text == ""
        assert not result.was_modified

    def test_correction_records_are_traceable(self):
        corrector = MedicalOCRCorrector()
        result = corrector.correct_line("ParacetamoI 500mg")
        if result.was_modified:
            for rec in result.corrections_applied:
                assert rec.original
                assert rec.corrected
                assert rec.rule

    def test_batch_correction(self):
        corrector = MedicalOCRCorrector()
        lines = ["ParacetamoI 500mg", "Metf0rmin 1000mg", "Ibupr0fen 400mg"]
        results = corrector.correct_batch(lines)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, MedicalCorrectionResult)

    def test_extra_corrections_parameter(self):
        corrector = MedicalOCRCorrector(extra_corrections={"Zerod0l": "Zerodol"})
        result = corrector.correct_line("T.Zerod0l P")
        assert "Zerodol" in result.corrected_text


class TestNumericValidation:
    def test_valid_hemoglobin(self):
        results = validate_numerics("13.2 g/dL", context_label="hemoglobin")
        nums = [r for r in results if r.parsed_value is not None]
        assert len(nums) >= 1
        assert all(r.validation == "valid" for r in nums)

    def test_suspicious_hemoglobin_transposition(self):
        # 132 instead of 13.2
        results = validate_numerics("132 g/dL", context_label="hemoglobin")
        nums = [r for r in results if r.parsed_value is not None]
        assert any(r.validation == "suspicious" for r in nums), (
            f"Expected suspicious for 132, got: {[r.validation for r in nums]}"
        )

    def test_valid_glucose(self):
        results = validate_numerics("95 mg/dL", context_label="glucose")
        nums = [r for r in results if r.parsed_value is not None]
        assert all(r.validation == "valid" for r in nums)

    def test_suspicious_glucose(self):
        results = validate_numerics("9500 mg/dL", context_label="glucose")
        nums = [r for r in results if r.parsed_value is not None]
        assert any(r.validation == "suspicious" for r in nums)

    def test_valid_dosage(self):
        results = validate_numerics("500 mg")
        nums = [r for r in results if r.parsed_value is not None]
        assert all(r.validation == "valid" for r in nums)

    def test_suspicious_dosage_extreme(self):
        results = validate_numerics("500000 mg")
        nums = [r for r in results if r.parsed_value is not None]
        assert any(r.validation == "suspicious" for r in nums)

    def test_no_number_returns_no_number_result(self):
        results = validate_numerics("Paracetamol tablet")
        assert any(r.validation == "no_number" for r in results)

    def test_unknown_context_label_valid_dosage(self):
        # Without context, dosage range applies
        results = validate_numerics("500 mg", context_label="unknown_drug")
        nums = [r for r in results if r.parsed_value is not None]
        # 500mg is within dosage range — should be valid
        assert all(r.validation == "valid" for r in nums)

    def test_numeric_validation_result_structure(self):
        results = validate_numerics("Hemoglobin: 13.2 g/dL", context_label="hemoglobin")
        for r in results:
            if r.parsed_value is not None:
                assert r.raw_value
                assert isinstance(r.validation, str)
                assert r.validation in ("valid", "suspicious", "unrecognized", "no_number")

    def test_detect_suspicious_numeric_mutation(self):
        warnings = MedicalOCRCorrector.detect_suspicious_numeric_mutation(
            "Hemoglobin 13.2 g/dL", "Hemoglobin 132 g/dL"
        )
        assert len(warnings) == 1
        assert "decimal point dropped" in warnings[0]

        # No mutation on identical text
        clean = MedicalOCRCorrector.detect_suspicious_numeric_mutation(
            "Hemoglobin 13.2 g/dL", "Hemoglobin 13.2 g/dL"
        )
        assert len(clean) == 0

