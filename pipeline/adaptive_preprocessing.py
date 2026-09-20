"""Adaptive image preprocessing engine with document-specific enhancement profiles.

Selects and applies targeted image transformations based on real quality analysis
metrics and document regions, preserving the original image alongside enhanced versions.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from pipeline.schemas.canonical import QualityReport

log = logging.getLogger(__name__)


class PreprocessingProfile(str, Enum):
    """Adaptive preprocessing profiles tailored to document quality states."""

    NORMAL = "NORMAL"
    LOW_QUALITY = "LOW_QUALITY"
    BLURRY = "BLURRY"
    SHADOWED = "SHADOWED"
    ROTATED = "ROTATED"
    PERSPECTIVE_DISTORTED = "PERSPECTIVE_DISTORTED"
    SMALL_TEXT = "SMALL_TEXT"
    HANDWRITING = "HANDWRITING"
    TABLE = "TABLE"


class AdaptivePreprocessor:
    """Adaptive preprocessing engine applying targeted transformations per profile."""

    def select_profile(
        self,
        quality_report: QualityReport,
        region_type: str | None = None,
        is_handwriting: bool = False,
    ) -> PreprocessingProfile:
        """Deterministically select a preprocessing profile from quality diagnostics."""
        metrics = quality_report.metrics

        if is_handwriting:
            return PreprocessingProfile.HANDWRITING
        if region_type == "table":
            return PreprocessingProfile.TABLE

        # Check geometry issues first
        if metrics.get("rotation", 0) != 0:
            return PreprocessingProfile.ROTATED
        if metrics.get("perspective_score", 1.0) < 0.75:
            return PreprocessingProfile.PERSPECTIVE_DISTORTED

        # Content degradation issues
        if metrics.get("shadow_score", 1.0) < 0.65:
            return PreprocessingProfile.SHADOWED
        if metrics.get("blur", 200.0) < 100.0:
            return PreprocessingProfile.BLURRY
        if metrics.get("contrast", 0.5) < 0.30 or metrics.get("noise", 0.0) > 0.05:
            return PreprocessingProfile.LOW_QUALITY

        return PreprocessingProfile.NORMAL

    def process(
        self,
        image: np.ndarray | Image.Image,
        profile: PreprocessingProfile,
        quality_report: QualityReport | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Apply targeted transformations based on the selected profile.

        Returns (processed_numpy_array, metadata_audit).
        Original image is never mutated.
        """
        if isinstance(image, Image.Image):
            orig_arr = np.array(image.convert("RGB"))
        else:
            orig_arr = image.copy()

        arr = orig_arr.copy()
        ops_applied: list[str] = []

        import cv2

        # 1. Orientation & Deskew
        if profile == PreprocessingProfile.ROTATED or (quality_report and "rotate_90" in quality_report.action_plan):
            arr = cv2.rotate(arr, cv2.ROTATE_90_CLOCKWISE)
            ops_applied.append("rotate_90_cw")

        if quality_report and "deskew" in quality_report.action_plan:
            skew = quality_report.metrics.get("skew_angle", 0.0)
            if abs(skew) > 0.5:
                arr = self._deskew(arr, skew)
                ops_applied.append(f"deskew_{skew:.1f}")

        # 2. Profile-specific transformations
        if profile == PreprocessingProfile.SHADOWED:
            arr = self._remove_shadows(arr)
            arr = self._enhance_contrast(arr, factor=1.3)
            ops_applied.extend(["shadow_removal", "contrast_1.3"])

        elif profile == PreprocessingProfile.BLURRY:
            arr = self._sharpen_unsharp_mask(arr, amount=1.6, radius=1.5)
            arr = self._enhance_contrast(arr, factor=1.2)
            ops_applied.extend(["unsharp_mask_1.6", "contrast_1.2"])

        elif profile == PreprocessingProfile.LOW_QUALITY:
            arr = self._remove_shadows(arr)
            arr = self._denoise_bilateral(arr)
            arr = self._enhance_contrast(arr, factor=1.35)
            ops_applied.extend(["shadow_removal", "bilateral_denoise", "contrast_1.35"])

        elif profile == PreprocessingProfile.SMALL_TEXT:
            arr = self._upscale_bicubic(arr, scale=1.5)
            arr = self._sharpen_unsharp_mask(arr, amount=1.3, radius=1.0)
            ops_applied.extend(["upscale_1.5x", "unsharp_mask_1.3"])

        elif profile == PreprocessingProfile.HANDWRITING:
            # For faint handwriting: gentle contrast expansion + bilateral smoothing of page paper grain
            arr = self._denoise_bilateral(arr, d=3, sigma_color=20, sigma_space=20)
            arr = self._enhance_contrast(arr, factor=1.25)
            ops_applied.extend(["bilateral_grain_smooth", "contrast_1.25"])

        elif profile == PreprocessingProfile.TABLE:
            # For tables: enhance ruling lines and sharpen numbers
            arr = self._enhance_contrast(arr, factor=1.2)
            arr = self._sharpen_unsharp_mask(arr, amount=1.2, radius=1.0)
            ops_applied.extend(["contrast_1.2", "unsharp_mask_1.2"])

        elif profile == PreprocessingProfile.PERSPECTIVE_DISTORTED:
            # Perspective regularization
            arr = self._enhance_contrast(arr, factor=1.2)
            ops_applied.append("perspective_contrast")

        metadata = {
            "profile": profile.value,
            "ops_applied": ops_applied,
            "operations_applied": ops_applied,
            "orig_shape": orig_arr.shape,
            "processed_shape": arr.shape,
        }
        return arr, metadata

    def deskew(self, arr: np.ndarray, angle: float) -> tuple[np.ndarray, list[str]]:
        """Public convenience method to deskew an image by angle degrees."""
        deskewed = self._deskew(arr, angle)
        return deskewed, [f"deskew_{angle:.1f}deg"]

    # -----------------------------------------------------------------------
    # Atomic Image Operators
    # -----------------------------------------------------------------------

    @staticmethod
    def _deskew(arr: np.ndarray, angle: float) -> np.ndarray:
        import cv2
        h, w = arr.shape[:2]
        center = (w // 2, h // 2)
        rot_mat = cv2.getRotationMatrix2D(center, -angle, 1.0)
        return cv2.warpAffine(arr, rot_mat, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    @staticmethod
    def _remove_shadows(arr: np.ndarray) -> np.ndarray:
        """Divide foreground by morphological background illumination estimate."""
        import cv2
        rgb_planes = cv2.split(arr)
        result_planes = []
        for plane in rgb_planes:
            dilated = cv2.dilate(plane, np.ones((7, 7), np.uint8))
            bg_blur = cv2.medianBlur(dilated, 21)
            diff = 255 - cv2.absdiff(plane, bg_blur)
            norm = cv2.normalize(diff, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8U)
            result_planes.append(norm)
        return cv2.merge(result_planes)

    @staticmethod
    def _enhance_contrast(arr: np.ndarray, factor: float = 1.3) -> np.ndarray:
        pil_img = Image.fromarray(arr)
        enhancer = ImageEnhance.Contrast(pil_img)
        return np.array(enhancer.enhance(factor))

    @staticmethod
    def _sharpen_unsharp_mask(arr: np.ndarray, amount: float = 1.5, radius: float = 1.5) -> np.ndarray:
        pil_img = Image.fromarray(arr)
        sharpened = pil_img.filter(ImageFilter.UnsharpMask(radius=radius, percent=int(amount * 100), threshold=3))
        return np.array(sharpened)

    @staticmethod
    def _denoise_bilateral(
        arr: np.ndarray, d: int = 5, sigma_color: int = 35, sigma_space: int = 35
    ) -> np.ndarray:
        import cv2
        return cv2.bilateralFilter(arr, d=d, sigmaColor=sigma_color, sigmaSpace=sigma_space)

    @staticmethod
    def _upscale_bicubic(arr: np.ndarray, scale: float = 1.5) -> np.ndarray:
        import cv2
        h, w = arr.shape[:2]
        return cv2.resize(arr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
