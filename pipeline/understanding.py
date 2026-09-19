"""Document structure and reading-order understanding stage using LayoutLMv3."""

from __future__ import annotations

import logging

import numpy as np
from PIL import Image

from models.manager import ModelManager
from pipeline.schemas.canonical import DocumentRegion

log = logging.getLogger(__name__)


class LayoutUnderstandingStage:
    """Stage using LayoutLMv3 to serialize regions into true reading order and map structural hierarchies."""

    def __init__(self, model_manager: ModelManager) -> None:
        self.model_manager = model_manager

    def process(
        self, regions: list[DocumentRegion], image: np.ndarray | Image.Image | None = None
    ) -> list[DocumentRegion]:
        """Establish reading order and section parent-child hierarchy across detected regions."""
        if not regions:
            return []

        layoutlmv3 = self.model_manager.get_model("layoutlmv3")
        ordered_regions = layoutlmv3.predict(regions, image=image)
        log.info(
            "LayoutLMv3 sequenced %d regions into reading order (model_version: %s)",
            len(ordered_regions),
            layoutlmv3.get_model_version(),
        )
        return ordered_regions
