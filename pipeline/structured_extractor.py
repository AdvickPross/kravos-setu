"""Structured information extraction from OCR results for medical documents.

This module post-processes OCR results and quality-checked text to extract
structured fields relevant to common medical document types.

The output is NOT a replacement for the existing ClinicalEntity/fact pipeline
(which handles coded vocabulary lookup). This is a complementary layer that
extracts document-level metadata and free-text fields that the lexicon-based
approach misses.

Supported document types:
- prescription: patient info, doctor, medications, schedules
- lab_report: patient info, test rows (name/result/unit/reference)
- discharge_summary: patient info, diagnosis, medications, follow-up
- unknown: best-effort extraction

Design constraints:
- Conservative: never invent content. If a field is not found, mark it.
- Traceable: every extracted field carries the source OCR text and confidence.
- Medical safety: raw_text is preserved alongside normalized_text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pipeline.schemas.canonical import OCRResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Field Presence States
# ---------------------------------------------------------------------------

class FieldPresence:
    PRESENT = "PRESENT"
    NOT_FOUND = "NOT_FOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNCERTAIN = "UNCERTAIN"


@dataclass
class ExtractedField:
    """One extracted field with full traceability."""
    value: str | None
    confidence: float
    presence: str  # FieldPresence constant
    raw_ocr_text: str | None = None  # The line that produced this field
    source_line_no: int | None = None
    normalized: bool = False  # True if value was normalized (e.g., unit casing)


@dataclass
class MedicationEntry:
    """One medication entry from a prescription."""
    name: ExtractedField
    dosage: ExtractedField
    morning: ExtractedField
    noon: ExtractedField
    evening: ExtractedField
    night: ExtractedField
    frequency_note: ExtractedField  # e.g., "½ hr before food"
    duration: ExtractedField  # e.g., "3 days"


@dataclass
class LabTestEntry:
    """One row from a lab report table."""
    test_name: ExtractedField
    result: ExtractedField
    unit: ExtractedField
    reference_range: ExtractedField


@dataclass
class StructuredDocument:
    """Complete structured extraction output for a medical document."""
    document_type: str
    classification_confidence: float

    # Common patient fields
    patient_name: ExtractedField = field(default_factory=lambda: ExtractedField(
        value=None, confidence=0.0, presence=FieldPresence.NOT_FOUND
    ))
    patient_age: ExtractedField = field(default_factory=lambda: ExtractedField(
        value=None, confidence=0.0, presence=FieldPresence.NOT_FOUND
    ))
    patient_sex: ExtractedField = field(default_factory=lambda: ExtractedField(
        value=None, confidence=0.0, presence=FieldPresence.NOT_FOUND
    ))
    hospital_number: ExtractedField = field(default_factory=lambda: ExtractedField(
        value=None, confidence=0.0, presence=FieldPresence.NOT_FOUND
    ))
    doctor_name: ExtractedField = field(default_factory=lambda: ExtractedField(
        value=None, confidence=0.0, presence=FieldPresence.NOT_FOUND
    ))
    document_date: ExtractedField = field(default_factory=lambda: ExtractedField(
        value=None, confidence=0.0, presence=FieldPresence.NOT_FOUND
    ))
    department: ExtractedField = field(default_factory=lambda: ExtractedField(
        value=None, confidence=0.0, presence=FieldPresence.NOT_FOUND
    ))
    institution: ExtractedField = field(default_factory=lambda: ExtractedField(
        value=None, confidence=0.0, presence=FieldPresence.NOT_FOUND
    ))

    # Prescription-specific
    medications: list[MedicationEntry] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)

    # Lab report-specific
    lab_tests: list[LabTestEntry] = field(default_factory=list)

    # Additional raw text sections
    diagnosis: ExtractedField = field(default_factory=lambda: ExtractedField(
        value=None, confidence=0.0, presence=FieldPresence.NOT_APPLICABLE
    ))
    follow_up: ExtractedField = field(default_factory=lambda: ExtractedField(
        value=None, confidence=0.0, presence=FieldPresence.NOT_APPLICABLE
    ))

    # Quality & Validation
    extraction_warnings: list[str] = field(default_factory=list)
    overall_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for API/JSON output."""
        def _field_dict(f: ExtractedField) -> dict[str, Any]:
            return {
                "value": f.value,
                "confidence": round(f.confidence, 3),
                "presence": f.presence,
                "raw_ocr_text": f.raw_ocr_text,
            }

        def _med_dict(m: MedicationEntry) -> dict[str, Any]:
            return {
                "name": _field_dict(m.name),
                "dosage": _field_dict(m.dosage),
                "schedule": {
                    "morning": _field_dict(m.morning),
                    "noon": _field_dict(m.noon),
                    "evening": _field_dict(m.evening),
                    "night": _field_dict(m.night),
                },
                "frequency_note": _field_dict(m.frequency_note),
                "duration": _field_dict(m.duration),
            }

        def _lab_dict(t: LabTestEntry) -> dict[str, Any]:
            return {
                "test_name": _field_dict(t.test_name),
                "result": _field_dict(t.result),
                "unit": _field_dict(t.unit),
                "reference_range": _field_dict(t.reference_range),
            }

        return {
            "document_type": self.document_type,
            "classification_confidence": round(self.classification_confidence, 3),
            "overall_confidence": round(self.overall_confidence, 3),
            "patient": {
                "name": _field_dict(self.patient_name),
                "age": _field_dict(self.patient_age),
                "sex": _field_dict(self.patient_sex),
                "hospital_number": _field_dict(self.hospital_number),
            },
            "doctor": _field_dict(self.doctor_name),
            "date": _field_dict(self.document_date),
            "department": _field_dict(self.department),
            "institution": _field_dict(self.institution),
            "medications": [_med_dict(m) for m in self.medications],
            "lab_tests": [_lab_dict(t) for t in self.lab_tests],
            "instructions": self.instructions,
            "diagnosis": _field_dict(self.diagnosis),
            "follow_up": _field_dict(self.follow_up),
            "extraction_warnings": self.extraction_warnings,
        }


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_DATE_PAT = re.compile(
    r"\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}|\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2})\b"
)
_AGE_PAT = re.compile(r"\b(\d{1,3})\s*[/\-]?\s*[MFmf]\b")
_AGE_SEX_PAT = re.compile(r"\b(\d{1,3})\s*(?:[yY]?(?:rs|ears)?|m)?\s*/\s*([MFmf])\b")
_DOSAGE_PAT = re.compile(r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|mL|IU|units?|%)\b", re.IGNORECASE)
_HOSP_NO_PAT = re.compile(r"(?:hosp(?:ital)?\.?\s*(?:no\.?|number|#|reg\.?)|H\.?No\.?|IP\s*(?:Number|No\.?)|GG\s*ID)\s*[:\-]?\s*([A-Za-z0-9\-\.\/]{4,16})", re.IGNORECASE)
_SCHEDULE_CELL = re.compile(r"^[10\-\+x\*o]{1,3}$")  # e.g., 1, 0, 1-0-0
_DURATION_PAT = re.compile(r"(?:x|for|X)\s*(\d+)\s*(days?|weeks?|months?)", re.IGNORECASE)
_FREQ_NOTE_KEYWORDS = [
    "before food", "after food", "with food", "empty stomach",
    "at bedtime", "hr before", "half hr", "1/2 hr", "½ hr",
]
_DOCTOR_PREFIX = re.compile(r"\bDr[\.:\s]+([A-Za-z][A-Za-z\s\.:]{2,40})", re.IGNORECASE)
_PATIENT_NAME_PAT = re.compile(
    r"(?:Mr\.?|Mrs\.?|Ms\.?|Miss|Master|Shri\.?|Smt\.?|Baby\.?|M/s\.?|M\.)\s*([A-Za-z][A-Za-z\s\.]{2,40})",
    re.IGNORECASE
)
_PATIENT_LABEL_PAT = re.compile(
    r"(?:Patient\s*Name|Patient|Pt\.?\s*Name|\bName\b)[\s\.:\-]+([A-Za-z][A-Za-z\s\.]{2,40})",
    re.IGNORECASE,
)




def _make_field(
    value: str | None,
    confidence: float,
    raw_text: str | None = None,
    line_no: int | None = None,
) -> ExtractedField:
    """Create an ExtractedField, setting presence automatically."""
    presence = FieldPresence.PRESENT if value else FieldPresence.NOT_FOUND
    return ExtractedField(
        value=value,
        confidence=confidence,
        presence=presence,
        raw_ocr_text=raw_text,
        source_line_no=line_no,
    )


class StructuredExtractor:
    """Extract structured fields from OCR results per document type."""

    def extract(
        self,
        ocr_results: list[OCRResult],
        document_type: str,
        classification_confidence: float = 0.0,
    ) -> StructuredDocument:
        """Run structured extraction for the given document type."""
        doc = StructuredDocument(
            document_type=document_type,
            classification_confidence=classification_confidence,
        )

        if not ocr_results:
            doc.extraction_warnings.append("No OCR results available for extraction")
            return doc

        # Normalize OCR text using the deterministic medical corrector
        from ocr.medical_corrector import get_corrector
        corrector = get_corrector()
        corrected_results: list[OCRResult] = []
        for r in ocr_results:
            c_res = corrector.correct_line(r.text)
            if c_res.was_modified:
                corrected_results.append(
                    OCRResult(
                        text=c_res.corrected_text,
                        confidence=r.confidence,
                        bbox=r.bbox,
                        page=r.page,
                        line_no=r.line_no,
                        region_id=r.region_id,
                        model=r.model,
                        model_version=r.model_version,
                        is_handwritten=r.is_handwritten,
                        is_reliable=r.is_reliable,
                    )
                )
            else:
                corrected_results.append(r)

        # Extract common fields from all document types
        self._extract_common_fields(corrected_results, doc)

        # Document-type-specific extraction
        if document_type == "prescription":
            self._extract_prescription(corrected_results, doc)
        elif document_type == "lab_report":
            self._extract_lab_report(ocr_results, doc)
        elif document_type == "discharge_summary":
            self._extract_discharge_summary(ocr_results, doc)

        # Compute overall confidence
        doc.overall_confidence = self._compute_overall_confidence(doc, ocr_results)

        return doc

    def _extract_common_fields(
        self, ocr_results: list[OCRResult], doc: StructuredDocument
    ) -> None:
        """Extract fields common to all document types."""
        lines_text = [(r.text, r.confidence, r.line_no) for r in ocr_results]

        for text, conf, line_no in lines_text:
            text_stripped = text.strip()

            # Date
            if doc.document_date.presence == FieldPresence.NOT_FOUND:
                date_match = _DATE_PAT.search(text_stripped)
                if date_match:
                    doc.document_date = _make_field(
                        date_match.group(0), conf, text_stripped, line_no
                    )

            # Patient name
            if doc.patient_name.presence == FieldPresence.NOT_FOUND:
                lbl_match = _PATIENT_LABEL_PAT.search(text_stripped)
                if lbl_match:
                    name_cand = lbl_match.group(1).strip()
                    if not any(kw in name_cand.lower() for kw in ["age", "sex", "hosp", "doctor"]):
                        doc.patient_name = _make_field(name_cand, conf, text_stripped, line_no)
                if doc.patient_name.presence == FieldPresence.NOT_FOUND:
                    name_match = _PATIENT_NAME_PAT.search(text_stripped)
                    if name_match:
                        doc.patient_name = _make_field(
                            name_match.group(0).strip(), conf, text_stripped, line_no
                        )

            # Age/Sex
            if doc.patient_age.presence == FieldPresence.NOT_FOUND:
                age_sex_match = _AGE_SEX_PAT.search(text_stripped)
                if age_sex_match:
                    doc.patient_age = _make_field(
                        age_sex_match.group(1), conf, text_stripped, line_no
                    )
                    doc.patient_sex = _make_field(
                        age_sex_match.group(2).upper(), conf, text_stripped, line_no
                    )

            # Hospital number
            if doc.hospital_number.presence == FieldPresence.NOT_FOUND:
                hosp_match = _HOSP_NO_PAT.search(text_stripped)
                if hosp_match:
                    doc.hospital_number = _make_field(
                        hosp_match.group(1), conf, text_stripped, line_no
                    )
                elif "hosp.no" in text_stripped.lower() or "hosp no" in text_stripped.lower():
                    # Check next line if available
                    for next_text, next_conf, next_line_no in lines_text:
                        if next_line_no == line_no + 1 and re.match(r"^\d{5,10}$", next_text.strip()):
                            doc.hospital_number = _make_field(
                                next_text.strip(), next_conf, f"{text_stripped} {next_text.strip()}", next_line_no
                            )
                            break

            # Doctor name & Institution
            _INSTITUTION_KEYWORDS = [
                "pathlab", "pathlabs", "laboratory", "laboratories", "diagnostic", "diagnostics",
                "hospital", "clinic", "imaging", "centre", "center", "health center", "institute", "accredited"
            ]

            # Institution detection from header / provider lines
            if doc.institution.presence == FieldPresence.NOT_FOUND:
                if any(w in text_stripped.lower() for w in _INSTITUTION_KEYWORDS):
                    if not any(kw in text_stripped.lower() for kw in ["ref by", "doctor", "consultant", "patient"]):
                        doc.institution = _make_field(text_stripped, conf, text_stripped, line_no)

            # Doctor name
            if doc.doctor_name.presence == FieldPresence.NOT_FOUND:
                if not any(kw in text_stripped.lower() for kw in ["consult to", "doubt", "regarding", "blockletters"]):
                    doctor_match = _DOCTOR_PREFIX.search(text_stripped)
                    if doctor_match:
                        cand = doctor_match.group(0).strip()
                        if any(w in cand.lower() for w in _INSTITUTION_KEYWORDS):
                            if doc.institution.presence == FieldPresence.NOT_FOUND:
                                doc.institution = _make_field(cand, conf, text_stripped, line_no)
                        else:
                            doc.doctor_name = _make_field(cand, conf, text_stripped, line_no)

            # Department
            if doc.department.presence == FieldPresence.NOT_FOUND:
                dept_match = re.search(
                    r"(?:dept\.?|department|consultant['\s]*)\s*[:\-]?\s*([A-Za-z]{3,20})",
                    text_stripped,
                    re.IGNORECASE,
                )
                if dept_match:
                    doc.department = _make_field(
                        dept_match.group(1).upper(), conf, text_stripped, line_no
                    )

    def _extract_prescription(
        self, ocr_results: list[OCRResult], doc: StructuredDocument
    ) -> None:
        """Extract medication entries from prescription OCR text."""
        # Look for medication lines: lines starting with common drug prefixes
        # or having dosage patterns
        med_prefix_pat = re.compile(
            r"^(?:[\-\*\•\d\.\)]\s*)*(?:T\.|C\.|Cap\.|Tab\.|Inj\.|Syp\.|Syr\.|Oint\.|Gel\.|Eye\.|Ear\.|C\-|T\-|M\s+)\s*([A-Za-z0-9\s\-\.]+)",
            re.IGNORECASE
        )
        duration_found: str | None = None
        freq_note_found: str | None = None
        instructions_collected: list[str] = []

        known_drugs = [
            "amox", "zerodol", "pan", "pantop", "microdox", "lacne", "perobar",
            "paracetamol", "ibuprofen", "metformin", "atorvastatin", "omeprazole",
            "pantoprazole", "ciprofloxacin", "azithromycin", "cetirizine",
            "breezy", "opox", "allihose", "aldigesic", "shipen",
        ]


        for i, r in enumerate(ocr_results):
            text = r.text.strip()
            if not text:
                continue

            # Duration detection (e.g., "x 3 days")
            dur_match = _DURATION_PAT.search(text)
            if dur_match:
                duration_found = dur_match.group(0)

            # Frequency notes
            text_lower = text.lower()
            for note in _FREQ_NOTE_KEYWORDS:
                if note in text_lower:
                    freq_note_found = text
                    instructions_collected.append(text)
                    break

            # Medication line
            med_match = med_prefix_pat.match(text)
            drug_name = None
            if med_match:
                drug_name = med_match.group(1).strip()
            else:
                # Also recognize standalone common drug/brand names
                for kd in known_drugs:
                    if kd in text.lower():
                        drug_name = text
                        break

            if drug_name:
                # Look for dosage in same line or immediate next line
                dosage_match = _DOSAGE_PAT.search(text)
                dosage_val = None
                dosage_conf = 0.0
                if dosage_match:
                    dosage_val = f"{dosage_match.group(1)} {dosage_match.group(2)}"
                    dosage_conf = r.confidence
                elif i + 1 < len(ocr_results):
                    next_text = ocr_results[i + 1].text.strip()
                    next_match = _DOSAGE_PAT.search(next_text)
                    if next_match:
                        dosage_val = f"{next_match.group(1)} {next_match.group(2)}"
                        dosage_conf = ocr_results[i + 1].confidence

                medication = MedicationEntry(
                    name=_make_field(drug_name, r.confidence, text, r.line_no),
                    dosage=_make_field(dosage_val, dosage_conf, text, r.line_no),
                    morning=ExtractedField(value=None, confidence=0.0, presence=FieldPresence.UNCERTAIN),
                    noon=ExtractedField(value=None, confidence=0.0, presence=FieldPresence.UNCERTAIN),
                    evening=ExtractedField(value=None, confidence=0.0, presence=FieldPresence.UNCERTAIN),
                    night=ExtractedField(value=None, confidence=0.0, presence=FieldPresence.UNCERTAIN),
                    frequency_note=ExtractedField(
                        value=freq_note_found,
                        confidence=0.7 if freq_note_found else 0.0,
                        presence=FieldPresence.PRESENT if freq_note_found else FieldPresence.NOT_FOUND,
                    ),
                    duration=ExtractedField(
                        value=duration_found,
                        confidence=0.8 if duration_found else 0.0,
                        presence=FieldPresence.PRESENT if duration_found else FieldPresence.NOT_FOUND,
                    ),
                )
                doc.medications.append(medication)

        doc.instructions = list(set(instructions_collected))

        if not doc.medications:
            doc.extraction_warnings.append(
                "No medications detected with standard prefixes (T./C./Cap./Tab.). "
                "Document may use non-standard abbreviations."
            )

    def _extract_lab_report(
        self, ocr_results: list[OCRResult], doc: StructuredDocument
    ) -> None:
        """Extract lab test rows from lab report OCR text."""
        # Method 1: Single-line pattern match
        lab_line_pat = re.compile(
            r"([A-Za-z][A-Za-z0-9\s\(\)]{2,40})\s{2,}(\d+\.?\d*)\s*([a-zA-Z/%]+)?\s{1,}([\d\.]+\s*[-\u2013]\s*[\d\.]+)?",
        )
        for r in ocr_results:
            text = r.text.strip()
            match = lab_line_pat.match(text)
            if match:
                test_name = match.group(1).strip()
                result_val = match.group(2)
                unit = match.group(3) or ""
                ref_range = match.group(4) or ""

                doc.lab_tests.append(LabTestEntry(
                    test_name=_make_field(test_name, r.confidence, text, r.line_no),
                    result=_make_field(result_val, r.confidence, text, r.line_no),
                    unit=_make_field(unit.strip() or None, r.confidence if unit else 0.0, text, r.line_no),
                    reference_range=_make_field(
                        ref_range.strip() or None,
                        r.confidence if ref_range else 0.0,
                        text, r.line_no
                    ),
                ))

        # Method 2: Multi-column bounding-box row grouping fallback
        if len(doc.lab_tests) < 2:
            rows = self._group_by_rows([r for r in ocr_results if r.bbox])
            for row in rows:
                if len(row) < 2:
                    continue
                first_text = row[0].text.strip()
                # Skip header/boilerplate rows
                if any(h in first_text.lower() for h in ["testname", "test name", "investigation", "parameter", "result", "range", "interpretation", "date", "specimen", "patient"]):
                    continue

                # Locate numeric result column
                val_idx = -1
                num_pat = re.compile(r"^[\d\.]+$")
                for i in range(1, len(row)):
                    cleaned_val = row[i].text.strip().rstrip(".:- ")
                    if num_pat.match(cleaned_val) and any(c.isdigit() for c in cleaned_val):
                        val_idx = i
                        break

                if val_idx != -1:
                    test_name = first_text.rstrip(".:- ")
                    val_str = row[val_idx].text.strip().rstrip(".:- ")
                    val_conf = row[val_idx].confidence

                    unit_str = None
                    unit_conf = 0.0
                    ref_str = None
                    ref_conf = 0.0

                    for j in range(val_idx + 1, len(row)):
                        elem_text = row[j].text.strip()
                        if any(u in elem_text.lower() for u in ["mg", "meq", "iu", "g/", "%", "mmol", "dl"]):
                            unit_str = elem_text.rstrip(".:- ")
                            unit_conf = row[j].confidence
                        elif re.search(r"\d+[\s\.\-]+[–\-]\s*[\d\.]+", elem_text):
                            ref_str = elem_text.rstrip(".:- ")
                            ref_conf = row[j].confidence
                        elif not unit_str and not re.search(r"\d", elem_text):
                            unit_str = elem_text.rstrip(".:- ")
                            unit_conf = row[j].confidence
                        elif not ref_str and re.search(r"\d", elem_text):
                            ref_str = elem_text.rstrip(".:- ")
                            ref_conf = row[j].confidence

                    doc.lab_tests.append(LabTestEntry(
                        test_name=_make_field(test_name, row[0].confidence, row[0].text, row[0].line_no),
                        result=_make_field(val_str, val_conf, row[val_idx].text, row[val_idx].line_no),
                        unit=_make_field(unit_str, unit_conf, unit_str, None),
                        reference_range=_make_field(ref_str, ref_conf, ref_str, None),
                    ))

    @staticmethod
    def _group_by_rows(ocr_results: list[OCRResult], y_tol: float = 16.0) -> list[list[OCRResult]]:
        """Cluster OCR bounding boxes into horizontal rows by vertical proximity."""
        def _get_coords(r: OCRResult) -> tuple[float, float, float, float]:
            b = r.bbox
            if hasattr(b, "x0"):
                return (float(b.x0), float(b.y0), float(b.x1), float(b.y1))
            if isinstance(b, (list, tuple)) and len(b) >= 4:
                return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
            return (0.0, 0.0, 0.0, 0.0)

        valid_results = [r for r in ocr_results if r.bbox is not None]
        sorted_res = sorted(valid_results, key=lambda r: (_get_coords(r)[1], _get_coords(r)[0]))
        rows: list[list[OCRResult]] = []
        for r in sorted_res:
            _, y0, _, y1 = _get_coords(r)
            r_cy = (y0 + y1) / 2.0
            placed = False
            for row in rows:
                c_coords = [_get_coords(item) for item in row]
                row_cy = sum((c[1] + c[3]) / 2.0 for c in c_coords) / len(c_coords)
                row_h = max(c[3] - c[1] for c in c_coords)
                if abs(r_cy - row_cy) < max(y_tol, row_h * 0.5):
                    row.append(r)
                    placed = True
                    break
            if not placed:
                rows.append([r])
        for row in rows:
            row.sort(key=lambda item: _get_coords(item)[0])
        return rows

    def _extract_discharge_summary(
        self, ocr_results: list[OCRResult], doc: StructuredDocument
    ) -> None:
        """Extract diagnosis and follow-up from discharge summary."""
        diag_pat = re.compile(r"(?:diagnosis|impression|final diagnosis)[:\-]?\s*(.+)", re.IGNORECASE)
        followup_pat = re.compile(r"(?:follow[\s\-]?up|advice|review)[:\-]?\s*(.+)", re.IGNORECASE)

        for r in ocr_results:
            text = r.text.strip()
            if doc.diagnosis.presence == FieldPresence.NOT_APPLICABLE or doc.diagnosis.presence == FieldPresence.NOT_FOUND:
                d_match = diag_pat.search(text)
                if d_match:
                    doc.diagnosis = _make_field(d_match.group(1).strip(), r.confidence, text, r.line_no)

            if doc.follow_up.presence == FieldPresence.NOT_APPLICABLE or doc.follow_up.presence == FieldPresence.NOT_FOUND:
                f_match = followup_pat.search(text)
                if f_match:
                    doc.follow_up = _make_field(f_match.group(1).strip(), r.confidence, text, r.line_no)

    def _compute_overall_confidence(
        self, doc: StructuredDocument, ocr_results: list[OCRResult]
    ) -> float:
        """Compute overall extraction confidence from key fields."""
        key_fields = [
            doc.patient_name,
            doc.document_date,
            doc.doctor_name,
        ]
        present_confs = [f.confidence for f in key_fields if f.presence == FieldPresence.PRESENT]
        ocr_mean = sum(r.confidence for r in ocr_results) / len(ocr_results) if ocr_results else 0.0

        if present_confs:
            field_conf = sum(present_confs) / len(present_confs)
            return round((field_conf + ocr_mean) / 2.0, 3)
        return round(ocr_mean, 3)
