"""ROI Router for Printed vs. Handwritten classification, low-confidence fusion, and special region handling."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from config.loader import PolicyConfig, RoutingPolicy
from pipeline.schemas.canonical import DocumentRegion, OCRResult, RegionClass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutingDecision:
    target_engine: str  # "ppocr" | "trocr" | "fusion" | "barcode" | "table" | "metadata"
    routing_confidence: float
    is_special_region: bool = False
    reason: str = "normal"


class ROIRouter:
    """Classifies document ROIs and routes them to PP-OCR, TrOCR, Fusion, or special decoders."""

    def __init__(self, policy: RoutingPolicy | None = None) -> None:
        if policy is not None:
            self.policy = policy
        else:
            try:
                self.policy = PolicyConfig.load().routing
            except Exception:
                self.policy = RoutingPolicy()

    def route(self, region: DocumentRegion, page_image: np.ndarray | Image.Image) -> RoutingDecision:
        """Decide the processing path for a detected region."""
        # 1. Special Region Routing (Phase 18)
        cls_name = region.class_name.lower()
        if cls_name == RegionClass.BARCODE.value:
            return RoutingDecision(target_engine="barcode", routing_confidence=1.0, is_special_region=True)
        if cls_name == RegionClass.TABLE.value:
            return RoutingDecision(target_engine="table", routing_confidence=0.95, is_special_region=True)
        if cls_name in (RegionClass.SIGNATURE.value, RegionClass.STAMP.value):
            return RoutingDecision(
                target_engine="metadata",
                routing_confidence=0.90,
                is_special_region=True,
                reason=f"non_text_{cls_name}",
            )

        # 2. Printed vs. Handwritten Analysis on Region Crop (Phase 14)
        crop = region.bbox.crop_from(page_image)
        mode, routing_conf = self.classify_handwriting(crop)

        # 3. Low-Confidence Fallback / Fusion Evaluation (Phase 17)
        if routing_conf < self.policy.fusion_threshold:
            log.info(
                "Region %s routing confidence (%.2f) below fusion threshold (%.2f). Routing to FUSION.",
                region.region_id,
                routing_conf,
                self.policy.fusion_threshold,
            )
            return RoutingDecision(
                target_engine="fusion",
                routing_confidence=routing_conf,
                is_special_region=False,
                reason="low_routing_confidence",
            )

        target = "trocr" if mode == "handwritten" else "ppocr"
        return RoutingDecision(
            target_engine=target,
            routing_confidence=routing_conf,
            is_special_region=False,
            reason=f"classified_{mode}",
        )

    def classify_handwriting(self, crop: np.ndarray | Image.Image) -> tuple[str, float]:
        """Classify whether a crop contains printed or handwritten text based on stroke curvature and gradient entropy."""
        import cv2

        if isinstance(crop, Image.Image):
            arr = np.array(crop.convert("RGB"))
        else:
            arr = crop

        if arr.size == 0 or arr.shape[0] < 8 or arr.shape[1] < 8:
            return "printed", 1.0

        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if len(arr.shape) == 3 else arr

        # Compute gradient orientation entropy
        sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        angles = np.arctan2(sobel_y, sobel_x) * 180 / np.pi

        # Filter out flat areas
        magnitudes = np.hypot(sobel_x, sobel_y)
        edge_mask = magnitudes > 30.0
        edge_angles = angles[edge_mask]

        if len(edge_angles) < 20:
            return "printed", 0.90

        # Printed text has strong peaks at 0, 90, 180, -90, -180 degrees (rectilinear vertical/horizontal strokes)
        near_ortho = np.abs(np.abs(edge_angles) - 90.0) < 15.0
        ortho_ratio = float(np.sum(near_ortho) / len(edge_angles))

        # High orthogonal stroke ratio indicates machine-printed typography
        if ortho_ratio >= self.policy.handwriting_threshold:
            confidence = min(1.0, 0.5 + (ortho_ratio - self.policy.handwriting_threshold) * 1.5)
            return "printed", round(confidence, 3)
        else:
            confidence = min(1.0, 0.5 + (self.policy.handwriting_threshold - ortho_ratio) * 1.5)
            return "handwritten", round(confidence, 3)

    def fuse(
        self,
        ppocr_results: list[OCRResult],
        trocr_results: list[OCRResult],
        decision: RoutingDecision,
    ) -> list[OCRResult]:
        """Deterministic fusion policy comparing candidate results from PP-OCR and TrOCR."""
        pp_text = " ".join(r.text for r in ppocr_results).strip()
        tr_text = " ".join(r.text for r in trocr_results).strip()

        pp_conf = (
            sum(r.confidence for r in ppocr_results) / len(ppocr_results)
            if ppocr_results
            else 0.0
        )
        tr_conf = (
            sum(r.confidence for r in trocr_results) / len(trocr_results)
            if trocr_results
            else 0.0
        )

        # Heuristic 1: Valid character density (alphanumeric vs punctuation ratio)
        pp_alnum_ratio = sum(c.isalnum() for c in pp_text) / max(1, len(pp_text))
        tr_alnum_ratio = sum(c.isalnum() for c in tr_text) / max(1, len(tr_text))

        # Weighting score: 0.6 * confidence + 0.4 * valid_ratio
        pp_score = (0.6 * pp_conf) + (0.4 * pp_alnum_ratio)
        tr_score = (0.6 * tr_conf) + (0.4 * tr_alnum_ratio)

        if pp_score >= tr_score:
            winner = ppocr_results
            winner_name = "ppocr"
        else:
            winner = trocr_results
            winner_name = "trocr"

        log.info(
            "FUSION RESOLVED: routing_conf=%.2f, pp_score=%.2f, tr_score=%.2f -> selected %s",
            decision.routing_confidence,
            pp_score,
            tr_score,
            winner_name,
        )

        # Mark results as produced via fusion
        return [
            OCRResult(
                text=r.text,
                confidence=r.confidence,
                bbox=r.bbox,
                page=r.page,
                line_no=r.line_no,
                region_id=r.region_id,
                model="fusion",
                model_version=f"{r.model}-{r.model_version}",
                is_handwritten=r.is_handwritten,
                is_reliable=r.is_reliable,
            )
            for r in winner
        ]
