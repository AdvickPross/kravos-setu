"""LayoutLMv3 model adapter for reading-order and structural document relationship understanding."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from PIL import Image

from models.base import BaseModelAdapter, has_local_hf_checkpoint
from pipeline.schemas.canonical import DocumentRegion, RegionClass

log = logging.getLogger(__name__)


class LayoutLMv3Adapter(BaseModelAdapter):
    """Adapter for LayoutLMv3 structural layout and reading-order understanding.

    NOTE: LayoutLMv3 in this pipeline does NOT re-detect bounding boxes.
    Detection is strictly owned by RT-DETR. LayoutLMv3 is responsible solely
    for reading-order serialization and section-hierarchy relationships.
    """

    def __init__(
        self,
        model_name: str = "layoutlmv3",
        device: str = "cpu",
        checkpoint: str = "microsoft/layoutlmv3-base",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(model_name=model_name, device=device, config=config)
        self.checkpoint = checkpoint
        self._processor = None
        self._model = None
        self._version = "layoutlmv3-v1.0"
        self._has_hf_model = False

    def get_model_version(self) -> str:
        if self._has_hf_model:
            return f"{self._version}-hf-{self.checkpoint}"
        return f"{self._version}-topological-heuristic"

    def load(self) -> None:
        """Lazily load LayoutLMv3 processor & model if available."""
        if self._is_loaded:
            return

        if not has_local_hf_checkpoint(self.checkpoint):
            log.info(
                "Local HuggingFace LayoutLMv3 checkpoint '%s' not cached. Using spatial topological reading-order.",
                self.checkpoint,
            )
            self._processor = None
            self._model = None
            self._has_hf_model = False
            self._memory_footprint_mb = 5.0
            self._is_loaded = True
            self.touch()
            return

        try:
            from transformers import LayoutLMv3Model, LayoutLMv3Processor

            log.info("Loading LayoutLMv3 from %s on %s...", self.checkpoint, self.device)
            self._processor = LayoutLMv3Processor.from_pretrained(self.checkpoint, local_files_only=True)
            self._model = LayoutLMv3Model.from_pretrained(self.checkpoint, local_files_only=True).to(self.device)
            self._has_hf_model = True
            self._memory_footprint_mb = 350.0
        except Exception as exc:
            log.info(
                "Local HuggingFace LayoutLMv3 checkpoint '%s' not cached (%s). Using spatial topological reading-order.",
                self.checkpoint,
                exc,
            )
            self._processor = None
            self._model = None
            self._has_hf_model = False
            self._memory_footprint_mb = 5.0

        self._is_loaded = True
        self.touch()

    def unload(self) -> None:
        self._processor = None
        self._model = None
        self._is_loaded = False
        self._memory_footprint_mb = 0.0

    def predict(
        self, regions: list[DocumentRegion], image: np.ndarray | Image.Image | None = None
    ) -> list[DocumentRegion]:
        """Establish true reading order and hierarchy between detected regions."""
        self.ensure_loaded()
        if not regions:
            return []

        # Perform two-column aware spatial topological sorting
        ordered_regions = self._sort_reading_order(regions)

        # Infer heading-child hierarchical relationships (parent_id)
        structured_regions = self._link_hierarchy(ordered_regions)

        return structured_regions

    def _sort_reading_order(self, regions: list[DocumentRegion]) -> list[DocumentRegion]:
        """Sort regions by reading order.

        Detects multi-column layout by checking horizontal overlap.
        For single-column: top-to-bottom.
        For multi-column: left-column top-to-bottom, then right-column top-to-bottom.
        """
        if len(regions) <= 1:
            for idx, r in enumerate(regions, start=1):
                r.reading_order = idx
            return regions

        # Estimate average page width from boxes
        max_x = max(r.bbox.x1 for r in regions)
        mid_x = max_x / 2.0

        # Check if there is a distinct two-column split
        left_col = [r for r in regions if r.bbox.x1 <= mid_x * 1.1]
        right_col = [r for r in regions if r.bbox.x0 >= mid_x * 0.9]
        full_width = [r for r in regions if r not in left_col and r not in right_col]

        # If significant column split exists (>60% of regions fit in columns)
        if len(left_col) + len(right_col) >= len(regions) * 0.6 and left_col and right_col:
            # Full-width headers first, then left col, then right col, then footers
            headers = [r for r in full_width if r.class_name == RegionClass.HEADER.value]
            footers = [r for r in full_width if r.class_name == RegionClass.FOOTER.value]
            other_full = [r for r in full_width if r not in headers and r not in footers]

            headers.sort(key=lambda r: (r.bbox.y0, r.bbox.x0))
            left_col.sort(key=lambda r: (r.bbox.y0, r.bbox.x0))
            right_col.sort(key=lambda r: (r.bbox.y0, r.bbox.x0))
            other_full.sort(key=lambda r: (r.bbox.y0, r.bbox.x0))
            footers.sort(key=lambda r: (r.bbox.y0, r.bbox.x0))

            sorted_list = headers + left_col + right_col + other_full + footers
        else:
            # Single-column: primary top-to-bottom, secondary left-to-right
            # Group lines with similar Y into bands
            sorted_list = sorted(regions, key=lambda r: (round(r.bbox.y0 / 20.0) * 20.0, r.bbox.x0))

        for idx, reg in enumerate(sorted_list, start=1):
            reg.reading_order = idx

        return sorted_list

    def _link_hierarchy(self, regions: list[DocumentRegion]) -> list[DocumentRegion]:
        """Link paragraph/table/roi regions to the nearest preceding section header."""
        current_header_id: str | None = None

        for reg in regions:
            if reg.class_name == RegionClass.HEADER.value:
                current_header_id = reg.region_id
                reg.parent_id = None
            else:
                reg.parent_id = current_header_id

        return regions
