"""Page-level classical computer vision quality assessment engine."""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
from PIL import Image

from config.loader import PolicyConfig, QualityPolicy
from pipeline.schemas.canonical import QualityCheckResult, QualityReport

log = logging.getLogger(__name__)


class QualityAssessmentEngine:
    """Fast, CPU-resident quality triage engine producing diagnostic scores and action plans."""

    def __init__(self, policy: QualityPolicy | None = None) -> None:
        if policy is not None:
            self.policy = policy
        else:
            try:
                self.policy = PolicyConfig.load().quality
            except Exception:
                self.policy = QualityPolicy()

    def assess_page(self, image: np.ndarray | Image.Image, page: int = 1) -> QualityReport:
        """Run all near-zero-cost classical CV checks on a page."""
        start_t = time.perf_counter()

        if isinstance(image, Image.Image):
            arr = np.array(image.convert("RGB"))
        elif isinstance(image, np.ndarray):
            arr = image
        else:
            raise TypeError(f"Expected PIL Image or numpy array, got {type(image)}")

        import cv2

        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if len(arr.shape) == 3 else arr

        checks: dict[str, QualityCheckResult] = {}

        # 1. Blur Check (Laplacian variance)
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        blur_passed = lap_var >= self.policy.blur_threshold
        checks["blur"] = QualityCheckResult(
            name="blur",
            score=round(lap_var, 2),
            passed=blur_passed,
            details={"threshold": self.policy.blur_threshold, "metric": "laplacian_variance"},
        )

        # 2. Skew Check (Hough Transform)
        skew_angle = self._estimate_skew_angle(gray)
        skew_passed = abs(skew_angle) <= self.policy.skew_threshold_degrees
        checks["skew"] = QualityCheckResult(
            name="skew",
            score=round(skew_angle, 2),
            passed=skew_passed,
            details={"threshold_degrees": self.policy.skew_threshold_degrees},
        )

        # 3. Brightness Check (Mean intensity)
        mean_brightness = float(np.mean(gray))
        brightness_passed = self.policy.brightness_min <= mean_brightness <= self.policy.brightness_max
        checks["brightness"] = QualityCheckResult(
            name="brightness",
            score=round(mean_brightness, 2),
            passed=brightness_passed,
            details={"min": self.policy.brightness_min, "max": self.policy.brightness_max},
        )

        # 4. Contrast Check (Michelson-like percentile spread)
        p5, p95 = np.percentile(gray, (5, 95))
        contrast_score = float((p95 - p5) / 255.0)
        contrast_passed = contrast_score >= self.policy.contrast_threshold
        checks["contrast"] = QualityCheckResult(
            name="contrast",
            score=round(contrast_score, 3),
            passed=contrast_passed,
            details={"threshold": self.policy.contrast_threshold},
        )

        # 5. Illumination Uniformity (Grid variance)
        illum_score = self._check_illumination_uniformity(gray)
        illum_passed = illum_score >= 0.70
        checks["illumination"] = QualityCheckResult(
            name="illumination",
            score=round(illum_score, 3),
            passed=illum_passed,
            details={"threshold": 0.70},
        )

        # 6. Color Cast (Gray-world deviation)
        color_cast_score = self._check_color_cast(arr)
        color_cast_passed = color_cast_score <= 0.15
        checks["color_cast"] = QualityCheckResult(
            name="color_cast",
            score=round(color_cast_score, 3),
            passed=color_cast_passed,
            details={"threshold": 0.15},
        )

        # 7. Speckle / Toner Noise Density
        speckle_density = self._check_speckle_density(gray)
        speckle_passed = speckle_density <= self.policy.noise_speckle_density_threshold
        checks["speckles"] = QualityCheckResult(
            name="speckles",
            score=round(speckle_density, 4),
            passed=speckle_passed,
            details={"threshold": self.policy.noise_speckle_density_threshold},
        )

        # 8. Shannon Entropy Check
        entropy_val = self._calculate_entropy(gray)
        entropy_passed = entropy_val >= 3.0  # Document images with text typically have entropy > 3.5
        checks["entropy"] = QualityCheckResult(
            name="entropy",
            score=round(entropy_val, 2),
            passed=entropy_passed,
            details={"threshold": 3.0},
        )

        # 9. Perspective & Shadow Checks
        perspective_score = self._estimate_perspective_distortion(gray)
        perspective_passed = perspective_score >= 0.80
        checks["perspective"] = QualityCheckResult(
            name="perspective",
            score=round(perspective_score, 2),
            passed=perspective_passed,
            details={"threshold": 0.80},
        )

        # 10. Rotation & Estimated DPI
        rotation_deg = self._detect_orientation_rotation(gray)
        estimated_dpi = self._estimate_dpi(gray)

        # 11. Blank Page Detection
        is_blank = self._check_blank_page(gray)

        # Collect issues list
        issues: list[str] = []
        if not blur_passed:
            issues.append(f"Blur detected (score={lap_var:.1f} < {self.policy.blur_threshold})")
        if not skew_passed:
            issues.append(f"Skew detected ({skew_angle:.1f}° > {self.policy.skew_threshold_degrees}°)")
        if not contrast_passed:
            issues.append(f"Low contrast (score={contrast_score:.2f} < {self.policy.contrast_threshold})")
        if not brightness_passed:
            issues.append(f"Suboptimal brightness (mean={mean_brightness:.1f})")
        if not illum_passed:
            issues.append(f"Uneven illumination / shadows (uniformity={illum_score:.2f})")
        if not speckle_passed:
            issues.append(f"High sensor speckle noise (density={speckle_density:.4f})")
        if not perspective_passed:
            issues.append(f"Perspective distortion detected (score={perspective_score:.2f})")
        if is_blank:
            issues.append("Page appears blank or empty")

        # Build Action Plan and Decision
        action_plan: list[str] = []
        if not skew_passed:
            action_plan.append("deskew")
        if rotation_deg != 0:
            action_plan.append(f"rotate_{rotation_deg}")
        if not perspective_passed:
            action_plan.append("perspective_correction")
        if not blur_passed:
            action_plan.append("restormer_deblur")
        if not contrast_passed or not brightness_passed:
            action_plan.append("contrast_normalization")
        if not illum_passed:
            action_plan.append("shadow_removal")
        if not speckle_passed:
            action_plan.append("restormer_denoise")

        # Enhancement required if any non-geometry content check failed (excluding paper tint/color_cast)
        enhancement_required = any(
            not c.passed for name, c in checks.items() if name not in ("skew", "color_cast", "perspective", "entropy")
        ) or not perspective_passed

        # Overall composite score (0.0 to 1.0)
        pass_ratio = sum(1 for c in checks.values() if c.passed) / len(checks)
        latency_ms = (time.perf_counter() - start_t) * 1000

        metrics_dict = {
            "blur": round(lap_var, 2),
            "contrast": round(contrast_score, 3),
            "brightness": round(mean_brightness, 2),
            "noise": round(speckle_density, 4),
            "entropy": round(entropy_val, 2),
            "dpi": estimated_dpi,
            "skew_angle": round(skew_angle, 2),
            "rotation": rotation_deg,
            "shadow_score": round(illum_score, 3),
            "perspective_score": round(perspective_score, 3),
            "blank_page": is_blank,
            "overall_quality": round(pass_ratio, 3),
            "issues": issues,
        }

        report = QualityReport(
            page=page,
            checks=checks,
            overall_score=round(pass_ratio, 3),
            enhancement_required=enhancement_required,
            action_plan=action_plan,
            latency_ms=round(latency_ms, 2),
            metrics=metrics_dict,
            issues=issues,
        )

        log.info(
            "Quality Assessment p%d: score=%.2f, enhancement_required=%s, actions=%s (%.1fms)",
            page,
            report.overall_score,
            report.enhancement_required,
            report.action_plan,
            report.latency_ms,
        )
        return report

    def _estimate_skew_angle(self, gray: np.ndarray) -> float:
        """Estimate skew angle in degrees using image moments or Radon/Hough lines."""
        import cv2

        # Small resized copy for speed
        h, w = gray.shape
        scale = min(1.0, 800.0 / max(h, w))
        small = cv2.resize(gray, (int(w * scale), int(h * scale))) if scale < 1.0 else gray

        edges = cv2.Canny(small, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80, minLineLength=50, maxLineGap=10)
        if lines is None or len(lines) == 0:
            return 0.0

        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            dx = x2 - x1
            dy = y2 - y1
            if dx == 0:
                continue
            angle = np.degrees(np.arctan2(dy, dx))
            # Text lines are near horizontal (-45 to 45 deg)
            if abs(angle) < 45.0:
                angles.append(angle)

        if not angles:
            return 0.0
        return float(np.median(angles))

    def _check_illumination_uniformity(self, gray: np.ndarray) -> float:
        """Divide into 4x4 grid and measure standard deviation of local means."""
        h, w = gray.shape
        grid_h, grid_w = h // 4, w // 4
        if grid_h <= 0 or grid_w <= 0:
            return 1.0
        means = [
            np.mean(gray[r * grid_h : (r + 1) * grid_h, c * grid_w : (c + 1) * grid_w])
            for r in range(4)
            for c in range(4)
        ]
        std_dev = np.std(means)
        # Score ranges from 1.0 (perfectly uniform) to 0.0 (high vignetting/shadow)
        return float(max(0.0, 1.0 - (std_dev / 100.0)))

    def _check_color_cast(self, arr: np.ndarray) -> float:
        """Measure difference between R, G, B channel means (gray-world assumption)."""
        if len(arr.shape) != 3 or arr.shape[2] < 3:
            return 0.0
        r_mean = np.mean(arr[:, :, 0])
        g_mean = np.mean(arr[:, :, 1])
        b_mean = np.mean(arr[:, :, 2])
        avg = (r_mean + g_mean + b_mean) / 3.0
        if avg == 0:
            return 0.0
        max_diff = max(abs(r_mean - avg), abs(g_mean - avg), abs(b_mean - avg))
        return float(max_diff / avg)

    def _check_speckle_density(self, gray: np.ndarray) -> float:
        """Measure density of isolated high-frequency components."""
        import cv2

        # Invert Otsu binary: foreground text & speckles are white
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary)
        if num_labels <= 1:
            return 0.0

        # Connected components with area between 1 and 4 pixels are speckles
        speckle_count = sum(1 for i in range(1, num_labels) if stats[i, cv2.CC_STAT_AREA] <= 4)
        total_pixels = gray.shape[0] * gray.shape[1]
        return float(speckle_count * 4 / total_pixels)

    def _calculate_entropy(self, gray: np.ndarray) -> float:
        """Compute Shannon entropy of grayscale image histogram."""
        import cv2
        hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
        hist = hist / (hist.sum() + 1e-7)
        non_zeros = hist[hist > 0]
        return float(-np.sum(non_zeros * np.log2(non_zeros)))

    def _estimate_dpi(self, gray: np.ndarray) -> int:
        """Estimate scanning DPI based on standard page dimensions."""
        h, w = gray.shape[:2]
        # Standard A4 is ~8.27 x 11.69 inches
        estimated = int(round(h / 11.69))
        return max(72, min(600, estimated if estimated >= 100 else 250))

    def _detect_orientation_rotation(self, gray: np.ndarray) -> int:
        """Estimate 0, 90, 180, or 270 degree rotation."""
        h, w = gray.shape[:2]
        if w > h * 1.3:
            # Landscape scan of portrait document -> likely 90 or 270 deg
            return 90
        return 0

    def _estimate_perspective_distortion(self, gray: np.ndarray) -> float:
        """Estimate perspective distortion based on contour bounding trapezoids."""
        import cv2
        h, w = gray.shape[:2]
        small = cv2.resize(gray, (min(w, 400), min(h, 400)))
        _, thresh = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return 1.0
        c = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(c)
        if area < (small.shape[0] * small.shape[1] * 0.1):
            return 1.0
        hull = cv2.convexHull(c)
        hull_area = cv2.contourArea(hull)
        if hull_area == 0:
            return 1.0
        # Ratio of contour to convex hull
        return float(min(1.0, area / hull_area))

    def _check_blank_page(self, gray: np.ndarray) -> bool:
        """Return True if the page appears empty or uniform background."""
        std = float(np.std(gray))
        mean = float(np.mean(gray))
        return std < 6.0 or (mean > 250 and std < 12.0)
