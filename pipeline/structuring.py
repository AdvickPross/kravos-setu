"""Structuring stage with minimum-across-stages confidence propagation and provenance assembly."""

from __future__ import annotations

import logging
from typing import Any

from pipeline.schemas.canonical import (
    ClinicalEntity,
    DocumentRegion,
    OCRResult,
    PipelineResult,
    Provenance,
    QualityReport,
)

log = logging.getLogger(__name__)


class StructuringStage:
    """Consolidates facts with minimum-across-stages confidence propagation and provenance."""

    def process(
        self,
        document_id: str,
        source_file: str,
        pipeline_version: str,
        quality_reports: list[QualityReport],
        regions: list[DocumentRegion],
        ocr_results: list[OCRResult],
        entities: list[ClinicalEntity],
        sections_seen: list[str],
        unmatched_lines: list[str],
        ambiguous_lines: list[str],
        low_confidence_lines: list[str],
        stage_timings: dict[str, float] | None = None,
        stage_versions: dict[str, str] | None = None,
        model_metadata: dict[str, Any] | None = None,
        memory_status: dict[str, Any] | None = None,
    ) -> PipelineResult:
        """Apply confidence propagation (min-across-stages) and assemble final PipelineResult."""
        quality_map = {q.page: q.overall_score for q in quality_reports}
        region_map = {r.region_id: r.confidence for r in regions if r.region_id}

        calibrated_entities: list[ClinicalEntity] = []

        for entity in entities:
            # Stage confidences
            page_quality_conf = quality_map.get(entity.provenance.page, 1.0)
            region_det_conf = (
                region_map.get(entity.provenance.region_id, 1.0)
                if entity.provenance.region_id
                else 1.0
            )
            ocr_conf = entity.confidence

            # Strict Minimum-Across-Stages Rule (not average!)
            overall_conf = min(page_quality_conf, region_det_conf, ocr_conf)

            calibrated = ClinicalEntity(
                fact_id=entity.fact_id,
                patient_id=entity.patient_id,
                category=entity.category,
                safety_class=entity.safety_class,
                text=entity.text,
                display=entity.display,
                code=entity.code,
                code_system=entity.code_system,
                value=entity.value,
                unit=entity.unit,
                date=entity.date,
                confidence=round(overall_conf, 3),
                provenance=entity.provenance,
                line_no=entity.line_no,
                line_text=entity.line_text,
                status=entity.status,
                also_in=entity.also_in,
            )
            calibrated_entities.append(calibrated)

        mean_conf = (
            sum(r.confidence for r in ocr_results) / len(ocr_results)
            if ocr_results
            else 0.0
        )

        return PipelineResult(
            document_id=document_id,
            source_file=source_file,
            pipeline_version=pipeline_version,
            quality_reports=quality_reports,
            regions=regions,
            ocr_results=ocr_results,
            entities=calibrated_entities,
            sections_seen=sections_seen,
            unmatched=unmatched_lines,
            ambiguous=ambiguous_lines,
            low_confidence=low_confidence_lines,
            stage_timings=stage_timings or {},
            stage_versions=stage_versions or {},
            mean_confidence=round(mean_conf, 3),
            model_metadata=model_metadata or {},
            memory_status=memory_status or {},
        )
