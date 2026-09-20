"""Structure-aware table extraction for medical document regions.

Medical documents commonly contain tables such as:
  Lab Report: Test | Result | Unit | Reference Range
  Prescription: Drug | Dosage | Morning | Noon | Evening | Night

This module detects row/column structure within a detected table region
and extracts cell-level text, preserving the spatial relationship between
cells. It uses OpenCV projection profiles for line detection.

Table data is returned as both structured JSON and as flat OCR lines
(for downstream compatibility with the normalization stage).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image

from pipeline.schemas.canonical import BoundingBox, OCRResult

log = logging.getLogger(__name__)


@dataclass
class TableCell:
    """One cell extracted from a detected table."""

    row: int
    col: int
    text: str
    confidence: float
    bbox: BoundingBox
    is_header: bool = False


@dataclass
class TableResult:
    """Structured output of table extraction."""

    headers: list[str]  # column header names (or empty list if no header row)
    rows: list[dict[str, Any]]  # each row as {header: cell_text}
    cells: list[TableCell]  # raw cell-level data
    row_count: int = 0
    col_count: int = 0
    confidence: float = 0.0

    def to_ocr_results(
        self,
        page: int,
        region_id: str | None,
        offset_x: float,
        offset_y: float,
        line_no_start: int = 1,
    ) -> list[OCRResult]:
        """Convert table cells to flat OCR results for downstream compatibility."""
        results: list[OCRResult] = []
        line_no = line_no_start
        for cell in self.cells:
            if not cell.text.strip():
                continue
            adj_bbox = BoundingBox(
                x0=cell.bbox.x0 + offset_x,
                y0=cell.bbox.y0 + offset_y,
                x1=cell.bbox.x1 + offset_x,
                y1=cell.bbox.y1 + offset_y,
            )
            results.append(
                OCRResult(
                    text=cell.text,
                    confidence=cell.confidence,
                    bbox=adj_bbox,
                    page=page,
                    line_no=line_no,
                    region_id=region_id,
                    model="table_extractor",
                    model_version="1.0.0",
                    is_handwritten=False,
                    is_reliable=(cell.confidence >= 0.50),
                )
            )
            line_no += 1
        return results

    def to_json(self) -> dict[str, Any]:
        """Return structured representation of the extracted table."""
        return {
            "type": "table",
            "headers": self.headers,
            "rows": self.rows,
            "row_count": self.row_count,
            "col_count": self.col_count,
            "confidence": round(self.confidence, 3),
        }


class TableExtractor:
    """Extracts structured table content from a table region crop.

    Algorithm:
    1. Convert to grayscale and binarize.
    2. Detect horizontal lines (row separators) via morphology.
    3. Detect vertical lines (column separators) via morphology.
    4. Compute row/column boundaries from detected lines.
    5. Crop each cell and run PP-OCR on it.
    6. Assemble structured table JSON.

    Falls back to running PP-OCR on the entire crop and returning it
    as a single-column table if structure detection fails.
    """

    # Minimum line length as fraction of image dimension to be considered a separator
    MIN_H_LINE_RATIO: float = 0.3
    MIN_V_LINE_RATIO: float = 0.2

    def __init__(self, ocr_fn: Any = None) -> None:
        """
        ocr_fn: callable(image: np.ndarray) -> list[OCRResult]
            The PP-OCR predict function (or any OCR callable accepting a numpy array).
            If None, cells are returned with empty text.
        """
        self._ocr_fn = ocr_fn

    def extract(
        self,
        crop: np.ndarray | Image.Image,
        page: int = 1,
        region_id: str | None = None,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> TableResult:
        """Extract structured table from a crop image."""
        if isinstance(crop, Image.Image):
            arr = np.array(crop.convert("RGB"))
        else:
            arr = crop

        if arr.size == 0 or arr.shape[0] < 10 or arr.shape[1] < 10:
            log.warning("Table crop too small (%s). Returning empty table.", arr.shape)
            return TableResult(headers=[], rows=[], cells=[])

        h, w = arr.shape[:2]
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if len(arr.shape) == 3 else arr

        # Binarize (OTSU, inverted: text = white)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Detect horizontal separators
        h_kernel_w = max(10, int(w * self.MIN_H_LINE_RATIO))
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_kernel_w, 1))
        h_lines_img = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel, iterations=2)

        # Detect vertical separators
        v_kernel_h = max(10, int(h * self.MIN_V_LINE_RATIO))
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_kernel_h))
        v_lines_img = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel, iterations=2)

        row_separators = self._detect_line_positions(h_lines_img, axis=0, image_size=h)
        col_separators = self._detect_line_positions(v_lines_img, axis=1, image_size=w)

        log.info(
            "Table detection: %dx%d crop -> %d row separators, %d col separators",
            w,
            h,
            len(row_separators),
            len(col_separators),
        )

        # Require at least 1 separator in each dimension to claim table structure
        if len(row_separators) < 1 or len(col_separators) < 1:
            return self._fallback_whole_crop(arr, page, region_id, offset_x, offset_y)

        # Build cell boundaries from separators
        row_bounds = self._separators_to_bounds(row_separators, 0, h)
        col_bounds = self._separators_to_bounds(col_separators, 0, w)

        cells, confidences = self._extract_cells(
            arr, row_bounds, col_bounds, page, region_id, offset_x, offset_y
        )

        # Build structured table
        n_rows = len(row_bounds)
        n_cols = len(col_bounds)

        # Attempt to detect header row (first row)
        headers: list[str] = []
        data_rows: list[dict[str, Any]] = []

        if n_rows >= 2:
            # First row as headers
            header_cells = [c for c in cells if c.row == 0]
            header_cells.sort(key=lambda c: c.col)
            headers = [c.text.strip() or f"Col{c.col+1}" for c in header_cells]
            for c in header_cells:
                c.is_header = True

            # Remaining rows as data
            for row_idx in range(1, n_rows):
                row_cells = {c.col: c for c in cells if c.row == row_idx}
                row_dict: dict[str, Any] = {}
                for col_idx, hdr in enumerate(headers):
                    cell = row_cells.get(col_idx)
                    row_dict[hdr] = cell.text.strip() if cell else ""
                data_rows.append(row_dict)
        elif n_rows == 1:
            # Only one row — treat as data, no headers
            row_cells_sorted = sorted([c for c in cells if c.row == 0], key=lambda c: c.col)
            headers = []
            data_rows = [{f"Col{i+1}": c.text for i, c in enumerate(row_cells_sorted)}]

        mean_conf = float(np.mean(confidences)) if confidences else 0.0

        return TableResult(
            headers=headers,
            rows=data_rows,
            cells=cells,
            row_count=n_rows,
            col_count=n_cols,
            confidence=mean_conf,
        )

    def _detect_line_positions(
        self, line_img: np.ndarray, axis: int, image_size: int
    ) -> list[int]:
        """Project line image onto one axis and find separator positions.

        axis=0: horizontal separators (project rows -> detect y positions)
        axis=1: vertical separators (project cols -> detect x positions)
        """
        projection = line_img.sum(axis=axis)  # sum along the perpendicular axis
        # Normalize to 0-1
        projection = projection / (projection.max() + 1e-8)
        # Find positions where projection > 0.3 (30% of the max)
        peaks: list[int] = []
        in_peak = False
        peak_start = 0
        for i, val in enumerate(projection):
            if val > 0.3 and not in_peak:
                in_peak = True
                peak_start = i
            elif val <= 0.1 and in_peak:
                in_peak = False
                peak_center = (peak_start + i) // 2
                peaks.append(peak_center)
        if in_peak:
            peaks.append((peak_start + image_size) // 2)
        return peaks

    def _separators_to_bounds(
        self, separators: list[int], start: int, end: int
    ) -> list[tuple[int, int]]:
        """Convert separator positions to cell boundary pairs."""
        boundaries = [start] + separators + [end]
        bounds = []
        for i in range(len(boundaries) - 1):
            lo = boundaries[i]
            hi = boundaries[i + 1]
            if hi - lo >= 5:  # Minimum cell size 5px
                bounds.append((lo, hi))
        return bounds

    def _extract_cells(
        self,
        arr: np.ndarray,
        row_bounds: list[tuple[int, int]],
        col_bounds: list[tuple[int, int]],
        page: int,
        region_id: str | None,
        offset_x: float,
        offset_y: float,
    ) -> tuple[list[TableCell], list[float]]:
        """Crop and OCR each cell."""
        cells: list[TableCell] = []
        confidences: list[float] = []

        for row_idx, (y0, y1) in enumerate(row_bounds):
            for col_idx, (x0, x1) in enumerate(col_bounds):
                cell_crop = arr[y0:y1, x0:x1]
                if cell_crop.size == 0:
                    continue

                # Run OCR on cell crop
                text, conf = self._ocr_cell(cell_crop)
                confidences.append(conf)

                bbox = BoundingBox(
                    x0=float(x0 + offset_x),
                    y0=float(y0 + offset_y),
                    x1=float(x1 + offset_x),
                    y1=float(y1 + offset_y),
                )
                cells.append(
                    TableCell(
                        row=row_idx,
                        col=col_idx,
                        text=text,
                        confidence=conf,
                        bbox=bbox,
                    )
                )

        return cells, confidences

    def _ocr_cell(self, cell_crop: np.ndarray) -> tuple[str, float]:
        """Run OCR on a single cell crop. Returns (text, confidence)."""
        if self._ocr_fn is None:
            return "", 0.0

        try:
            results = self._ocr_fn(cell_crop)
            if not results:
                return "", 0.0
            # Concatenate all text lines in the cell
            text = " ".join(r.text for r in results if r.text.strip())
            conf = float(np.mean([r.confidence for r in results])) if results else 0.0
            return text.strip(), conf
        except Exception as exc:
            log.debug("OCR failed on cell crop: %s", exc)
            return "", 0.0

    def _fallback_whole_crop(
        self,
        arr: np.ndarray,
        page: int,
        region_id: str | None,
        offset_x: float,
        offset_y: float,
    ) -> TableResult:
        """Fall back to OCR on the entire crop as a single table cell."""
        log.info("Table structure not detected. Falling back to whole-crop OCR.")
        text, conf = self._ocr_cell(arr)
        h, w = arr.shape[:2]
        cell = TableCell(
            row=0,
            col=0,
            text=text,
            confidence=conf,
            bbox=BoundingBox(
                x0=offset_x,
                y0=offset_y,
                x1=offset_x + w,
                y1=offset_y + h,
            ),
        )
        rows = [{"text": text}] if text else []
        return TableResult(
            headers=[],
            rows=rows,
            cells=[cell] if text else [],
            row_count=1,
            col_count=1,
            confidence=conf,
        )
