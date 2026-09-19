"""Validation stage checking structured document facts against clinical rules and policies."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from config.loader import PolicyConfig, ValidationPolicy
from pipeline.schemas.canonical import PipelineResult

log = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ValidationStage:
    """Validates structured document output against policy rules and mandatory clinical sections."""

    def __init__(self, policy: ValidationPolicy | None = None) -> None:
        if policy is not None:
            self.policy = policy
        else:
            try:
                self.policy = PolicyConfig.load().validation
            except Exception:
                self.policy = ValidationPolicy()

    def process(
        self, result: PipelineResult, document_type: str = "discharge_summary"
    ) -> ValidationReport:
        errors: list[str] = []
        warnings: list[str] = []

        # 1. Non-empty check
        if self.policy.reject_empty and not result.ocr_results:
            errors.append("Document yielded zero text lines (possible blank page or corrupt scan)")

        # 2. Confidence floor check
        low_facts = [
            e for e in result.entities if e.confidence < self.policy.confidence_floor
        ]
        if low_facts:
            warnings.append(
                f"{len(low_facts)} facts fell below the confidence floor ({self.policy.confidence_floor})"
            )

        # 3. Mandatory clinical section checks based on document type
        from config.loader import PipelineConfig

        try:
            p_config = PipelineConfig.load()
            doc_type_rules = p_config.document_types.get(document_type, {})
            mandatory = doc_type_rules.get("mandatory_sections", [])
            for section in mandatory:
                if section not in result.sections_seen:
                    warnings.append(f"Mandatory section '{section}' was not identified on scan")
        except Exception:
            pass

        passed = len(errors) == 0
        log.info(
            "Validation result: passed=%s, errors=%d, warnings=%d",
            passed,
            len(errors),
            len(warnings),
        )
        return ValidationReport(passed=passed, errors=errors, warnings=warnings)
