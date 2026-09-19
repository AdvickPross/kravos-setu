"""Centralized model lifecycle and memory management for Jetson Orin Nano and Cloud environments."""

from __future__ import annotations

import gc
import logging
import time
from typing import Any

from config.loader import MemoryPolicy, PolicyConfig
from models.base import BaseModelAdapter
from models.registry import ModelRegistry, registry as default_registry

log = logging.getLogger(__name__)


class ModelManager:
    """Manages lazy instantiation, caching, device assignment, and memory eviction of models."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        policy: MemoryPolicy | None = None,
        max_resident_models: int | None = None,
        device: str | None = None,
    ) -> None:
        self.registry = registry or default_registry
        if policy is not None:
            self.policy = policy
        else:
            try:
                self.policy = PolicyConfig.load().memory
            except Exception:
                self.policy = MemoryPolicy()

        self.max_resident_models = (
            max_resident_models if max_resident_models is not None else self.policy.max_resident_models
        )
        self.idle_timeout_seconds = self.policy.idle_timeout_seconds
        self._instances: dict[str, BaseModelAdapter] = {}
        self._device: str = device or self._resolve_device(self.policy.device_preference)


    def _resolve_device(self, preference: str = "auto") -> str:
        """Resolve target device with automatic CUDA detection and CPU fallback."""
        if preference == "cpu":
            return "cpu"
        try:
            import torch

            if torch.cuda.is_available():
                log.info("CUDA detected. Using GPU for pipeline models.")
                return "cuda"
        except Exception as exc:
            log.warning("CUDA check raised an error: %s. Defaulting to CPU.", exc)
        return "cpu"

    @property
    def device(self) -> str:
        return self._device

    def get_loaded_models(self) -> list[str]:
        """Return list of currently resident model keys."""
        return [k for k, adapter in self._instances.items() if adapter.is_loaded]

    def _free_memory(self) -> None:
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def evict_lru_if_needed(self, incoming_key: str | None = None) -> None:
        """Evict oldest loaded models to stay within the max resident memory budget."""
        loaded_keys = [k for k, a in self._instances.items() if a.is_loaded and k != incoming_key]
        while len(loaded_keys) >= self.max_resident_models:
            # Pick oldest by last_used timestamp
            oldest_key = min(loaded_keys, key=lambda k: self._instances[k].last_used)
            log.info(
                "Memory constraint (%d resident models reached): Unloading model '%s' (idle for %.1fs)",
                self.max_resident_models,
                oldest_key,
                time.time() - self._instances[oldest_key].last_used,
            )
            self._instances[oldest_key].unload()
            loaded_keys.remove(oldest_key)
        self._free_memory()

    def get_model(self, name: str, config: dict[str, Any] | None = None) -> BaseModelAdapter:
        """Retrieve a model adapter by name, instantiating and loading it lazily on demand."""
        key = name.lower().strip()

        if key not in self._instances:
            adapter_cls = self.registry.get(key)
            self._instances[key] = adapter_cls(model_name=key, device=self._device, config=config)

        adapter = self._instances[key]
        if not adapter.is_loaded:
            self.evict_lru_if_needed(incoming_key=key)
            log.info("Lazy loading model '%s' on device '%s'...", key, self._device)
            try:
                adapter.load()
            except Exception as exc:
                if self._device == "cuda" and self.policy.fallback_to_cpu:
                    log.warning("Failed loading '%s' on CUDA (%s). Retrying on CPU...", key, exc)
                    adapter._device = "cpu"
                    adapter.load()
                else:
                    raise
        adapter.touch()
        return adapter

    def unload(self, name: str) -> bool:
        """Explicitly unload a model by name."""
        key = name.lower().strip()
        if key in self._instances and self._instances[key].is_loaded:
            log.info("Unloading model '%s'", key)
            self._instances[key].unload()
            self._free_memory()
            return True
        return False

    def unload_idle(self, timeout_seconds: float | None = None) -> int:
        """Unload all models that have not been accessed within timeout_seconds."""
        timeout = timeout_seconds if timeout_seconds is not None else self.idle_timeout_seconds
        now = time.time()
        unloaded_count = 0

        for key, adapter in list(self._instances.items()):
            if adapter.is_loaded and (now - adapter.last_used) > timeout:
                log.info("Model '%s' exceeded idle timeout (%.1fs). Unloading.", key, now - adapter.last_used)
                adapter.unload()
                unloaded_count += 1

        if unloaded_count > 0:
            self._free_memory()
        return unloaded_count

    def unload_all(self) -> None:
        """Unload all resident models."""
        for adapter in self._instances.values():
            if adapter.is_loaded:
                adapter.unload()
        self._free_memory()

    def memory_report(self) -> dict[str, Any]:
        """Produce a structured status report of resident models and memory state."""
        loaded = self.get_loaded_models()
        total_footprint = sum(self._instances[k].memory_footprint_mb for k in loaded)
        return {
            "target_hardware": self.policy.target_hardware,
            "device": self._device,
            "max_resident_models": self.max_resident_models,
            "loaded_models": loaded,
            "loaded_count": len(loaded),
            "estimated_footprint_mb": round(total_footprint, 2),
            "registered_models": self.registry.list_models(),
        }
