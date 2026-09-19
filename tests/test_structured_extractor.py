"""Unit tests for StructuredExtractor."""

from __future__ import annotations

import pytest

from pipeline.schemas.canonical import BoundingBox, OCRResult
from pipeline.structured_extractor import FieldPresence, StructuredExtractor


def _make_ocr(text: str, conf: float = 0.9, line_no: int = 1, bbox: tuple[float, float, float, float] | None = None) -> OCRResult:
    b = BoundingBox(*bbox) if bbox else BoundingBox(0.0, float(line_no * 20), 200.0, float(line_no * 20 + 18))
    return OCRResult(
        text=text,
        confidence=conf,
        bbox=b,
        page=1,
        line_no=line_no,
        region_id=f"r_{line_no}",
        model="trocr",
        model_version="1.0",
        is_handwritten=False,
        is_reliable=True,
    )


class TestStructuredExtractor:
    @pytest.fixture
    def extractor(self) -> StructuredExtractor:
        return StructuredExtractor()

    def test_extract_common_fields(self, extractor: StructuredExtractor) -> None:
        lines = [
            _make_ocr("CITY HOSPITAL MEDICAL CENTER", line_no=1),
            _make_ocr("Patient Name: Mr. Ramesh Gupta", line_no=2),
            _make_ocr("Age/Sex: 45/M", line_no=3),
            _make_ocr("Hosp. No: IP-992384", line_no=4),
            _make_ocr("Dr. Sunita Sharma, MD", line_no=5),
            _make_ocr("Dept: CARDIOLOGY", line_no=6),
            _make_ocr("Date: 15/04/2026", line_no=7),
        ]
        doc = extractor.extract(lines, document_type="unknown")

        assert doc.patient_name.presence == FieldPresence.PRESENT
        assert doc.patient_name.value == "Mr. Ramesh Gupta"
        assert doc.patient_age.value == "45"
        assert doc.patient_sex.value == "M"
        assert doc.hospital_number.value == "IP-992384"
        assert doc.doctor_name.value == "Dr. Sunita Sharma"
        assert doc.institution.value == "CITY HOSPITAL MEDICAL CENTER"
        assert doc.department.value == "CARDIOLOGY"
        assert doc.document_date.value == "15/04/2026"

    def test_extract_prescription_medications(self, extractor: StructuredExtractor) -> None:
        lines = [
            _make_ocr("Rx Prescription", line_no=1),
            _make_ocr("Tab. Amoxicillin 500 mg", line_no=2),
            _make_ocr("Take 1 tablet after food x 5 days", line_no=3),
            _make_ocr("Cap. Omeprazole 20 mg", line_no=4),
            _make_ocr("1/2 hr before food for 7 days", line_no=5),
        ]
        doc = extractor.extract(lines, document_type="prescription")

        assert len(doc.medications) >= 2
        med_names = [m.name.value for m in doc.medications]
        assert any("Amoxicillin" in name for name in med_names)
        assert any("Omeprazole" in name for name in med_names)

        dosages = [m.dosage.value for m in doc.medications]
        assert any("500 mg" in (d or "") for d in dosages)
        assert any("20 mg" in (d or "") for d in dosages)

    def test_extract_lab_report_table_rows(self, extractor: StructuredExtractor) -> None:
        # Simulate multi-column layout on same horizontal rows
        lines = [
            _make_ocr("LABORATORY INVESTIGATION REPORT", line_no=1, bbox=(50.0, 50.0, 500.0, 80.0)),
            # Row 1: Hemoglobin
            _make_ocr("Hemoglobin", line_no=2, bbox=(50.0, 120.0, 200.0, 140.0)),
            _make_ocr("14.5", line_no=3, bbox=(250.0, 120.0, 300.0, 140.0)),
            _make_ocr("g/dL", line_no=4, bbox=(350.0, 120.0, 420.0, 140.0)),
            _make_ocr("13.0 - 17.0", line_no=5, bbox=(480.0, 120.0, 600.0, 140.0)),
            # Row 2: Fasting Blood Sugar
            _make_ocr("Fasting Blood Sugar", line_no=6, bbox=(50.0, 160.0, 220.0, 180.0)),
            _make_ocr("98", line_no=7, bbox=(250.0, 160.0, 290.0, 180.0)),
            _make_ocr("mg/dL", line_no=8, bbox=(350.0, 160.0, 420.0, 180.0)),
            _make_ocr("70 - 100", line_no=9, bbox=(480.0, 160.0, 580.0, 180.0)),
        ]
        doc = extractor.extract(lines, document_type="lab_report")

        assert len(doc.lab_tests) == 2
        hb = doc.lab_tests[0]
        assert hb.test_name.value == "Hemoglobin"
        assert hb.result.value == "14.5"
        assert hb.unit.value == "g/dL"
        assert hb.reference_range.value == "13.0 - 17.0"

        fbs = doc.lab_tests[1]
        assert fbs.test_name.value == "Fasting Blood Sugar"
        assert fbs.result.value == "98"

    def test_extract_discharge_summary(self, extractor: StructuredExtractor) -> None:
        lines = [
            _make_ocr("DISCHARGE SUMMARY", line_no=1),
            _make_ocr("Final Diagnosis: Acute Myocardial Infarction", line_no=2),
            _make_ocr("Follow up: Review after 2 weeks with ECG", line_no=3),
        ]
        doc = extractor.extract(lines, document_type="discharge_summary")

        assert doc.diagnosis.presence == FieldPresence.PRESENT
        assert "Acute Myocardial Infarction" in (doc.diagnosis.value or "")
        assert doc.follow_up.presence == FieldPresence.PRESENT
        assert "Review after 2 weeks" in (doc.follow_up.value or "")

    def test_to_dict_serialization(self, extractor: StructuredExtractor) -> None:
        lines = [_make_ocr("Date: 10/10/2025", line_no=1)]
        doc = extractor.extract(lines, document_type="prescription")
        data = doc.to_dict()

        assert data["document_type"] == "prescription"
        assert "patient" in data
        assert "medications" in data
        assert "lab_tests" in data
        assert data["date"]["value"] == "10/10/2025"
        assert data["date"]["presence"] == "PRESENT"
