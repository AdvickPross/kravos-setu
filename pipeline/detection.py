"""Region detection stage using RT-DETR model adapter."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from PIL import Image

from models.manager import ModelManager
from pipeline.schemas.canonical import DocumentRegion

log = logging.getLogger(__name__)


class DetectionStage:
    """Detection stage wrapping RT-DETR to segment document regions into canonical DocumentRegion objects."""

    def __init__(self, model_manager: ModelManager) -> None:
        self.model_manager = model_manager

    def process(
        self, image: np.ndarray | Image.Image, page: int = 1, confidence_threshold: float = 0.30
    ) -> list[DocumentRegion]:
        """Detect document regions: headers, footers, paragraphs, tables, signatures, stamps, barcodes."""
        rtdetr = self.model_manager.get_model("rt-detr")
        regions = rtdetr.predict(image, page=page, confidence_threshold=confidence_threshold)
        log.info(
            "RT-DETR detected %d regions on page %d (model_version: %s)",
            len(regions),
            page,
            rtdetr.get_model_version(),
        )
        return regions
