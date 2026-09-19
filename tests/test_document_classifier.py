"""Unit tests for the heuristic medical document classifier."""

from __future__ import annotations

import pytest

from pipeline.document_classifier import DocumentClassifier
from pipeline.schemas.canonical import BoundingBox, OCRResult


def _make_ocr_result(text: str, conf: float = 0.9, line_no: int = 1) -> OCRResult:
    return OCRResult(
        text=text,
        confidence=conf,
        bbox=BoundingBox(0.0, 0.0, 100.0, 20.0),
        page=1,
        line_no=line_no,
        region_id=f"r_{line_no}",
        model="trocr",
        model_version="1.0",
        is_handwritten=False,
        is_reliable=True,
    )


class TestDocumentClassifier:
    @pytest.fixture
    def classifier(self) -> DocumentClassifier:
        return DocumentClassifier()

    def test_empty_results_returns_unknown(self, classifier: DocumentClassifier) -> None:
        result = classifier.classify([])
        assert result.document_type == "unknown"
        assert result.confidence == 0.0

    def test_whitespace_only_returns_unknown(self, classifier: DocumentClassifier) -> None:
        lines = [_make_ocr_result("   "), _make_ocr_result("\n\t")]
        result = classifier.classify(lines)
        assert result.document_type == "unknown"

    def test_classify_prescription(self, classifier: DocumentClassifier) -> None:
        lines = [
            _make_ocr_result("Rx Prescription"),
            _make_ocr_result("Tab. Amoxicillin 500 mg 1-0-1"),
            _make_ocr_result("Cap. Omeprazole 20 mg before food"),
            _make_ocr_result("Take for 5 days morning and night"),
        ]
        result = classifier.classify(lines)
        assert result.document_type == "prescription"
        assert result.confidence >= 0.5
        assert "rx" in result.signals or "prescription" in result.signals

    def test_classify_lab_report(self, classifier: DocumentClassifier) -> None:
        lines = [
            _make_ocr_result("LABORATORY INVESTIGATION REPORT"),
            _make_ocr_result("Haematology & Biochemistry Pathology"),
            _make_ocr_result("Hemoglobin 13.2 g/dL (Reference range: 12.0 - 15.5)"),
            _make_ocr_result("Fasting Blood Glucose 112 mg/dL (Normal range: 70 - 100)"),
            _make_ocr_result("Serum Creatinine 0.9 mg/dL"),
        ]
        result = classifier.classify(lines)
        assert result.document_type == "lab_report"
        assert result.confidence >= 0.5

    def test_classify_discharge_summary(self, classifier: DocumentClassifier) -> None:
        lines = [
            _make_ocr_result("DISCHARGE SUMMARY"),
            _make_ocr_result("Date of Admission: 12-01-2026"),
            _make_ocr_result("Date of Discharge: 16-01-2026"),
            _make_ocr_result("Chief Complaint: Acute chest pain"),
            _make_ocr_result("Diagnosis: Unstable Angina"),
            _make_ocr_result("Treatment given and Advice on discharge: Follow up in 1 week"),
        ]
        result = classifier.classify(lines)
        assert result.document_type == "discharge_summary"
        assert result.confidence >= 0.5

    def test_classify_medical_bill(self, classifier: DocumentClassifier) -> None:
        lines = [
            _make_ocr_result("INVOICE / MEDICAL BILL"),
            _make_ocr_result("Consultation fee: Rs. 800"),
            _make_ocr_result("Pharmacy charges: Rs. 1250"),
            _make_ocr_result("Room charges: Rs. 3500"),
            _make_ocr_result("GST Total Amount Payable: INR 5550"),
        ]
        result = classifier.classify(lines)
        assert result.document_type == "medical_bill"
        assert result.confidence >= 0.5

    def test_classify_insurance_form(self, classifier: DocumentClassifier) -> None:
        lines = [
            _make_ocr_result("HEALTH INSURANCE CLAIM FORM"),
            _make_ocr_result("Policy Number: POL-9988123"),
            _make_ocr_result("TPA Pre-authorization Cashless Request"),
            _make_ocr_result("Treating Doctor & Hospital Registration"),
            _make_ocr_result("ICD-10 Diagnosis Code: I20.0"),
        ]
        result = classifier.classify(lines)
        assert result.document_type == "insurance_form"
        assert result.confidence >= 0.5

    def test_low_confidence_below_threshold_falls_back_to_unknown(self, classifier: DocumentClassifier) -> None:
        # A single generic ambiguous word without enough corroboration
        lines = [_make_ocr_result("Hello World Doctor Office")]
        result = classifier.classify(lines, min_confidence=0.8)
        assert result.document_type == "unknown"
