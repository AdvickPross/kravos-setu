"""Canonical data contracts for Kravos Setu Document Intelligence Pipeline.

Defines unified models for bounding boxes, detected regions, quality reports,
OCR text units, clinical entities, provenance, and end-to-end pipeline results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class RegionClass(str, Enum):
    TABLE = "table"
    SIGNATURE = "signature"
    STAMP = "stamp"
    BARCODE = "barcode"
    PARAGRAPH = "paragraph"
    FIGURE = "figure"
    HEADER = "header"
    FOOTER = "footer"
    ROI = "roi"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BoundingBox:
    """Coordinate representation in original image pixel space (x0, y0, x1, y1)."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.x1 < self.x0 or self.y1 < self.y0:
            object.__setattr__(self, "x0", min(self.x0, self.x1))
            object.__setattr__(self, "x1", max(self.x0, self.x1))
            object.__setattr__(self, "y0", min(self.y0, self.y1))
            object.__setattr__(self, "y1", max(self.y0, self.y1))

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height if self.height > 0 else 0.0

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    def to_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def to_normalized(self, page_width: float, page_height: float) -> tuple[float, float, float, float]:
        if page_width <= 0 or page_height <= 0:
            return (0.0, 0.0, 0.0, 0.0)
        return (
            self.x0 / page_width,
            self.y0 / page_height,
            self.x1 / page_width,
            self.y1 / page_height,
        )

    @classmethod
    def from_normalized(
        cls, norm_box: tuple[float, float, float, float], page_width: float, page_height: float
    ) -> BoundingBox:
        return cls(
            x0=norm_box[0] * page_width,
            y0=norm_box[1] * page_height,
            x1=norm_box[2] * page_width,
            y1=norm_box[3] * page_height,
        )

    def iou(self, other: BoundingBox) -> float:
        """Compute Intersection over Union between two bounding boxes."""
        ix0 = max(self.x0, other.x0)
        iy0 = max(self.y0, other.y0)
        ix1 = min(self.x1, other.x1)
        iy1 = min(self.y1, other.y1)

        inter_w = max(0.0, ix1 - ix0)
        inter_h = max(0.0, iy1 - iy0)
        inter_area = inter_w * inter_h

        union_area = self.area + other.area - inter_area
        if union_area <= 0:
            return 0.0
        return inter_area / union_area

    def crop_from(self, image: Any) -> Any:
        """Crop this region from a PIL Image or numpy array."""
        try:
            # PIL Image
            return image.crop((int(self.x0), int(self.y0), int(self.x1), int(self.y1)))
        except AttributeError:
            # Numpy array: [y0:y1, x0:x1]
            return image[int(self.y0) : int(self.y1), int(self.x0) : int(self.x1)]


@dataclass
class DocumentRegion:
    """A detected document region with bounding box and structural information."""

    region_id: str
    page: int
    bbox: BoundingBox
    class_name: str
    confidence: float
    source_model: str
    model_version: str
    reading_order: int = 0
    parent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityCheckResult:
    name: str
    score: float
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityReport:
    """Diagnostics and action plan produced by page-level quality assessment."""

    page: int
    checks: dict[str, QualityCheckResult]
    overall_score: float
    enhancement_required: bool
    action_plan: list[str]
    latency_ms: float = 0.0


@dataclass(frozen=True)
class OCRResult:
    """One line/block of recognised text conforming to the canonical OCR contract."""

    text: str
    confidence: float
    bbox: BoundingBox
    page: int
    line_no: int
    region_id: str | None = None
    model: str = "ppocr"
    model_version: str = "1.0.0"
    is_handwritten: bool = False
    is_reliable: bool = True

    def to_legacy_line(self) -> Any:
        """Compatibility bridge to legacy ocr.engine.OcrLine."""
        from ocr.engine import OcrLine

        return OcrLine(
            text=self.text,
            confidence=self.confidence,
            page=self.page,
            line_no=self.line_no,
            box=self.bbox.to_tuple(),
        )


@dataclass(frozen=True)
class Provenance:
    """Traceability metadata linking any extracted fact to its source origin."""

    document_id: str
    page: int
    region_id: str | None
    bbox: tuple[float, float, float, float] | None
    source_model: str
    model_version: str
    pipeline_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "page": self.page,
            "region_id": self.region_id,
            "bbox": list(self.bbox) if self.bbox else None,
            "source_model": self.source_model,
            "model_version": self.model_version,
            "pipeline_version": self.pipeline_version,
        }


@dataclass(frozen=True)
class ClinicalEntity:
    """A recognized clinical fact with full provenance and confidence tracking."""

    fact_id: str
    patient_id: str
    category: str
    safety_class: str
    text: str
    display: str | None
    code: str | None
    code_system: str | None
    value: str | None
    unit: str | None
    date: datetime | None
    confidence: float
    provenance: Provenance
    line_no: int = 1
    line_text: str = ""
    status: str = "active"
    also_in: list[str] = field(default_factory=list)

    def as_fact_row(self) -> dict[str, Any]:
        """Convert to the dictionary shape expected by the Postgres 'facts' table."""
        return {
            "fact_id": self.fact_id,
            "patient_id": self.patient_id,
            "category": self.category,
            "safety_class": self.safety_class,
            "text": self.text,
            "code_system": self.code_system,
            "code": self.code,
            "display": self.display,
            "value": self.value,
            "unit": self.unit,
            "date": self.date.isoformat() if self.date else None,
            "status": self.status,
            "source_resource_type": "ScannedDocument",
            "source_resource_id": f"{self.provenance.document_id}#p{self.provenance.page}L{self.line_no}",
            "encounter_id": None,
            "also_in": list(self.also_in),
        }


@dataclass
class PipelineResult:
    """The complete result of running the document intelligence pipeline."""

    document_id: str
    source_file: str
    pipeline_version: str
    quality_reports: list[QualityReport]
    regions: list[DocumentRegion]
    ocr_results: list[OCRResult]
    entities: list[ClinicalEntity]
    sections_seen: list[str]
    unmatched: list[str]
    ambiguous: list[str]
    low_confidence: list[str]
    stage_timings: dict[str, float] = field(default_factory=dict)
    stage_versions: dict[str, str] = field(default_factory=dict)
    mean_confidence: float = 0.0
    model_metadata: dict[str, Any] = field(default_factory=dict)
    memory_status: dict[str, Any] = field(default_factory=dict)

    @property
    def total_lines(self) -> int:
        return len(self.ocr_results)

    @property
    def total_facts(self) -> int:
        return len(self.entities)
