"""Conditional neural and photographic enhancement stage."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from PIL import Image

from models.manager import ModelManager
from pipeline.schemas.canonical import QualityReport

log = logging.getLogger(__name__)


class EnhancementStage:
    """Conditional enhancement stage driven strictly by QualityReport action plan."""

    def __init__(self, model_manager: ModelManager) -> None:
        self.model_manager = model_manager

    def process(
        self, image: np.ndarray | Image.Image, quality_report: QualityReport
    ) -> tuple[np.ndarray, bool]:
        """Apply deskewing and Restormer restoration if action plan requires it.

        Returns (processed_image_as_numpy, was_enhanced_boolean).
        """
        if isinstance(image, Image.Image):
            arr = np.array(image.convert("RGB"))
        else:
            arr = image.copy()

        # Step 1: Geometry fix (deskew) if identified
        if "deskew" in quality_report.action_plan:
            skew_angle = quality_report.checks.get("skew", None)
            angle = skew_angle.score if skew_angle else 0.0
            if abs(angle) > 0.5:
                arr = self._deskew_image(arr, angle)
                log.info("Deskew applied: rotated by %.2f degrees", -angle)

        # Step 2: Conditional Restormer Neural / Photographic Enhancement
        if not quality_report.enhancement_required:
            log.info("Page quality passed thresholds. Skipping Restormer enhancement pass.")
            return arr, False

        log.info(
            "Page quality failed checks (%s). Triggering Restormer enhancement...",
            quality_report.action_plan,
        )
        restormer = self.model_manager.get_model("restormer")
        enhanced = restormer.predict(arr)
        return enhanced, True

    def _deskew_image(self, arr: np.ndarray, angle: float) -> np.ndarray:
        """Rotate image array around center by -angle to correct skew."""
        import cv2

        h, w = arr.shape[:2]
        center = (w // 2, h // 2)
        rot_mat = cv2.getRotationMatrix2D(center, -angle, 1.0)
        return cv2.warpAffine(
            arr, rot_mat, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
        )
