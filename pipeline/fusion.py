"""Dedicated OCR Fusion module for candidate arbitration and alignment.

Evaluates candidates from multiple models (PP-OCR, TrOCR) and preprocessing passes
using confidence, text agreement, medical vocabulary presence, and numeric consistency.
Raw OCR text is strictly preserved.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ocr.medical_corrector import MedicalOCRCorrector, get_corrector
from pipeline.schemas.canonical import BoundingBox, OCRResult

log = logging.getLogger(__name__)


@dataclass
class OCRCandidate:
    """A recognition candidate from a specific model and preprocessing pass."""

    text: str
    confidence: float
    model: str
    preprocessing: str
    is_handwritten: bool = False
    bbox: BoundingBox | None = None
    line_no: int = 1


@dataclass
class FusedResult:
    """The result of candidate fusion with audit traceability."""

    best_text: str
    confidence: float
    winning_model: str
    winning_preprocessing: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    agreement_score: float = 0.0
    medical_boost: float = 0.0
    numeric_consistent: bool = True

    @property
    def text(self) -> str:
        return self.best_text

    @property
    def model(self) -> str:
        return self.winning_model

    @property
    def preprocessing(self) -> str:
        return self.winning_preprocessing


class OCRFusionEngine:
    """Arbitrates between multiple OCR candidates to select the highest-integrity medical reading."""

    def __init__(self, corrector: MedicalOCRCorrector | None = None) -> None:
        self.corrector = corrector or get_corrector()

    def fuse_candidates(self, candidates: list[OCRCandidate]) -> FusedResult:
        """Score and select the optimal candidate among model/preprocessing variants."""
        if not candidates:
            return FusedResult(
                best_text="",
                confidence=0.0,
                winning_model="none",
                winning_preprocessing="none",
            )

        if len(candidates) == 1:
            c = candidates[0]
            return FusedResult(
                best_text=c.text,
                confidence=c.confidence,
                winning_model=c.model,
                winning_preprocessing=c.preprocessing,
                candidates=[{
                    "text": c.text,
                    "confidence": round(c.confidence, 3),
                    "model": c.model,
                    "preprocessing": c.preprocessing,
                }],
                agreement_score=1.0,
            )

        scored_candidates: list[tuple[float, OCRCandidate, dict[str, float]]] = []

        for cand in candidates:
            text = cand.text.strip()
            score = cand.confidence * 0.50  # 50% base from model confidence

            # 1. Alphanumeric & punctuation cleanliness (15%)
            if text:
                alnum_ratio = sum(ch.isalnum() for ch in text) / len(text)
                score += alnum_ratio * 0.15

            # 2. Medical Vocabulary validation (20%)
            med_res = self.corrector.correct_line(text)
            med_boost = 0.20 if med_res.was_modified or self._contains_medical_term(text) else 0.0
            score += med_boost

            # 3. Numeric Consistency & Plausibility (15%)
            num_consistent = self._is_numeric_plausible(text)
            num_score = 0.15 if num_consistent else -0.15
            score += num_score

            scored_candidates.append((
                score,
                cand,
                {"base": cand.confidence, "med_boost": med_boost, "numeric": num_score},
            ))

        # Sort descending by composite score
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_cand, breakdown = scored_candidates[0]

        # Calculate text agreement between candidates
        agreement = self._calculate_agreement([c.text for c in candidates])

        cand_records = [
            {
                "text": c.text,
                "confidence": round(c.confidence, 3),
                "model": c.model,
                "preprocessing": c.preprocessing,
                "composite_score": round(sc, 3),
            }
            for sc, c, _ in scored_candidates
        ]

        log.debug(
            "Fusion selected: '%s' (model=%s, prep=%s, score=%.2f, agreement=%.2f)",
            best_cand.text,
            best_cand.model,
            best_cand.preprocessing,
            best_score,
            agreement,
        )

        return FusedResult(
            best_text=best_cand.text,
            confidence=round(best_cand.confidence, 3),
            winning_model=best_cand.model,
            winning_preprocessing=best_cand.preprocessing,
            candidates=cand_records,
            agreement_score=round(agreement, 3),
            medical_boost=breakdown["med_boost"],
            numeric_consistent=bool(breakdown["numeric"] > 0),
        )

    def _contains_medical_term(self, text: str) -> bool:
        """Check if candidate text contains recognized clinical or drug vocabulary."""
        text_lower = text.lower()
        known_terms = [
            "mg", "mcg", "tab", "cap", "inj", "syp", "after food", "before food",
            "amox", "paracetamol", "pantop", "zerodol", "microdox", "lacne", "perobar",
            "glucose", "hemoglobin", "hba1c", "creatinine", "urea", "potassium", "sodium",
        ]
        return any(term in text_lower for term in known_terms)

    def _is_numeric_plausible(self, text: str) -> bool:
        """Verify numeric tokens do not exhibit common OCR dropped-decimal pathologies."""
        try:
            val_results = self.corrector.validate_numerics(text)
            if any(r.validation == "suspicious" for r in val_results):
                return False
        except Exception:
            pass

        numbers = re.findall(r"\b\d+(?:\.\d+)?\b", text)
        for num_str in numbers:
            try:
                val = float(num_str)
                # Flag extreme unscaled integers where decimals are standard
                if val > 50 and "." not in num_str and "g/dl" in text.lower():
                    return False
                if val > 1000 and "." not in num_str and any(unit in text.lower() for unit in ["mg/dl", "g/dl"]):
                    return False
            except ValueError:
                pass
        return True

    def _calculate_agreement(self, texts: list[str]) -> float:
        """Calculate pairwise Jaccard/Levenshtein agreement across candidates."""
        if len(texts) < 2:
            return 1.0
        from tests.ocr_benchmark.evaluator import OCRBenchmarkEvaluator
        evaluator = OCRBenchmarkEvaluator()
        pairs_scores = []
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                cer = evaluator.compute_cer(texts[i], texts[j])
                pairs_scores.append(max(0.0, 1.0 - cer))
        return sum(pairs_scores) / len(pairs_scores) if pairs_scores else 1.0
