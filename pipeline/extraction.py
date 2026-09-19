"""Extraction stage orchestrating routed PP-OCR, TrOCR, and special region decoding."""

from __future__ import annotations

import logging
import re
from typing import Any

import numpy as np
from PIL import Image

from models.manager import ModelManager
from pipeline.router import ROIRouter, RoutingDecision
from pipeline.schemas.canonical import BoundingBox, DocumentRegion, OCRResult, RegionClass

log = logging.getLogger(__name__)


class ExtractionStage:
    """Extracts text across all detected document regions using the ROI router."""

    def __init__(self, model_manager: ModelManager, router: ROIRouter | None = None) -> None:
        self.model_manager = model_manager
        self.router = router or ROIRouter()
        self._table_extractor: Any = None  # lazy-init

    def process(
        self, image: np.ndarray | Image.Image, regions: list[DocumentRegion], page: int = 1
    ) -> list[OCRResult]:
        """Extract text from all regions according to their routed path."""
        if isinstance(image, Image.Image):
            arr = np.array(image.convert("RGB"))
        else:
            arr = image

        if not regions:
            # If no regions, run PP-OCR on the whole page as a single block
            ppocr = self.model_manager.get_model("ppocr")
            return ppocr.predict(arr, page=page)

        all_results: list[OCRResult] = []

        # Process regions in reading order
        for region in sorted(regions, key=lambda r: r.reading_order):
            decision = self.router.route(region, arr)
            crop = region.bbox.crop_from(arr)

            results = self._extract_for_decision(decision, crop, region, page)
            all_results.extend(results)

        log.info(
            "Extraction stage complete: %d lines extracted across %d regions on page %d",
            len(all_results),
            len(regions),
            page,
        )
        return all_results

    def _extract_for_decision(
        self,
        decision: RoutingDecision,
        crop: np.ndarray,
        region: DocumentRegion,
        page: int,
    ) -> list[OCRResult]:
        x0, y0 = region.bbox.x0, region.bbox.y0

        if decision.target_engine == "ppocr":
            ppocr = self.model_manager.get_model("ppocr")
            return ppocr.predict(crop, page=page, region_id=region.region_id, offset_x=x0, offset_y=y0)

        elif decision.target_engine == "trocr":
            trocr = self.model_manager.get_model("trocr")
            return trocr.predict(crop, page=page, region_id=region.region_id, offset_x=x0, offset_y=y0)

        elif decision.target_engine == "fusion":
            ppocr = self.model_manager.get_model("ppocr")
            trocr = self.model_manager.get_model("trocr")

            pp_res = ppocr.predict(crop, page=page, region_id=region.region_id, offset_x=x0, offset_y=y0)
            tr_res = trocr.predict(crop, page=page, region_id=region.region_id, offset_x=x0, offset_y=y0)

            return self.router.fuse(pp_res, tr_res, decision)

        elif decision.target_engine == "barcode":
            return self._decode_barcode(crop, region, page)

        elif decision.target_engine == "metadata":
            log.debug("Region %s handled as %s metadata", region.region_id, region.class_name)
            return []

        elif decision.target_engine == "table":
            extractor = self._get_table_extractor()
            table_result = extractor.extract(
                crop, page=page, region_id=region.region_id,
                offset_x=x0, offset_y=y0
            )
            log.info(
                "Table extracted: %d rows x %d cols, confidence=%.2f (region %s)",
                table_result.row_count, table_result.col_count,
                table_result.confidence, region.region_id
            )
            return table_result.to_ocr_results(
                page=page, region_id=region.region_id,
                offset_x=x0, offset_y=y0
            )

        else:
            # Default to PP-OCR
            ppocr = self.model_manager.get_model("ppocr")
            return ppocr.predict(crop, page=page, region_id=region.region_id, offset_x=x0, offset_y=y0)

    def _get_table_extractor(self):
        """Lazily create and return the table extractor."""
        if self._table_extractor is None:
            from pipeline.table_extractor import TableExtractor
            ppocr = self.model_manager.get_model("ppocr")
            # Pass ppocr.predict as the OCR function for cells
            def _ocr_fn(crop):
                return ppocr.predict(crop, page=1)
            self._table_extractor = TableExtractor(ocr_fn=_ocr_fn)
        return self._table_extractor


    def _decode_barcode(self, crop: np.ndarray, region: DocumentRegion, page: int) -> list[OCRResult]:
        """Decode 1D/2D barcodes from the region crop."""
        try:
            from pyzbar import pyzbar

            decoded = pyzbar.decode(crop)
            results = []
            for idx, item in enumerate(decoded, start=1):
                data_str = item.data.decode("utf-8", errors="ignore")
                results.append(
                    OCRResult(
                        text=f"BARCODE: {data_str}",
                        confidence=1.0,
                        bbox=region.bbox,
                        page=page,
                        line_no=idx,
                        region_id=region.region_id,
                        model="barcode_decoder",
                        model_version="1.0.0",
                    )
                )
            if results:
                return results
        except ImportError:
            pass

        # Fallback: run PP-OCR on the barcode region (digits under barcode)
        ppocr = self.model_manager.get_model("ppocr")
        return ppocr.predict(
            crop, page=page, region_id=region.region_id, offset_x=region.bbox.x0, offset_y=region.bbox.y0
        )
