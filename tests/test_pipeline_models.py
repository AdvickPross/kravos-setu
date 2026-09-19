"""Unit tests for the five canonical model adapters."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from models.layoutlmv3_adapter import LayoutLMv3Adapter
from models.ppocr_adapter import PPOCRAdapter
from models.restormer_adapter import RestormerAdapter
from models.rtdetr_adapter import RTDetrAdapter
from models.trocr_adapter import TrOCRAdapter
from pipeline.schemas.canonical import BoundingBox, DocumentRegion, RegionClass


def test_restormer_adapter_contract():
    adapter = RestormerAdapter(device="cpu")
    assert not adapter.is_loaded
    adapter.load()
    assert adapter.is_loaded
    assert adapter.get_model_version()

    # Inference test
    sample_img = np.full((100, 100, 3), 200, dtype=np.uint8)
    enhanced = adapter.predict(sample_img)
    assert isinstance(enhanced, np.ndarray)
    assert enhanced.shape == sample_img.shape

    adapter.unload()
    assert not adapter.is_loaded


def test_rtdetr_adapter_contract():
    adapter = RTDetrAdapter(device="cpu")
    adapter.load()
    assert adapter.is_loaded
    assert adapter.get_model_version()

    sample_img = np.full((600, 800, 3), 255, dtype=np.uint8)
    regions = adapter.predict(sample_img, page=1)
    assert isinstance(regions, list)
    for reg in regions:
        assert isinstance(reg, DocumentRegion)
        assert reg.page == 1
        assert reg.bbox.width >= 0
        assert reg.bbox.height >= 0

    adapter.unload()
    assert not adapter.is_loaded


def test_layoutlmv3_adapter_contract():
    adapter = LayoutLMv3Adapter(device="cpu")
    adapter.load()
    assert adapter.is_loaded
    assert adapter.get_model_version()

    regions = [
        DocumentRegion(
            region_id="r2",
            page=1,
            bbox=BoundingBox(10, 200, 500, 300),
            class_name=RegionClass.PARAGRAPH.value,
            confidence=0.9,
            source_model="test",
            model_version="1",
        ),
        DocumentRegion(
            region_id="r1",
            page=1,
            bbox=BoundingBox(10, 50, 500, 100),
            class_name=RegionClass.HEADER.value,
            confidence=0.9,
            source_model="test",
            model_version="1",
        ),
    ]
    ordered = adapter.predict(regions)
    assert len(ordered) == 2
    # Top region r1 should precede r2 in reading order
    assert ordered[0].region_id == "r1"
    assert ordered[0].reading_order == 1
    assert ordered[1].region_id == "r2"
    assert ordered[1].reading_order == 2

    adapter.unload()
    assert not adapter.is_loaded


def test_ppocr_adapter_contract():
    adapter = PPOCRAdapter(device="cpu")
    adapter.load()
    assert adapter.is_loaded
    assert "ppocr" in adapter.get_model_version().lower()

    # Predict on blank image
    img = np.full((100, 200, 3), 255, dtype=np.uint8)
    res = adapter.predict(img, page=1)
    assert isinstance(res, list)

    adapter.unload()
    assert not adapter.is_loaded


def test_trocr_adapter_contract():
    adapter = TrOCRAdapter(device="cpu")
    adapter.load()
    assert adapter.is_loaded
    assert "trocr" in adapter.get_model_version().lower()

    img = np.full((60, 200, 3), 255, dtype=np.uint8)
    res = adapter.predict(img, page=1)
    assert isinstance(res, list)

    adapter.unload()
    assert not adapter.is_loaded
