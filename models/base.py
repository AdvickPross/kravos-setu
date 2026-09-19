"""Common model adapter abstraction for all pipeline vision & OCR models."""

from __future__ import annotations

import abc
from pathlib import Path
import time
from typing import Any


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
            "model": self.model_name,
            "backend": "base",
            "weights_loaded": self.is_loaded,
            "execution_device": self.device,
            "precision": self.resolved_precision,
            "memory_footprint_mb": self.memory_footprint_mb,
            "version": self.get_model_version() if self.is_loaded else "unloaded",
        }

    def ensure_loaded(self) -> None:
        """Helper to ensure the model is loaded before inference."""
        if not self._is_loaded:
            self.load()
        self.touch()
