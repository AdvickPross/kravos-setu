"""Heuristic document type classifier for medical document images.

Classifies documents into: prescription, lab_report, discharge_summary,
medical_bill, insurance_form, or unknown.

The classifier uses keyword signals from the raw OCR text rather than image
pixels, avoiding the need for a large vision model. It returns a confidence
score so the pipeline can fall back to 'unknown' when the signal is weak.

Design constraints:
- No LLM. Purely deterministic keyword and layout heuristics.
- Confidence must be honest. If the document is ambiguous, return 'unknown'.
- Does not modify or interpret clinical content.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pipeline.schemas.canonical import OCRResult

log = logging.getLogger(__name__)

# Minimum confidence to claim a document type. Below this, return 'unknown'.
DEFAULT_MIN_CONFIDENCE: float = 0.35


@dataclass(frozen=True)
class DocumentTypeResult:
    """Result of document type classification."""

    document_type: str  # 'prescription', 'lab_report', 'discharge_summary', etc.
    confidence: float   # 0.0 to 1.0
    signals: dict[str, int]  # {keyword: hit_count} for transparency


# Keyword signal sets per document type.
# Weights: each keyword match contributes 1.0 to the raw score for its category.
# Higher-specificity keywords are listed first for logging clarity.

_SIGNALS: dict[str, list[str]] = {
    "prescription": [
        "prescription",
        "clinic",
        "dispensary",
        "physician",
        "consultant",
        "family physician",
        "rx",
        "\\bRx\\b",
        "\\br/x\\b",
        "\\br/o\\b",
        "\\bc/o\\b",
        "rlo",
        "for medicine",
        "b\\.p",
        "\\bbp\\b",
        "spo2",
        "temp",
        "vitals",
        "pulse",
        "drug",
        "dosage",
        "tablet",
        "tab\\.",
        "capsule",
        "cap\\.",
        "syrup",
        "ointment",
        "twice daily",
        "once daily",
        "bd",
        "tds",
        "od",
        "sos",
        "before food",
        "after food",
        "morning",
        "evening",
        "night",
        "days",
        "mg",
        "mcg",
        "ml",
        "\\bt\\.\\b",
        "\\bc\\.\\b",
    ],
    "lab_report": [
        "laboratory",
        "lab report",
        "pathology",
        "haematology",
        "biochemistry",
        "reference range",
        "normal range",
        "result",
        "units",
        "method",
        "hemoglobin",
        "hb",
        "wbc",
        "rbc",
        "platelet",
        "glucose",
        "creatinine",
        "urea",
        "sodium",
        "potassium",
        "cholesterol",
        "triglyceride",
        "tsh",
        "hba1c",
        "serum",
        "plasma",
        "urine",
        "g/dl",
        "mg/dl",
        "mmol/l",
        "u/l",
        "iu/l",
    ],
    "discharge_summary": [
        "discharge summary",
        "discharge note",
        "admission",
        "admitted on",
        "discharge",
        "date of admission",
        "date of discharge",
        "chief complaint",
        "history of present illness",
        "past history",
        "clinical examination",
        "diagnosis",
        "treatment given",
        "advice on discharge",
        "follow up",
        "prognosis",
        "ward",
        "icu",
        "procedure",
        "operation",
        "surgery",
    ],
    "medical_bill": [
        "invoice",
        "bill",
        "receipt",
        "payment",
        "amount",
        "total",
        "tax",
        "gst",
        "consultation fee",
        "room charges",
        "surgery charges",
        "pharmacy",
        "drugs and materials",
        "insurance claim",
        "payable",
        "rupees",
        "inr",
        "rs\\.",
        "\\brs\\b",
    ],
    "insurance_form": [
        "insurance",
        "claim form",
        "policy number",
        "policy no",
        "insurer",
        "insured",
        "tpa",
        "pre-authorization",
        "reimbursement",
        "cashless",
        "sum insured",
        "ailment",
        "icd code",
        "icd-10",
        "diagnosis code",
        "treating doctor",
        "hospital registration",
    ],
}

# Compile patterns for performance
_COMPILED: dict[str, list[re.Pattern]] = {
    doc_type: [re.compile(kw, re.IGNORECASE) for kw in keywords]
    for doc_type, keywords in _SIGNALS.items()
}


class DocumentClassifier:
    """Heuristic keyword-based medical document type classifier."""

    def __init__(self, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> None:
        self.min_confidence = min_confidence

    def classify(
        self, ocr_results: list[OCRResult], min_confidence: float | None = None
    ) -> DocumentTypeResult:
        """Classify a document from its OCR results.

        Args:
            ocr_results: All OCR lines from the document.
            min_confidence: Override for minimum confidence threshold.

        Returns:
            DocumentTypeResult with document_type, confidence, and signal breakdown.
        """
        threshold = min_confidence if min_confidence is not None else self.min_confidence
        full_text = " ".join(r.text for r in ocr_results if r.text.strip()).lower()

        if not full_text.strip():
            return DocumentTypeResult(
                document_type="unknown",
                confidence=0.0,
                signals={},
            )

        scores: dict[str, float] = {}
        signals: dict[str, dict[str, int]] = {}

        for doc_type, patterns in _COMPILED.items():
            hit_signals: dict[str, int] = {}
            score = 0.0
            for pat, kw in zip(patterns, _SIGNALS[doc_type]):
                matches = pat.findall(full_text)
                if matches:
                    count = len(matches)
                    hit_signals[kw] = count
                    score += min(count, 3)  # Cap per-keyword contribution at 3

            scores[doc_type] = score
            signals[doc_type] = hit_signals

        if not any(scores.values()):
            return DocumentTypeResult(
                document_type="unknown",
                confidence=0.0,
                signals={},
            )

        best_type = max(scores, key=lambda k: scores[k])
        best_score = scores[best_type]
        total_score = sum(scores.values())

        # Confidence = best_score / total_score (how dominant is the winner)
        confidence = best_score / total_score if total_score > 0 else 0.0

        # Require a minimum absolute score (at least 3 keyword hits)
        if best_score < 3:
            confidence *= 0.5  # Penalize low-hit classifications

        log.info(
            "Document classification: %s (confidence=%.2f, scores=%s)",
            best_type,
            confidence,
            {k: round(v, 1) for k, v in scores.items()},
        )

        if confidence < threshold:
            return DocumentTypeResult(
                document_type="unknown",
                confidence=confidence,
                signals=signals.get(best_type, {}),
            )

        return DocumentTypeResult(
            document_type=best_type,
            confidence=round(confidence, 3),
            signals=signals.get(best_type, {}),
        )


# Module-level convenience singleton
_default_classifier: DocumentClassifier | None = None


def classify_document(
    ocr_results: list[OCRResult],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> DocumentTypeResult:
    """Classify a document from OCR results using the default classifier."""
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = DocumentClassifier()
    return _default_classifier.classify(ocr_results, min_confidence=min_confidence)
