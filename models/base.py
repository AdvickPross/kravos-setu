"""Common model adapter abstraction for all pipeline vision & OCR models."""

from __future__ import annotations

import abc
from enum import Enum
from pathlib import Path
import time
from typing import Any


class ModelStatus(str, Enum):
    """Truthful lifecycle and inference status of a model adapter."""

    ACTIVE_NEURAL_MODEL = "ACTIVE_NEURAL_MODEL"
    FALLBACK = "FALLBACK"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


def has_local_hf_checkpoint(repo_or_path: str) -> bool:
    """Check if model checkpoint exists on local disk or in HuggingFace cache without importing heavy libraries."""
    if not repo_or_path:
        return False
    path = Path(repo_or_path)
    if path.exists():
        return True
    safe_name = "models--" + repo_or_path.replace("/", "--")
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub" / safe_name
    return cache_dir.exists()


class BaseModelAdapter(abc.ABC):
    """Abstract base adapter establishing the common lifecycle contract for all models."""

    def __init__(
        self,
        model_name: str,
        device: str = "cpu",
        config: dict[str, Any] | None = None,
        precision: str = "auto",
    ) -> None:
        self.model_name = model_name
        self._device = device
        self.config = config or {}
        self.precision = precision
        self._resolved_precision = "fp32"
        self._is_loaded = False
        self._last_used: float = time.time()
        self._memory_footprint_mb: float = 0.0

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    @property
    def device(self) -> str:
        return self._device

    @property
    def resolved_precision(self) -> str:
        return self._resolved_precision

    @property
    def memory_footprint_mb(self) -> float:
        return self._memory_footprint_mb

    @property
    def last_used(self) -> float:
        return self._last_used

    @property
    def status(self) -> ModelStatus:
        """Truthful operational status of this adapter."""
        if not self._is_loaded:
            return ModelStatus.UNAVAILABLE
        if self.is_fallback:
            return ModelStatus.FALLBACK
        return ModelStatus.ACTIVE_NEURAL_MODEL

    @property
    def is_fallback(self) -> bool:
        """True if the adapter is using classical CV or spatial heuristics instead of neural model."""
        return False

    @property
    def fallback_type(self) -> str | None:
        """Name of the classical heuristic algorithm used when in fallback mode."""
        return None

    @property
    def checkpoint_path(self) -> str | None:
        """Local file path or HuggingFace identifier for the checkpoint if applicable."""
        return None

    @property
    def backend_name(self) -> str:
        """Human-readable backend engine description."""
        return "base"

    def touch(self) -> None:
        self._last_used = time.time()

    @abc.abstractmethod
    def load(self) -> None:
        """Instantiate model weights into device memory."""
        ...

    @abc.abstractmethod
    def unload(self) -> None:
        """Release model weights and free device memory."""
        ...

    @abc.abstractmethod
    def predict(self, *args: Any, **kwargs: Any) -> Any:
        """Execute inference on the inputs."""
        ...

    @abc.abstractmethod
    def get_model_version(self) -> str:
        """Return semantic or checkpoint version string for provenance."""
        ...

    def get_metadata(self) -> dict[str, Any]:
        """Return truthful runtime metadata describing active execution backend and model state."""
        return {
            "name": self.model_name,
            "model": self.model_name,
            "status": self.status.value,
            "checkpoint": self.checkpoint_path,
            "backend": self.backend_name,
            "device": self.device,
            "version": self.get_model_version() if self.is_loaded else "unloaded",
            "fallback": self.is_fallback,
            "fallback_type": self.fallback_type,
            "is_neural": not self.is_fallback and self.is_loaded,
            "weights_loaded": self.is_loaded and not self.is_fallback,
            "execution_device": self.device,
            "precision": self.resolved_precision,
            "memory_footprint_mb": self.memory_footprint_mb,
        }

    def ensure_loaded(self) -> None:
        """Helper to ensure the model is loaded before inference."""
        if not self._is_loaded:
            self.load()
        self.touch()
