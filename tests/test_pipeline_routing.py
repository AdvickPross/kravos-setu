"""Unit tests for ROI router, printed vs. handwritten routing, and fusion resolution."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from config.loader import RoutingPolicy
from pipeline.router import ROIRouter, RoutingDecision
from pipeline.schemas.canonical import BoundingBox, DocumentRegion, OCRResult, RegionClass


@pytest.fixture
def router() -> ROIRouter:
    policy = RoutingPolicy(handwriting_threshold=0.60, fusion_threshold=0.35)
    return ROIRouter(policy=policy)


def test_special_region_routing(router: ROIRouter):
    page_img = np.full((1000, 800, 3), 255, dtype=np.uint8)
    barcode_reg = DocumentRegion(
        region_id="r1",
        page=1,
        bbox=BoundingBox(50, 50, 200, 100),
        class_name=RegionClass.BARCODE.value,
        confidence=0.99,
        source_model="rt-detr",
        model_version="1.0",
    )
    decision = router.route(barcode_reg, page_img)
    assert decision.target_engine == "barcode"
    assert decision.is_special_region

    table_reg = DocumentRegion(
        region_id="r2",
        page=1,
        bbox=BoundingBox(50, 200, 600, 500),
        class_name=RegionClass.TABLE.value,
        confidence=0.95,
        source_model="rt-detr",
        model_version="1.0",
    )
    table_decision = router.route(table_reg, page_img)
    assert table_decision.target_engine == "table"


def test_printed_typography_routes_to_ppocr(router: ROIRouter):
    # Synthetic printed typography crop: sharp horizontal/vertical edges
    crop = np.full((100, 300, 3), 255, dtype=np.uint8)
    for y in range(20, 80, 15):
        crop[y : y + 3, 20:280] = 0

    mode, conf = router.classify_handwriting(crop)
    assert mode == "printed"
    assert conf >= 0.50


def test_fusion_resolution_picks_highest_quality_candidate(router: ROIRouter):
    bbox = BoundingBox(0, 0, 100, 30)
    pp_candidates = [
        OCRResult(
            text="Metformin 500 mg",
            confidence=0.92,
            bbox=bbox,
            page=1,
            line_no=1,
            model="ppocr",
        )
    ]
    tr_candidates = [
        OCRResult(
            text="Metf0rm!n 5oOmg",
            confidence=0.55,
            bbox=bbox,
            page=1,
            line_no=1,
            model="trocr",
        )
    ]

    decision = RoutingDecision(target_engine="fusion", routing_confidence=0.30)
    fused = router.fuse(pp_candidates, tr_candidates, decision)

    assert len(fused) == 1
    assert fused[0].text == "Metformin 500 mg"
    assert fused[0].model == "fusion"
