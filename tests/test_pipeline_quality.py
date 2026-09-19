"""Unit tests for the classical computer vision quality assessment engine."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from config.loader import QualityPolicy
from pipeline.quality import QualityAssessmentEngine


@pytest.fixture
def quality_engine() -> QualityAssessmentEngine:
    policy = QualityPolicy(blur_threshold=100.0, skew_threshold_degrees=1.0, contrast_threshold=0.30)
    return QualityAssessmentEngine(policy=policy)


def test_clean_image_passes_all_quality_checks(quality_engine: QualityAssessmentEngine):
    # Create clean synthetic text-like image: white background with black text lines
    img = Image.new("RGB", (600, 800), "white")
    arr = np.array(img)
    # Add sharp horizontal lines simulating clean text
    for y in range(50, 750, 30):
        arr[y : y + 3, 50:550] = (0, 0, 0)

    report = quality_engine.assess_page(arr, page=1)
    assert report.page == 1
    assert report.overall_score >= 0.70
    assert not report.enhancement_required, "Clean document should not require neural enhancement"
    assert "restormer_deblur" not in report.action_plan


def test_heavily_blurred_image_triggers_enhancement(quality_engine: QualityAssessmentEngine):
    # Create very blurry low-frequency image
    arr = np.full((400, 400, 3), 128, dtype=np.uint8)
    report = quality_engine.assess_page(arr, page=1)

    assert not report.checks["blur"].passed
    assert report.enhancement_required
    assert "restormer_deblur" in report.action_plan


def test_skew_angle_detection(quality_engine: QualityAssessmentEngine):
    import cv2

    img = np.full((500, 500), 255, dtype=np.uint8)
    # Draw horizontal bars
    for y in range(100, 400, 40):
        cv2.line(img, (50, y), (450, y), 0, 3)

    # Rotate by 4 degrees
    rot_mat = cv2.getRotationMatrix2D((250, 250), 4.0, 1.0)
    rotated = cv2.warpAffine(img, rot_mat, (500, 500), borderValue=255)

    report = quality_engine.assess_page(rotated, page=1)
    # Skew check should flag the rotation
    skew_check = report.checks["skew"]
    assert abs(skew_check.score) > 1.0
    assert not skew_check.passed
    assert "deskew" in report.action_plan


def test_quality_latency_is_under_50ms(quality_engine: QualityAssessmentEngine):
    img = np.full((1200, 800, 3), 240, dtype=np.uint8)
    report = quality_engine.assess_page(img, page=1)
    assert report.latency_ms < 150.0  # Safe threshold for CPU
