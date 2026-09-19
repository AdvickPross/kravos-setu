"""Unit tests for adaptive preprocessing profiles and transformations."""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.adaptive_preprocessing import (
    AdaptivePreprocessor,
    PreprocessingProfile,
)


@pytest.fixture
def preprocessor() -> AdaptivePreprocessor:
    return AdaptivePreprocessor()


@pytest.fixture
def sample_image() -> np.ndarray:
    # 200x200 3-channel synthetic document image with background and contrast
    img = np.full((200, 200, 3), 220, dtype=np.uint8)
    # Add some dark rectangular blocks to simulate text lines
    img[40:50, 20:180] = 30
    img[70:80, 20:150] = 30
    img[100:110, 20:160] = 30
    return img


def test_all_profiles_exist(preprocessor: AdaptivePreprocessor, sample_image: np.ndarray):
    """Verify that all 9 preprocessing profiles execute without error."""
    for profile in PreprocessingProfile:
        processed, meta = preprocessor.process(sample_image, profile)
        assert isinstance(processed, np.ndarray)
        assert processed.ndim in (2, 3)
        assert isinstance(meta, dict)
        assert "operations_applied" in meta


def test_normal_profile_returns_copy(preprocessor: AdaptivePreprocessor, sample_image: np.ndarray):
    """NORMAL profile returns copy of image without destructive transformations."""
    processed, meta = preprocessor.process(sample_image, PreprocessingProfile.NORMAL)
    assert np.array_equal(processed, sample_image)
    assert len(meta["operations_applied"]) == 0


def test_shadowed_profile_applies_shadow_removal(preprocessor: AdaptivePreprocessor, sample_image: np.ndarray):
    """SHADOWED profile applies morphological division shadow removal."""
    processed, meta = preprocessor.process(sample_image, PreprocessingProfile.SHADOWED)
    applied_ops = meta["operations_applied"]
    assert any("shadow" in op.lower() for op in applied_ops)
    assert processed.shape == sample_image.shape


def test_blurry_profile_applies_unsharp_mask(preprocessor: AdaptivePreprocessor, sample_image: np.ndarray):
    """BLURRY profile sharpens image."""
    processed, meta = preprocessor.process(sample_image, PreprocessingProfile.BLURRY)
    applied_ops = meta["operations_applied"]
    assert any("unsharp" in op.lower() or "sharpen" in op.lower() for op in applied_ops)


def test_small_text_profile_upscales(preprocessor: AdaptivePreprocessor, sample_image: np.ndarray):
    """SMALL_TEXT profile upscales the image."""
    processed, meta = preprocessor.process(sample_image, PreprocessingProfile.SMALL_TEXT)
    applied_ops = meta["operations_applied"]
    assert any("upscale" in op.lower() for op in applied_ops)
    assert processed.shape[0] > sample_image.shape[0]
    assert processed.shape[1] > sample_image.shape[1]


def test_handwriting_profile_contrast(preprocessor: AdaptivePreprocessor, sample_image: np.ndarray):
    """HANDWRITING profile enhances stroke contrast."""
    processed, meta = preprocessor.process(sample_image, PreprocessingProfile.HANDWRITING)
    applied_ops = meta["operations_applied"]
    assert any("contrast" in op.lower() or "smooth" in op.lower() or "bilateral" in op.lower() for op in applied_ops)


def test_deskew_transformation(preprocessor: AdaptivePreprocessor, sample_image: np.ndarray):
    """Deskew function handles skew angle correction."""
    skewed, applied = preprocessor.deskew(sample_image, angle=5.0)
    assert any("deskew" in op.lower() for op in applied)
    assert skewed.shape == sample_image.shape
