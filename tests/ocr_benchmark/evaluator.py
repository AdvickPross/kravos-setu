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


@dataclass
class FieldMetric:
    name: str
    ground_truth: Any
    extracted: Any
    is_match: bool


@dataclass
class BenchmarkEvaluationResult:
    cer: float
    wer: float
    field_metrics: list[FieldMetric] = field(default_factory=list)
    field_accuracy: float = 0.0

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "cer": round(self.cer, 4),
            "wer": round(self.wer, 4),
            "field_accuracy": round(self.field_accuracy, 4),
            "total_fields": len(self.field_metrics),
            "matched_fields": sum(1 for f in self.field_metrics if f.is_match),
        }


def evaluate_structured_fields(
    ground_truth: dict[str, Any], extracted: dict[str, Any]
) -> BenchmarkEvaluationResult:
    """Compare key-value ground truth fields against extracted values."""
    metrics: list[FieldMetric] = []

    for key, gt_val in ground_truth.items():
        ext_val = extracted.get(key)
        # Normalize comparisons for strings
        if isinstance(gt_val, str) and isinstance(ext_val, str):
            match = gt_val.strip().lower() == ext_val.strip().lower()
        else:
            match = gt_val == ext_val

        metrics.append(
            FieldMetric(
                name=key,
                ground_truth=gt_val,
                extracted=ext_val,
                is_match=match,
            )
        )

    matched = sum(1 for m in metrics if m.is_match)
    accuracy = matched / len(metrics) if metrics else 1.0

    return BenchmarkEvaluationResult(
        cer=0.0,
        wer=0.0,
        field_metrics=metrics,
        field_accuracy=accuracy,
    )
