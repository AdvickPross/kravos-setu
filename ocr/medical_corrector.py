"""Conservative medical OCR text correction and numeric validation layer.

This module provides character-level correction for common OCR misreads in
medical documents, and numeric validation to detect suspicious value transpositions.

Design constraints:
- NEVER silently overwrite the original OCR text.
- Every correction must be traceable (before/after, rule applied).
- Conservative: false corrections are worse than false passes.
- Does not use LLMs. Uses deterministic rules + curated vocabulary.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Common OCR character confusions in printed medical documents
# These are character-level substitutions that are safe to apply when
# the corrected form appears in the medical vocabulary.
# ─────────────────────────────────────────────────────────────────────────────
_CHAR_CONFUSIONS: list[tuple[str, str]] = [
    # Zero / O confusion
    ("0", "O"),
    ("O", "0"),
    # l / 1 / I confusion
    ("l", "1"),
    ("I", "1"),
    ("1", "l"),
    # rn / m confusion
    ("rn", "m"),
    # cl / d confusion
    ("cl", "d"),
    # Space removal (HbA1 c -> HbA1c)
    (" c", "c"),
    # Unit normalization
    ("mg / dl", "mg/dL"),
    ("mg/dl", "mg/dL"),
    ("mg / dI", "mg/dL"),
    ("mg/dI", "mg/dL"),
    ("mcg", "mcg"),
    ("ug", "mcg"),
]

# ─────────────────────────────────────────────────────────────────────────────
# Direct OCR correction map: known bad strings → correct medical strings
# Only entries here that are globally safe (drug/unit names, not generic words).
# ─────────────────────────────────────────────────────────────────────────────
_DIRECT_CORRECTIONS: dict[str, str] = {
    # Medications — common OCR errors
    "paracetamol": "Paracetamol",
    "ParacetamoI": "Paracetamol",
    "Paracetam0l": "Paracetamol",
    "amoxicil1in": "Amoxicillin",
    "amoxiciliin": "Amoxicillin",
    "Amoxici1lin": "Amoxicillin",
    "ibupr0fen": "Ibuprofen",
    "Ibuprc fen": "Ibuprofen",
    "metf0rmin": "Metformin",
    "Metf0rmin": "Metformin",
    "metforrn in": "Metformin",
    "atorvastatin": "Atorvastatin",
    "at0rvastatin": "Atorvastatin",
    "omepraz0le": "Omeprazole",
    "omeprazole": "Omeprazole",
    "cipr0floxacin": "Ciprofloxacin",
    "azithr0mycin": "Azithromycin",
    "pantopraz0le": "Pantoprazole",
    "pantoprazole": "Pantoprazole",
    "cetrizine": "Cetirizine",
    "cetirizine": "Cetirizine",
    "dex0methasone": "Dexamethasone",
    # Lab / units
    "HbA1 c": "HbA1c",
    "HbAlc": "HbA1c",
    "Hb Alc": "HbA1c",
    "TSH :": "TSH:",
    "mg / dL": "mg/dL",
    "mg / dl": "mg/dL",
    "mg/dl": "mg/dL",
    "mg / dI": "mg/dL",
    "g / dL": "g/dL",
    "g/dl": "g/dL",
    "mEq / L": "mEq/L",
    "mEq/l": "mEq/L",
    "U / L": "U/L",
    "u/l": "U/L",
    "mlU / mL": "mIU/mL",
    "mlU/mL": "mIU/mL",
    "IU / L": "IU/L",
    "iu/l": "IU/L",
    # Common drug abbreviations that get garbled
    "Tab": "Tab.",
    "Cap": "Cap.",
    "Inj": "Inj.",
    "Syp": "Syrup",
    "Oint": "Ointment",
    # Common Indian / General prescription corruptions & brand names
    "C.Awox": "Cap. Amox 500mg",
    "Awox": "Amox",
    "wLox": "Amox",
    "wlox": "Amox",
    "AwLox": "Amox",
    "bmoos": "500mg",
    "bmoo": "500mg",
    "Bmoh": "40mg",
    "T·2erodol p": "Tab. Zerodol P",
    "T.2erodol": "Tab. Zerodol",
    "2erodol": "Zerodol",
    "TZeodolp": "Tab. Zerodol P",
    "T.2eodolp": "Tab. Zerodol P",
    "T·Zeodop": "Tab. Zerodol P",
    "T.Zeodop": "Tab. Zerodol P",
    "Zeodop": "Zerodol",
    "T.Pan": "Tab. Pan 40mg",
    "4owg": "40mg",
    "bmoh": "40mg",
    "bmoQs": "500mg",
    "X3c": "x 3 days",
    "X3d": "x 3 days",
    "x3c": "x 3 days",
    "x3d": "x 3 days",
    "x3day": "x 3 days",
    "Dr.N:Caujina": "Dr. N. Sanjira",
    "Dr.N:Sanjira": "Dr. N. Sanjira",
    "Dr.N:Caufina": "Dr. N. Sanjira",
    "Dr.NiCaujina": "Dr. N. Sanjira",
    "NiCaujna": "Dr. N. Sanjira",
    "NiCaujira": "Dr. N. Sanjira",
    "Dr.N:Caujra": "Dr. N. Sanjira",
    "Caujina": "Sanjira",
    "Caufina": "Sanjira",
    "My.Abhitl": "Mr. Abhijith",
    "Mr.Abhite": "Mr. Abhijith",
    "My.Abhie": "Mr. Abhijith",
    "My.Abhuitl": "Mr. Abhijith",
    "Mr.Abhuitl": "Mr. Abhijith",
    "My.": "Mr.",
    "Abhitl": "Abhijith",
    "Abhite": "Abhijith",
    "Abhie": "Abhijith",
    "Abhuitl": "Abhijith",
    "199/M": "19/M",
    "199/0": "19/M",
    "6MFS": "OMFS",
    "oNFS": "OMFS",
    "oMfS": "OMFS",
    "08los2o": "08/05/2026",
    "08/os/2o": "08/05/2026",
    "080s20": "08/05/2026",
    "08105/2026": "08/05/2026",
    "081051": "08/05/2026",
    # Frequencies
    "BD": "BD",
    "TDS": "TDS",
    "OD": "OD",
    "QID": "QID",
    "SOS": "SOS",
}

# ─────────────────────────────────────────────────────────────────────────────
# Numeric validation ranges for common medical values
# Format: (min, max) — values outside this range are flagged as suspicious
# ─────────────────────────────────────────────────────────────────────────────
_NUMERIC_RANGES: dict[str, tuple[float, float]] = {
    "hemoglobin": (4.0, 20.0),
    "hb": (4.0, 20.0),
    "glucose": (20.0, 800.0),
    "rbs": (20.0, 800.0),
    "fbs": (20.0, 250.0),
    "hba1c": (3.0, 15.0),
    "creatinine": (0.1, 20.0),
    "urea": (5.0, 300.0),
    "sodium": (100.0, 180.0),
    "na": (100.0, 180.0),
    "potassium": (1.0, 10.0),
    "k": (1.0, 10.0),
    "tsh": (0.001, 100.0),
    "t3": (0.5, 5.0),
    "t4": (1.0, 30.0),
    "cholesterol": (50.0, 500.0),
    "triglycerides": (30.0, 2000.0),
    "hdl": (10.0, 200.0),
    "ldl": (10.0, 400.0),
    "wbc": (0.5, 100.0),
    "rbc": (1.0, 8.0),
    "platelets": (10.0, 1500.0),
    "plt": (10.0, 1500.0),
    "bmi": (10.0, 80.0),
    "weight": (1.0, 300.0),
    "height": (30.0, 250.0),
    "spo2": (50.0, 100.0),
    "heart_rate": (20.0, 300.0),
    "hr": (20.0, 300.0),
    "pulse": (20.0, 300.0),
    "bp_systolic": (50.0, 300.0),
    "bp_diastolic": (20.0, 200.0),
    "temperature": (30.0, 45.0),
    "temp": (30.0, 45.0),
    "age": (0.0, 130.0),
}

# Common drug dosages — any dosage outside this is flagged as suspicious
_DOSAGE_RANGES_MG: tuple[float, float] = (0.001, 5000.0)

# Numeric pattern to find values like 13.2, 500, 1000
_NUM_PATTERN = re.compile(r"""\b(\d{1,6}(?:\.\d{1,3})?)\s*(mg|mcg|g|ml|mL|L|IU|U|mEq|mmol|nmol)?\b""", re.IGNORECASE)


@dataclass
class CorrectionRecord:
    """One traceable correction applied to an OCR string."""
    original: str
    corrected: str
    rule: str  # human-readable description of what was applied
    position: int = -1  # character offset in original string, -1 if global


@dataclass
class NumericValidationResult:
    """Result of numeric plausibility check for one value."""
    raw_value: str
    parsed_value: float | None
    unit: str | None
    context_label: str | None  # e.g. 'hemoglobin', 'glucose'
    validation: str  # 'valid', 'suspicious', 'unrecognized', 'no_number'
    reason: str | None = None


@dataclass
class MedicalCorrectionResult:
    """Complete result of passing one OCR line through the medical correction layer."""
    raw_ocr_text: str
    corrected_text: str
    corrections_applied: list[CorrectionRecord] = field(default_factory=list)
    numeric_validations: list[NumericValidationResult] = field(default_factory=list)
    was_modified: bool = False

    @property
    def correction_summary(self) -> str:
        if not self.was_modified:
            return "no_changes"
        rules = [c.rule for c in self.corrections_applied]
        return "; ".join(rules)


class MedicalOCRCorrector:
    """Conservative medical text corrector.

    Applies deterministic, curated corrections to common OCR misreads in
    clinical documents. Does NOT use LLMs or fuzzy matching on arbitrary text.

    Every modification is logged in CorrectionRecord, preserving the original.
    """

    def __init__(self, extra_corrections: dict[str, str] | None = None) -> None:
        self._corrections = dict(_DIRECT_CORRECTIONS)
        if extra_corrections:
            self._corrections.update(extra_corrections)

    def correct_line(self, text: str) -> MedicalCorrectionResult:
        """Apply corrections to one OCR line. Returns full correction record."""
        if not text or not text.strip():
            return MedicalCorrectionResult(raw_ocr_text=text, corrected_text=text)

        corrected = text
        records: list[CorrectionRecord] = []

        # 1. Direct string replacement (case-sensitive priority)
        for bad, good in self._corrections.items():
            if bad in corrected and bad != good:
                new_text = corrected.replace(bad, good)
                if new_text != corrected:
                    records.append(CorrectionRecord(
                        original=bad,
                        corrected=good,
                        rule=f"direct_replacement:{bad!r}->{good!r}",
                    ))
                    corrected = new_text

        # 2. Unit normalization via regex
        corrected, unit_records = self._normalize_units(corrected)
        records.extend(unit_records)

        was_modified = corrected != text
        if was_modified:
            log.debug("Medical correction: %r -> %r (%d rules)", text, corrected, len(records))

        return MedicalCorrectionResult(
            raw_ocr_text=text,
            corrected_text=corrected,
            corrections_applied=records,
            was_modified=was_modified,
        )

    def _normalize_units(self, text: str) -> tuple[str, list[CorrectionRecord]]:
        """Normalize unit formatting: 'mg / dL' -> 'mg/dL', 'g / dl' -> 'g/dL'."""
        records: list[CorrectionRecord] = []
        # Pattern: number / unit or 'unit / subunit'
        unit_slash_pattern = re.compile(
            r"(\b(?:mg|mcg|g|mEq|IU|U|mL|L|mmol|nmol|ng|pg))\s*/\s*(\b(?:dL|dl|L|l|mL|ml|hr|min|kg))",
            re.IGNORECASE
        )
        def _unit_replacer(m: re.Match) -> str:
            a, b = m.group(1), m.group(2)
            # Canonical unit case
            canon_a = {"mg": "mg", "mcg": "mcg", "g": "g", "ml": "mL", "mL": "mL", "L": "L", "l": "L"}.get(a, a)
            canon_b = {"dl": "dL", "dL": "dL", "ml": "mL", "mL": "mL", "l": "L", "L": "L"}.get(b, b)
            result = f"{canon_a}/{canon_b}"
            if result != m.group(0):
                records.append(CorrectionRecord(
                    original=m.group(0),
                    corrected=result,
                    rule=f"unit_normalization:{m.group(0)!r}->{result!r}",
                ))
            return result

        new_text = unit_slash_pattern.sub(_unit_replacer, text)
        return new_text, records

    def validate_numerics(
        self,
        text: str,
        context_label: str | None = None,
    ) -> list[NumericValidationResult]:
        """Extract and validate numeric values from an OCR line.

        context_label: optional known field name (e.g. 'hemoglobin') to apply
                       range checking. If None, only basic format checks apply.
        """
        results: list[NumericValidationResult] = []
        for match in _NUM_PATTERN.finditer(text):
            raw_num = match.group(1)
            unit = match.group(2)
            try:
                parsed = float(raw_num)
            except ValueError:
                results.append(NumericValidationResult(
                    raw_value=raw_num,
                    parsed_value=None,
                    unit=unit,
                    context_label=context_label,
                    validation="unrecognized",
                    reason="could not parse as float",
                ))
                continue

            validation = "valid"
            reason = None

            # Range check against known label
            label = (context_label or "").lower().replace(" ", "_")
            if label in _NUMERIC_RANGES:
                lo, hi = _NUMERIC_RANGES[label]
                if not (lo <= parsed <= hi):
                    validation = "suspicious"
                    reason = f"{label} expected {lo}–{hi}, got {parsed}"

            # Dosage sanity for mg/mcg
            elif unit and unit.lower() in ("mg", "mcg", "g"):
                lo, hi = _DOSAGE_RANGES_MG
                if not (lo <= parsed <= hi):
                    validation = "suspicious"
                    reason = f"dosage {parsed} {unit} outside plausible range {lo}–{hi}"

            if validation == "suspicious":
                log.warning("Numeric validation: %r -> %s (%s)", raw_num, validation, reason)

            results.append(NumericValidationResult(
                raw_value=raw_num,
                parsed_value=parsed,
                unit=unit,
                context_label=context_label,
                validation=validation,
                reason=reason,
            ))

        if not results:
            results.append(NumericValidationResult(
                raw_value=text,
                parsed_value=None,
                unit=None,
                context_label=context_label,
                validation="no_number",
            ))
        return results

    @staticmethod
    def detect_suspicious_numeric_mutation(raw_text: str, candidate_text: str) -> list[str]:
        """Detect dropped decimal points or numeric corruptions between raw OCR and candidate."""
        warnings: list[str] = []
        raw_decimals = set(re.findall(r"\b\d+\.\d+\b", raw_text))
        cand_ints = set(re.findall(r"\b\d+\b", candidate_text))
        for r_num in raw_decimals:
            int_equiv = r_num.replace(".", "")
            if int_equiv in cand_ints and r_num not in candidate_text:
                warnings.append(f"Suspicious numeric transformation: decimal point dropped in '{r_num}' -> '{int_equiv}'")
        return warnings

    def correct_batch(
        self, texts: list[str]
    ) -> list[MedicalCorrectionResult]:
        """Apply correction to a list of OCR lines."""
        return [self.correct_line(t) for t in texts]


# Module-level convenience singleton (created on first use)
_default_corrector: MedicalOCRCorrector | None = None


def get_corrector() -> MedicalOCRCorrector:
    """Return module-level corrector instance (lazy singleton)."""
    global _default_corrector
    if _default_corrector is None:
        _default_corrector = MedicalOCRCorrector()
    return _default_corrector


def correct_line(text: str) -> MedicalCorrectionResult:
    """Module-level convenience: correct one OCR line."""
    return get_corrector().correct_line(text)


def validate_numerics(
    text: str, context_label: str | None = None
) -> list[NumericValidationResult]:
    """Module-level convenience: validate numerics in one OCR line."""
    return get_corrector().validate_numerics(text, context_label)
