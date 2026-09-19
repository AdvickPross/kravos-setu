"""Main pipeline orchestrator coordinating document acquisition through structuring and validation."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from config.loader import PipelineConfig, PolicyConfig
from models.manager import ModelManager
from pipeline.detection import DetectionStage
from pipeline.enhancement import EnhancementStage
from pipeline.extraction import ExtractionStage
from pipeline.normalization import NormalizationStage
from pipeline.quality import QualityAssessmentEngine
from pipeline.router import ROIRouter
from pipeline.schemas.canonical import (
    DocumentRegion,
    OCRResult,
    PipelineResult,
    QualityReport,
)
from pipeline.structuring import StructuringStage
from pipeline.understanding import LayoutUnderstandingStage
from pipeline.validation import ValidationStage

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
DEFAULT_PDF_RENDER_DPI = 250


def structured_log(
    stage: str,
    doc_id: str,
    latency_ms: float,
    confidence: float | None = None,
    model_version: str | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit fixed-schema JSON-line structured log. Avoids logging sensitive patient data."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "doc_id": doc_id,
        "latency_ms": round(latency_ms, 2),
        "confidence": round(confidence, 3) if confidence is not None else None,
        "model_version": model_version or "unset",
        "error": error,
    }
    if extra:
        entry["extra"] = extra
    log.info(json.dumps(entry))


class PipelineOrchestrator:
    """Coordinates the 5-model medical document intelligence pipeline."""

    def __init__(
        self,
        model_manager: ModelManager | None = None,
        pipeline_config: PipelineConfig | None = None,
        policy_config: PolicyConfig | None = None,
        device: str | None = None,
    ) -> None:
        self.pipeline_config = pipeline_config or PipelineConfig.load()
        self.policy_config = policy_config or PolicyConfig.load()
        self.model_manager = model_manager or ModelManager(policy=self.policy_config.memory, device=device)

        # Initialize stages
        self.quality_stage = QualityAssessmentEngine(policy=self.policy_config.quality)
        self.enhancement_stage = EnhancementStage(self.model_manager)
        self.detection_stage = DetectionStage(self.model_manager)
        self.understanding_stage = LayoutUnderstandingStage(self.model_manager)
        self.router = ROIRouter(policy=self.policy_config.routing)
        self.extraction_stage = ExtractionStage(self.model_manager, router=self.router)
        self.normalization_stage = NormalizationStage()
        self.structuring_stage = StructuringStage()
        self.validation_stage = ValidationStage(policy=self.policy_config.validation)

    def load_pages(self, path: Path | str, dpi: int | None = None) -> list[np.ndarray]:
        """Rasterize PDF pages or load image into list of 3-channel RGB numpy arrays."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Document file does not exist: {p}")

        effective_dpi = dpi or getattr(self.policy_config, "pdf_dpi", DEFAULT_PDF_RENDER_DPI)
        suffix = p.suffix.lower()
        if suffix == ".pdf":
            import fitz

            zoom = effective_dpi / 72.0
            pages: list[np.ndarray] = []
            with fitz.open(p) as doc:
                for page in doc:
                    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                    if pix.n == 4:
                        rgb = arr[:, :, :3]
                    elif pix.n == 1:
                        rgb = np.repeat(arr, 3, axis=-1)
                    else:
                        rgb = arr
                    pages.append(np.ascontiguousarray(rgb, dtype=np.uint8))
            return pages

        elif suffix in IMAGE_SUFFIXES:
            with Image.open(p) as handle:
                arr = np.array(handle.convert("RGB"))
                return [np.ascontiguousarray(arr, dtype=np.uint8)]

        else:
            raise ValueError(
                f"unsupported file type {suffix!r}; expected a PDF or one of {', '.join(sorted(IMAGE_SUFFIXES))}"
            )

    def process_document(
        self,
        file_path: Path | str,
        patient_id: str = "patient-unknown",
        document_date: datetime | None = None,
        document_type: str = "discharge_summary",
        dpi: int | None = None,
    ) -> PipelineResult:
        """Run complete document intelligence pipeline over all pages of the document."""
        path = Path(file_path)
        doc_id = path.name
        t0 = time.perf_counter()

        stage_timings: dict[str, float] = {}
        stage_versions: dict[str, str] = {
            "pipeline_version": self.pipeline_config.pipeline_version,
            "policy_version": self.policy_config.policy_version,
        }

        # 1. Acquisition
        acq_start = time.perf_counter()
        pages = self.load_pages(path, dpi=dpi)
        stage_timings["acquisition_ms"] = (time.perf_counter() - acq_start) * 1000
        structured_log("acquisition", doc_id, stage_timings["acquisition_ms"])

        all_quality_reports: list[QualityReport] = []
        all_regions: list[DocumentRegion] = []
        all_ocr_results: list[OCRResult] = []

        # Process each page
        for page_idx, page_arr in enumerate(pages, start=1):
            # 2. Quality Assessment
            q_start = time.perf_counter()
            q_report = self.quality_stage.assess_page(page_arr, page=page_idx)
            all_quality_reports.append(q_report)
            stage_timings[f"p{page_idx}_quality_ms"] = (time.perf_counter() - q_start) * 1000
            structured_log(
                "quality",
                doc_id,
                stage_timings[f"p{page_idx}_quality_ms"],
                confidence=q_report.overall_score,
                extra={"enhancement_required": q_report.enhancement_required},
            )

            # 3. Enhancement (Conditional Restormer)
            enh_start = time.perf_counter()
            processed_image, was_enhanced = self.enhancement_stage.process(page_arr, q_report)
            enh_ms = (time.perf_counter() - enh_start) * 1000
            stage_timings[f"p{page_idx}_enhancement_ms"] = enh_ms
            restormer_ver = (
                self.model_manager.get_model("restormer").get_model_version()
                if was_enhanced
                else "bypassed"
            )
            stage_versions["enhancement"] = restormer_ver
            structured_log(
                "enhancement",
                doc_id,
                enh_ms,
                model_version=restormer_ver,
                extra={"was_enhanced": was_enhanced},
            )

            # 4. Detection (RT-DETR)
            det_start = time.perf_counter()
            regions = self.detection_stage.process(processed_image, page=page_idx)
            det_ms = (time.perf_counter() - det_start) * 1000
            stage_timings[f"p{page_idx}_detection_ms"] = det_ms
            rtdetr_ver = self.model_manager.get_model("rt-detr").get_model_version()
            stage_versions["detection"] = rtdetr_ver
            structured_log(
                "detection",
                doc_id,
                det_ms,
                model_version=rtdetr_ver,
                extra={"region_count": len(regions)},
            )

            # 5. Understanding (LayoutLMv3)
            und_start = time.perf_counter()
            ordered_regions = self.understanding_stage.process(regions, processed_image)
            und_ms = (time.perf_counter() - und_start) * 1000
            stage_timings[f"p{page_idx}_understanding_ms"] = und_ms
            layoutlm_ver = self.model_manager.get_model("layoutlmv3").get_model_version()
            stage_versions["understanding"] = layoutlm_ver
            structured_log(
                "understanding",
                doc_id,
                und_ms,
                model_version=layoutlm_ver,
            )
            all_regions.extend(ordered_regions)

            # 6. Extraction (PP-OCR, TrOCR, Fusion)
            ext_start = time.perf_counter()
            ocr_results = self.extraction_stage.process(
                processed_image, ordered_regions, page=page_idx
            )
            ext_ms = (time.perf_counter() - ext_start) * 1000
            stage_timings[f"p{page_idx}_extraction_ms"] = ext_ms
            ppocr_ver = self.model_manager.get_model("ppocr").get_model_version()
            trocr_ver = self.model_manager.get_model("trocr").get_model_version()
            stage_versions["printed_ocr"] = ppocr_ver
            stage_versions["handwritten_ocr"] = trocr_ver

            mean_conf = (
                sum(r.confidence for r in ocr_results) / len(ocr_results)
                if ocr_results
                else 0.0
            )
            structured_log(
                "extraction",
                doc_id,
                ext_ms,
                confidence=mean_conf,
                extra={"lines_extracted": len(ocr_results)},
            )
            all_ocr_results.extend(ocr_results)

        # 7. Normalization
        norm_start = time.perf_counter()
        norm_res = self.normalization_stage.process(
            ocr_results=all_ocr_results,
            document_id=doc_id,
            patient_id=patient_id,
            pipeline_version=self.pipeline_config.pipeline_version,
            document_date=document_date,
        )
        norm_ms = (time.perf_counter() - norm_start) * 1000
        stage_timings["normalization_ms"] = norm_ms
        structured_log(
            "normalization",
            doc_id,
            norm_ms,
            extra={"entities_found": len(norm_res.entities)},
        )

        # 8. Structuring (Confidence Propagation & Provenance)
        struct_start = time.perf_counter()
        stage_timings["total_ms"] = (time.perf_counter() - t0) * 1000
        result = self.structuring_stage.process(
            document_id=doc_id,
            source_file=path.name,
            pipeline_version=self.pipeline_config.pipeline_version,
            quality_reports=all_quality_reports,
            regions=all_regions,
            ocr_results=all_ocr_results,
            entities=norm_res.entities,
            sections_seen=norm_res.sections_seen,
            unmatched_lines=norm_res.unmatched_lines,
            ambiguous_lines=norm_res.ambiguous_lines,
            low_confidence_lines=norm_res.low_confidence_lines,
            stage_timings=stage_timings,
            stage_versions=stage_versions,
            model_metadata=self.model_manager.get_all_metadata(),
            memory_status=self.model_manager.get_memory_status(),
        )
        stage_timings["structuring_ms"] = (time.perf_counter() - struct_start) * 1000
        structured_log(
            "structuring",
            doc_id,
            stage_timings["structuring_ms"],
            confidence=result.mean_confidence,
        )

        # 9. Validation
        val_start = time.perf_counter()
        val_report = self.validation_stage.process(result, document_type=document_type)
        val_ms = (time.perf_counter() - val_start) * 1000
        stage_timings["validation_ms"] = val_ms
        structured_log(
            "validation",
            doc_id,
            val_ms,
            extra={"passed": val_report.passed, "warnings": len(val_report.warnings)},
        )

        log.info(
            "Completed document processing for %s in %.1fms (%d lines, %d facts)",
            doc_id,
            stage_timings["total_ms"],
            len(all_ocr_results),
            len(result.entities),
        )
        return result
