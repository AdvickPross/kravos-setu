"""Configuration loader and schema validation for pipeline and policy configs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PIPELINE_CONFIG = REPO_ROOT / "config" / "pipeline.json"
DEFAULT_POLICY_CONFIG = REPO_ROOT / "config" / "policy.json"


@dataclass(frozen=True)
class QualityPolicy:
    blur_threshold: float = 120.0
    skew_threshold_degrees: float = 1.0
    contrast_threshold: float = 0.35
    brightness_min: float = 40.0
    brightness_max: float = 240.0
    noise_speckle_density_threshold: float = 0.04
    min_line_confidence: float = 0.50


@dataclass(frozen=True)
class RoutingPolicy:
    handwriting_threshold: float = 0.60
    fusion_threshold: float = 0.35
    fallback_to_printed: bool = True
    special_regions: dict[str, str] = field(
        default_factory=lambda: {
            "barcode": "barcode_decoder",
            "table": "table_parser",
            "signature": "signature_meta",
            "stamp": "stamp_meta",
        }
    )


@dataclass(frozen=True)
class MemoryPolicy:
    target_hardware: str = "Jetson Orin Nano (8GB unified memory)"
    max_resident_models: int = 2
    idle_timeout_seconds: int = 120
    device_preference: str = "auto"
    fallback_to_cpu: bool = True


@dataclass(frozen=True)
class ValidationPolicy:
    confidence_floor: float = 0.40
    strict_mode: bool = False
    reject_empty: bool = True


@dataclass(frozen=True)
class PolicyConfig:
    policy_version: str
    quality: QualityPolicy
    enhancement: dict[str, Any]
    routing: RoutingPolicy
    memory: MemoryPolicy
    validation: ValidationPolicy

    @classmethod
    def load(cls, path: Path | str = DEFAULT_POLICY_CONFIG) -> PolicyConfig:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            policy_version=raw.get("policy_version", "2.0.0"),
            quality=QualityPolicy(**raw.get("quality", {})),
            enhancement=raw.get("enhancement", {}),
            routing=RoutingPolicy(**raw.get("routing", {})),
            memory=MemoryPolicy(**raw.get("memory", {})),
            validation=ValidationPolicy(**raw.get("validation", {})),
        )


@dataclass(frozen=True)
class PipelineConfig:
    pipeline_version: str
    stages: list[str]
    models: dict[str, str]
    document_types: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PIPELINE_CONFIG) -> PipelineConfig:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            pipeline_version=raw.get("pipeline_version", "2.0.0"),
            stages=raw.get("stages", []),
            models=raw.get("models", {}),
            document_types=raw.get("document_types", {}),
        )
