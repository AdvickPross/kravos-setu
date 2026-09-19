"""Benchmark and evaluation metrics for Medical OCR and Document Intelligence.

Calculates:
- CER (Character Error Rate) via Levenshtein distance
- WER (Word Error Rate) via word-level edit distance
- Field-level Precision, Recall, and F1 for structured extraction
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def levenshtein_distance(seq1: list[Any] | str, seq2: list[Any] | str) -> int:
    """Compute minimum edit operations (insert, delete, substitute) between two sequences."""
    size_x = len(seq1) + 1
    size_y = len(seq2) + 1
    matrix = [[0] * size_y for _ in range(size_x)]

    for x in range(size_x):
        matrix[x][0] = x
    for y in range(size_y):
        matrix[0][y] = y

    for x in range(1, size_x):
        for y in range(1, size_y):
            if seq1[x - 1] == seq2[y - 1]:
                matrix[x][y] = matrix[x - 1][y - 1]
            else:
                matrix[x][y] = min(
                    matrix[x - 1][y] + 1,      # deletion
                    matrix[x][y - 1] + 1,      # insertion
                    matrix[x - 1][y - 1] + 1,  # substitution
                )
    return matrix[size_x - 1][size_y - 1]


def calculate_cer(reference: str, hypothesis: str) -> float:
    """Calculate Character Error Rate (CER = EditDistance / RefLength)."""
    ref_chars = list(reference)
    hyp_chars = list(hypothesis)
    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0
    dist = levenshtein_distance(ref_chars, hyp_chars)
    return dist / len(ref_chars)


def calculate_wer(reference: str, hypothesis: str) -> float:
    """Calculate Word Error Rate (WER = WordEditDistance / RefWordCount)."""
    ref_words = re.findall(r"\S+", reference)
    hyp_words = re.findall(r"\S+", hypothesis)
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    dist = levenshtein_distance(ref_words, hyp_words)
    return dist / len(ref_words)


from enum import Enum


class OCRErrorCategory(str, Enum):
    DETECTION_ERROR = "DETECTION_ERROR"
    RECOGNITION_ERROR = "RECOGNITION_ERROR"
    HANDWRITING_ERROR = "HANDWRITING_ERROR"
    PREPROCESSING_ERROR = "PREPROCESSING_ERROR"
    LAYOUT_ERROR = "LAYOUT_ERROR"
    TABLE_ERROR = "TABLE_ERROR"
    NUMERIC_ERROR = "NUMERIC_ERROR"
    NORMALIZATION_ERROR = "NORMALIZATION_ERROR"


ERROR_TAXONOMY_METADATA: dict[OCRErrorCategory, dict[str, str]] = {
    OCRErrorCategory.DETECTION_ERROR: {
        "subsystem": "RT-DETR / Layout Segmenter",
        "manifestation": "Missing lines, cut-off words, truncated bounding boxes",
        "action": "Fine-tune RT-DETR on clinical prescription layouts (training/detection/)",
    },
    OCRErrorCategory.RECOGNITION_ERROR: {
        "subsystem": "PP-OCR / RapidOCR",
        "manifestation": "Character confusion on printed machine typography",
        "action": "Fine-tune PP-OCR SVTR recognizer on Indian hospital font styles (training/ppocr/)",
    },
    OCRErrorCategory.HANDWRITING_ERROR: {
        "subsystem": "TrOCR",
        "manifestation": "Misread cursive doctor handwriting",
        "action": "Fine-tune TrOCR with LoRA on region-specific clinical prescriptions (training/trocr/)",
    },
    OCRErrorCategory.PREPROCESSING_ERROR: {
        "subsystem": "Quality / Adaptive Preprocessing",
        "manifestation": "Over-filtering, thresholding wash-out, distortion",
        "action": "Adjust quality thresholds and selective enhancement profiles",
    },
    OCRErrorCategory.LAYOUT_ERROR: {
        "subsystem": "LayoutLMv3 / Spatial Router",
        "manifestation": "Multi-column words interleaved in wrong reading order",
        "action": "Fine-tune LayoutLMv3 on multi-column lab reports & hospital bills (training/layout/)",
    },
    OCRErrorCategory.TABLE_ERROR: {
        "subsystem": "TableExtractor",
        "manifestation": "Cell boundaries misaligned, row-spanning values merged",
        "action": "Calibrate morphological kernel sizes and ruling line detection",
    },
    OCRErrorCategory.NUMERIC_ERROR: {
        "subsystem": "MedicalOCRCorrector & Fusion",
        "manifestation": "Dropped decimal points (13.2 -> 132), misread dosage units",
        "action": "Tighten numeric plausibility ranges and fusion penalty weights",
    },
    OCRErrorCategory.NORMALIZATION_ERROR: {
        "subsystem": "Lexicon / Normalizer",
        "manifestation": "Legitimate drug/test name rejected by lexicon lookup",
        "action": "Expand standard vocabulary (lexicon.csv & SNOMED/RxNorm maps)",
    },
}


@dataclass
class CategorizedError:
    category: OCRErrorCategory
    reference: str
    hypothesis: str
    reason: str
    field_name: str | None = None
    subsystem: str = ""
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "reference": self.reference,
            "hypothesis": self.hypothesis,
            "reason": self.reason,
            "field_name": self.field_name,
            "subsystem": self.subsystem,
            "suggested_action": self.suggested_action,
        }


def categorize_ocr_discrepancy(
    reference: str,
    hypothesis: str,
    context: dict[str, Any] | None = None,
) -> CategorizedError:
    """Classify an OCR/extraction discrepancy into the 8-part medical error taxonomy."""
    ctx = context or {}
    field_name = ctx.get("field_name")
    ref_s = str(reference).strip()
    hyp_s = str(hypothesis).strip()

    # 1. Detection error: text in ground truth completely missed / truncated
    if ref_s and not hyp_s:
        cat = OCRErrorCategory.DETECTION_ERROR
        reason = "Hypothesis is empty; text line or bounding box was omitted"
    elif ctx.get("is_truncated", False) or ctx.get("is_detection_miss", False):
        cat = OCRErrorCategory.DETECTION_ERROR
        reason = "Bounding box truncated or boundary clipped text"

    # 2. Layout error: reading order or multi-column interleave
    elif ctx.get("is_layout", False) or ctx.get("order_inverted", False):
        cat = OCRErrorCategory.LAYOUT_ERROR
        reason = "Reading order permutation or multi-column block interleave"

    # 3. Table error: cell alignment or tabular coordinate issue
    elif ctx.get("is_table", False) or (field_name and "table" in field_name.lower()):
        cat = OCRErrorCategory.TABLE_ERROR
        reason = "Table structure, cell boundary, or row alignment discrepancy"

    # 4. Handwriting error: handwriting crop or cursive style
    elif ctx.get("is_handwritten", False):
        cat = OCRErrorCategory.HANDWRITING_ERROR
        reason = "Handwritten cursive script transcription mismatch"

    # 5. Preprocessing error: contrast wash-out, binarization artifact
    elif ctx.get("is_preprocessing", False):
        cat = OCRErrorCategory.PREPROCESSING_ERROR
        reason = "Distortion or over-filtering during binarization/enhancement"

    # 6. Numeric error: decimal dropped (13.2 -> 132), number mangled
    elif (
        ctx.get("is_numeric", False)
        or (field_name and any(k in field_name.lower() for k in ("dose", "dosage", "value", "result", "count", "num")))
        or any(char.isdigit() for char in ref_s)
    ):
        ref_digits = re.sub(r"[^\d.]", "", ref_s)
        hyp_digits = re.sub(r"[^\d.]", "", hyp_s)
        # Check decimal drop specifically
        if "." in ref_digits and "." not in hyp_digits and ref_digits.replace(".", "") == hyp_digits:
            cat = OCRErrorCategory.NUMERIC_ERROR
            reason = f"Dropped decimal point: '{ref_s}' became '{hyp_s}'"
        elif ref_digits != hyp_digits:
            cat = OCRErrorCategory.NUMERIC_ERROR
            reason = f"Numeric value mismatch: '{ref_s}' vs '{hyp_s}'"
        else:
            cat = OCRErrorCategory.RECOGNITION_ERROR
            reason = f"Unit or non-numeric affix mismatch in numeric field: '{ref_s}' vs '{hyp_s}'"

    # 7. Normalization error: valid drug/test name rejected or bad alias
    elif (
        ctx.get("is_medication", False)
        or (field_name and any(k in field_name.lower() for k in ("drug", "med", "medicine", "rx")))
        or ctx.get("normalization_failed", False)
    ):
        # If edit distance is very small (1-2 chars), it could be normalization vocabulary gap
        if levenshtein_distance(ref_s.lower(), hyp_s.lower()) <= 2:
            cat = OCRErrorCategory.NORMALIZATION_ERROR
            reason = f"Clinical terminology normalization / alias mismatch: '{ref_s}' vs '{hyp_s}'"
        else:
            cat = OCRErrorCategory.RECOGNITION_ERROR
            reason = f"Severe recognition error in medication field: '{ref_s}' vs '{hyp_s}'"

    # 8. Printed recognition error: standard typography character confusion
    else:
        cat = OCRErrorCategory.RECOGNITION_ERROR
        reason = f"Printed typography character confusion: '{ref_s}' vs '{hyp_s}'"

    meta = ERROR_TAXONOMY_METADATA.get(cat, {})
    return CategorizedError(
        category=cat,
        reference=ref_s,
        hypothesis=hyp_s,
        reason=reason,
        field_name=field_name,
        subsystem=meta.get("subsystem", ""),
        suggested_action=meta.get("action", ""),
    )


def calculate_category_metrics(eval_items: list[dict[str, Any]]) -> dict[str, float]:
    """Calculate fine-grained category metrics:
    - printed_cer: CER over printed text lines
    - handwriting_cer: CER over handwritten text lines
    - numeric_accuracy: Accuracy for numeric extractions (0.0 - 1.0)
    - medication_accuracy: Accuracy for medication extractions (0.0 - 1.0)
    - table_accuracy: Accuracy for tabular cell extractions (0.0 - 1.0)
    """
    printed_refs: list[str] = []
    printed_hyps: list[str] = []
    hw_refs: list[str] = []
    hw_hyps: list[str] = []

    num_total = 0
    num_correct = 0

    med_total = 0
    med_correct = 0

    table_total = 0
    table_correct = 0

    for item in eval_items:
        ref = str(item.get("reference", ""))
        hyp = str(item.get("hypothesis", ""))
        is_hw = item.get("is_handwritten", False)
        is_num = item.get("is_numeric", False)
        is_med = item.get("is_medication", False)
        is_table = item.get("is_table", False)

        if is_hw:
            hw_refs.append(ref)
            hw_hyps.append(hyp)
        else:
            printed_refs.append(ref)
            printed_hyps.append(hyp)

        if is_num:
            num_total += 1
            # Exact or clean numeric comparison
            ref_clean = re.sub(r"[^\d.]", "", ref)
            hyp_clean = re.sub(r"[^\d.]", "", hyp)
            if ref_clean and ref_clean == hyp_clean:
                num_correct += 1

        if is_med:
            med_total += 1
            if ref.strip().lower() == hyp.strip().lower():
                med_correct += 1

        if is_table:
            table_total += 1
            if ref.strip().lower() == hyp.strip().lower():
                table_correct += 1

    printed_cer = (
        calculate_cer(" ".join(printed_refs), " ".join(printed_hyps))
        if printed_refs
        else 0.0
    )
    hw_cer = (
        calculate_cer(" ".join(hw_refs), " ".join(hw_hyps))
        if hw_refs
        else 0.0
    )

    return {
        "printed_cer": round(printed_cer, 4),
        "handwriting_cer": round(hw_cer, 4),
        "numeric_accuracy": round(num_correct / num_total, 4) if num_total > 0 else 1.0,
        "medication_accuracy": round(med_correct / med_total, 4) if med_total > 0 else 1.0,
        "table_accuracy": round(table_correct / table_total, 4) if table_total > 0 else 1.0,
    }


@dataclass
class FieldMetric:
    name: str
    ground_truth: Any
    extracted: Any
    is_match: bool
    error: CategorizedError | None = None


@dataclass
class BenchmarkEvaluationResult:
    cer: float
    wer: float
    field_metrics: list[FieldMetric] = field(default_factory=list)
    field_accuracy: float = 0.0
    error_breakdown: dict[str, int] = field(default_factory=dict)
    category_metrics: dict[str, float] = field(default_factory=dict)
    categorized_errors: list[CategorizedError] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "cer": round(self.cer, 4),
            "wer": round(self.wer, 4),
            "field_accuracy": round(self.field_accuracy, 4),
            "total_fields": len(self.field_metrics),
            "matched_fields": sum(1 for f in self.field_metrics if f.is_match),
            "error_breakdown": self.error_breakdown,
            "category_metrics": self.category_metrics,
        }


def evaluate_structured_fields(
    ground_truth: dict[str, Any],
    extracted: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> BenchmarkEvaluationResult:
    """Compare key-value ground truth fields against extracted values and categorize errors."""
    metrics: list[FieldMetric] = []
    categorized_errors: list[CategorizedError] = []
    error_counts: dict[str, int] = {cat.value: 0 for cat in OCRErrorCategory}
    eval_items: list[dict[str, Any]] = []

    ctx = context or {}

    for key, gt_val in ground_truth.items():
        ext_val = extracted.get(key)
        # Normalize comparisons for strings
        if isinstance(gt_val, str) and isinstance(ext_val, str):
            match = gt_val.strip().lower() == ext_val.strip().lower()
        else:
            match = gt_val == ext_val

        cat_error: CategorizedError | None = None
        if not match:
            field_ctx = dict(ctx)
            field_ctx["field_name"] = key
            if any(k in key.lower() for k in ("dose", "freq", "date", "age", "num", "count", "val")):
                field_ctx["is_numeric"] = True
            if any(k in key.lower() for k in ("drug", "med", "medicine")):
                field_ctx["is_medication"] = True
            if "table" in key.lower():
                field_ctx["is_table"] = True

            cat_error = categorize_ocr_discrepancy(str(gt_val), str(ext_val or ""), field_ctx)
            categorized_errors.append(cat_error)
            error_counts[cat_error.category.value] += 1

        metrics.append(
            FieldMetric(
                name=key,
                ground_truth=gt_val,
                extracted=ext_val,
                is_match=match,
                error=cat_error,
            )
        )

        eval_items.append({
            "reference": str(gt_val),
            "hypothesis": str(ext_val or ""),
            "is_handwritten": ctx.get("is_handwritten", False),
            "is_numeric": any(k in key.lower() for k in ("dose", "val", "age", "num", "count")),
            "is_medication": any(k in key.lower() for k in ("drug", "med", "medicine")),
            "is_table": "table" in key.lower(),
        })

    matched = sum(1 for m in metrics if m.is_match)
    accuracy = matched / len(metrics) if metrics else 1.0

    gt_all = " ".join(str(v) for v in ground_truth.values())
    ext_all = " ".join(str(extracted.get(k, "")) for k in ground_truth)
    overall_cer = calculate_cer(gt_all, ext_all)
    overall_wer = calculate_wer(gt_all, ext_all)

    cat_metrics = calculate_category_metrics(eval_items)

    return BenchmarkEvaluationResult(
        cer=overall_cer,
        wer=overall_wer,
        field_metrics=metrics,
        field_accuracy=accuracy,
        error_breakdown=error_counts,
        category_metrics=cat_metrics,
        categorized_errors=categorized_errors,
    )


class OCRBenchmarkEvaluator:
    """Unified benchmark evaluator for OCR character, word, field, and taxonomy evaluations."""

    def compute_cer(self, reference: str, hypothesis: str) -> float:
        return calculate_cer(reference, hypothesis)

    def compute_wer(self, reference: str, hypothesis: str) -> float:
        return calculate_wer(reference, hypothesis)

    def categorize_error(
        self, reference: str, hypothesis: str, context: dict[str, Any] | None = None
    ) -> CategorizedError:
        return categorize_ocr_discrepancy(reference, hypothesis, context)

    def evaluate_fields(
        self,
        ground_truth: dict[str, Any],
        extracted: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> BenchmarkEvaluationResult:
        return evaluate_structured_fields(ground_truth, extracted, context)

    def evaluate_categories(self, eval_items: list[dict[str, Any]]) -> dict[str, float]:
        return calculate_category_metrics(eval_items)

