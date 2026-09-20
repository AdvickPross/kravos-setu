"""Configuration loader and schema validation for pipeline, policy, and hardware profile configs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PIPELINE_CONFIG = REPO_ROOT / "config" / "pipeline.json"
DEFAULT_POLICY_CONFIG = REPO_ROOT / "config" / "policy.json"
PROFILES_DIR = REPO_ROOT / "config" / "profiles"


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
class HardwareProfile:
    """Hardware and execution runtime profile (e.g. laptop, jetson, cloud)."""

    profile_name: str = "laptop"
    device: str = "auto"
    fallback_to_cpu: bool = True
    max_resident_models: int = 5
    enable_eviction: bool = False
    idle_timeout_seconds: int = 1800
    precision: str = "auto"
    cpu_threads: str | int = "auto"
    pdf_dpi: int = 250

    @classmethod
    def load(cls, profile_name_or_path: str | Path | None = None) -> HardwareProfile:
        """Load hardware profile with precedence:
        Hardcoded defaults -> Profile JSON -> Environment variables.
        """
        env_profile = os.environ.get("KRAVOS_PROFILE")
        target = profile_name_or_path or env_profile or "laptop"

        values: dict[str, Any] = {
            "profile_name": "laptop",
            "device": "auto",
            "fallback_to_cpu": True,
            "max_resident_models": 5,
            "enable_eviction": False,
            "idle_timeout_seconds": 1800,
            "precision": "auto",
            "cpu_threads": "auto",
            "pdf_dpi": 250,
        }

        # Check if target is a file or a named profile in config/profiles/
        target_path: Path | None = None
        if isinstance(target, Path) or str(target).endswith(".json"):
            target_path = Path(target)
        else:
            cand = PROFILES_DIR / f"{target}.json"
            if cand.exists():
                target_path = cand

        if target_path and target_path.exists():
            try:
                raw = json.loads(target_path.read_text(encoding="utf-8"))
                values["profile_name"] = raw.get("profile_name", target_path.stem)
                hw = raw.get("hardware", {})
                values["device"] = hw.get("device", values["device"])
                values["fallback_to_cpu"] = hw.get("fallback_to_cpu", values["fallback_to_cpu"])

                mem = raw.get("memory", {})
                values["max_resident_models"] = mem.get("max_resident_models", values["max_resident_models"])
                values["enable_eviction"] = mem.get("enable_eviction", values["enable_eviction"])
                values["idle_timeout_seconds"] = mem.get("idle_timeout_seconds", values["idle_timeout_seconds"])

                inf = raw.get("inference", {})
                values["precision"] = inf.get("precision", values["precision"])
                values["cpu_threads"] = inf.get("cpu_threads", values["cpu_threads"])

                pdf = raw.get("pdf", {})
                values["pdf_dpi"] = pdf.get("dpi", values["pdf_dpi"])
            except Exception:
                pass

        # Environment variable overrides
        if "KRAVOS_DEVICE" in os.environ:
            values["device"] = os.environ["KRAVOS_DEVICE"].strip().lower()
        if "KRAVOS_MAX_RESIDENT_MODELS" in os.environ:
            try:
                values["max_resident_models"] = int(os.environ["KRAVOS_MAX_RESIDENT_MODELS"])
            except ValueError:
                pass
        if "KRAVOS_MODEL_EVICTION" in os.environ:
            values["enable_eviction"] = os.environ["KRAVOS_MODEL_EVICTION"].strip().lower() in ("true", "1", "yes")
        if "KRAVOS_PRECISION" in os.environ:
            values["precision"] = os.environ["KRAVOS_PRECISION"].strip().lower()
        if "KRAVOS_CPU_THREADS" in os.environ:
            val = os.environ["KRAVOS_CPU_THREADS"].strip()
            values["cpu_threads"] = int(val) if val.isdigit() else val
        if "KRAVOS_PDF_DPI" in os.environ:
            try:
                values["pdf_dpi"] = int(os.environ["KRAVOS_PDF_DPI"])
            except ValueError:
                pass

        return cls(**values)


@dataclass(frozen=True)
class MemoryPolicy:
    target_hardware: str = "laptop"
    max_resident_models: int = 5
    enable_eviction: bool = False
    idle_timeout_seconds: int = 1800
    device_preference: str = "auto"
    fallback_to_cpu: bool = True
    precision: str = "auto"
    cpu_threads: str | int = "auto"
    pdf_dpi: int = 250
    profile_name: str = "laptop"


@dataclass(frozen=True)
class ValidationPolicy:
    confidence_floor: float = 0.40
    strict_mode: bool = False
    reject_empty: bool = True


@dataclass(frozen=True)
class ModelExecutionPolicy:
    strict: bool = False


@dataclass(frozen=True)
class ExtractionPolicy:
    enable_second_pass: bool = True
    low_confidence_threshold: float = 0.70
    high_confidence_threshold: float = 0.90
    fusion_threshold: float = 0.35


@dataclass(frozen=True)
class PolicyConfig:
    policy_version: str
    quality: QualityPolicy
    enhancement: dict[str, Any]
    routing: RoutingPolicy
    memory: MemoryPolicy
    validation: ValidationPolicy
    model_execution: ModelExecutionPolicy = field(default_factory=ModelExecutionPolicy)
    extraction: ExtractionPolicy = field(default_factory=ExtractionPolicy)
    default_profile: str = "laptop"
    pdf_dpi: int = 250

    @classmethod
    def load(
        cls,
        path: Path | str = DEFAULT_POLICY_CONFIG,
        profile: str | Path | None = None,
    ) -> PolicyConfig:
        raw: dict[str, Any] = {}
        target_path = Path(path)
        if target_path.exists():
            try:
                raw = json.loads(target_path.read_text(encoding="utf-8"))
            except Exception:
                raw = {}

        # Resolve hardware profile with environment precedence
        hw_profile = HardwareProfile.load(profile or raw.get("default_profile"))

        # Merge raw memory policy with resolved hardware profile
        raw_mem = raw.get("memory", {})
        mem_kwargs = {
            "target_hardware": hw_profile.profile_name,
            "max_resident_models": hw_profile.max_resident_models,
            "enable_eviction": hw_profile.enable_eviction,
            "idle_timeout_seconds": hw_profile.idle_timeout_seconds,
            "device_preference": hw_profile.device,
            "fallback_to_cpu": hw_profile.fallback_to_cpu,
            "precision": hw_profile.precision,
            "cpu_threads": hw_profile.cpu_threads,
            "pdf_dpi": hw_profile.pdf_dpi,
            "profile_name": hw_profile.profile_name,
        }
        # Allow raw policy overrides if specified explicitly in policy.json and not default
        for k in ("max_resident_models", "idle_timeout_seconds", "device_preference", "fallback_to_cpu"):
            if k in raw_mem and profile is None and not os.environ.get(f"KRAVOS_{k.upper()}"):
                mem_kwargs[k] = raw_mem[k]

        memory_policy = MemoryPolicy(**mem_kwargs)
        raw_exec = raw.get("model_execution", {})
        raw_ext = raw.get("extraction", {})

        return cls(
            policy_version=raw.get("policy_version", "2.0.0"),
            quality=QualityPolicy(**raw.get("quality", {})),
            enhancement=raw.get("enhancement", {}),
            routing=RoutingPolicy(**raw.get("routing", {})),
            memory=memory_policy,
            validation=ValidationPolicy(**raw.get("validation", {})),
            model_execution=ModelExecutionPolicy(**raw_exec) if isinstance(raw_exec, dict) else ModelExecutionPolicy(),
            extraction=ExtractionPolicy(**raw_ext) if isinstance(raw_ext, dict) else ExtractionPolicy(),
            default_profile=hw_profile.profile_name,
            pdf_dpi=hw_profile.pdf_dpi,
        )


@dataclass(frozen=True)
class PipelineConfig:
    pipeline_version: str
    stages: list[str]
    models: dict[str, str]
    document_types: dict[str, dict[str, Any]]
    model_execution: dict[str, Any] = field(default_factory=dict)
    second_pass: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PIPELINE_CONFIG) -> PipelineConfig:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            pipeline_version=raw.get("pipeline_version", "2.0.0"),
            stages=raw.get("stages", []),
            models=raw.get("models", {}),
            document_types=raw.get("document_types", {}),
            model_execution=raw.get("model_execution", {}),
            second_pass=raw.get("second_pass", {}),
        )
