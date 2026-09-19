"""Hardware-agnostic model lifecycle and memory manager for laptop-first and edge execution."""

from __future__ import annotations

import gc
import logging
import os
import time
from typing import Any

from config.loader import MemoryPolicy, PolicyConfig
from models.base import BaseModelAdapter
from models.registry import ModelRegistry, registry as default_registry

log = logging.getLogger(__name__)


def get_process_rss_mb() -> float | None:
    """Return current process Resident Set Size (RSS) in megabytes."""
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / (1024 * 1024), 2)
    except Exception:
        pass
    try:
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)
    except Exception:
        pass
    return None


def configure_cpu_threads(threads: str | int = "auto") -> int:
    """Configure CPU thread parallelism across PyTorch and OpenCV without oversubscription."""
    if isinstance(threads, str) and threads.strip().lower() == "auto":
        # Sensible laptop default: clamp to min(8, cpu_count)
        count = min(8, os.cpu_count() or 4)
    else:
        try:
            count = max(1, int(threads))
        except (ValueError, TypeError):
            count = min(8, os.cpu_count() or 4)

    try:
        import torch

        torch.set_num_threads(count)
    except Exception:
        pass

    try:
        import cv2

        cv2.setNumThreads(count)
    except Exception:
        pass

    log.info("Configured CPU execution threads: %d", count)
    return count


class ModelManager:
    """Manages lazy instantiation, caching, device assignment, and memory eviction of models.

    Designed to be hardware-agnostic: supports laptop-first execution (keeping models resident
    with zero eviction overhead and automatic CUDA acceleration) while retaining configurable
    LRU eviction policies for resource-constrained environments.
    """

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        policy: MemoryPolicy | None = None,
        max_resident_models: int | None = None,
        device: str | None = None,
        enable_eviction: bool | None = None,
        precision: str | None = None,
        cpu_threads: str | int | None = None,
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
        self.enable_eviction = (
            enable_eviction if enable_eviction is not None else self.policy.enable_eviction
        )
        self.idle_timeout_seconds = self.policy.idle_timeout_seconds
        self._instances: dict[str, BaseModelAdapter] = {}

        # Resolve compute device
        device_pref = device or self.policy.device_preference
        self._device: str = self._resolve_device(device_pref, fallback_to_cpu=self.policy.fallback_to_cpu)

        # Resolve numerical precision
        precision_pref = precision or self.policy.precision
        self._precision: str = self._resolve_precision(precision_pref, device=self._device)

        # Configure CPU threading
        threads_pref = cpu_threads or self.policy.cpu_threads
        self._configured_threads = configure_cpu_threads(threads_pref)

    def _resolve_device(self, preference: str = "auto", fallback_to_cpu: bool = True) -> str:
        """Resolve target device with automatic CUDA detection and safe CPU fallback."""
        pref = (preference or "auto").strip().lower()

        if pref == "cpu":
            return "cpu"

        if pref == "cuda":
            cuda_avail = False
            try:
                import torch

                cuda_avail = torch.cuda.is_available()
            except Exception:
                cuda_avail = False

            if cuda_avail:
                return "cuda"
            if fallback_to_cpu:
                log.warning("CUDA was explicitly requested but is unavailable on this machine. Falling back to CPU.")
                return "cpu"
            raise ValueError("CUDA was explicitly requested but is not available on this system.")

        if pref == "auto":
            try:
                import torch

                if torch.cuda.is_available():
                    log.info("CUDA detected. Auto-selected GPU device for pipeline models.")
                    return "cuda"
            except Exception as exc:
                log.debug("CUDA auto-detection exception: %s", exc)
            return "cpu"

        raise ValueError(f"Invalid device '{preference}'. Expected 'auto', 'cuda', or 'cpu'.")

    def _resolve_precision(self, preference: str = "auto", device: str = "cpu") -> str:
        """Resolve inference precision (fp16 on CUDA, fp32 on CPU)."""
        pref = (preference or "auto").strip().lower()

        if pref == "auto":
            return "fp16" if device == "cuda" else "fp32"

        if pref == "fp16":
            if device != "cuda":
                log.warning("FP16 requested on CPU device. Falling back to FP32 for numerical stability.")
                return "fp32"
            return "fp16"

        if pref in ("fp32", "float32"):
            return "fp32"

        log.warning("Unknown precision '%s'; defaulting to fp32", preference)
        return "fp32"

    @property
    def device(self) -> str:
        return self._device

    @property
    def precision(self) -> str:
        return self._precision

    @property
    def configured_threads(self) -> int:
        return self._configured_threads

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
        """Evict oldest loaded models to stay within memory budget if eviction is enabled."""
        if not self.enable_eviction:
            return

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
            self._instances[key] = adapter_cls(
                model_name=key,
                device=self._device,
                config=config,
                precision=self._precision,
            )

        adapter = self._instances[key]
        if not adapter.is_loaded:
            self.evict_lru_if_needed(incoming_key=key)
            log.info(
                "Lazy loading model '%s' on device '%s' with precision '%s'...",
                key,
                self._device,
                self._precision,
            )
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

    def get_models_metadata(self) -> dict[str, dict[str, Any]]:
        """Return truthful runtime metadata for all registered models."""
        for key in self.registry.list_models():
            if key not in self._instances:
                adapter_cls = self.registry.get(key)
                self._instances[key] = adapter_cls(
                    model_name=key,
                    device=self._device,
                    precision=self._precision,
                )
        return {key: adapter.get_metadata() for key, adapter in self._instances.items()}

    def get_all_metadata(self) -> dict[str, dict[str, Any]]:
        return self.get_models_metadata()

    def memory_report(self) -> dict[str, Any]:
        """Produce a structured status report of resident models, CPU RAM, and GPU VRAM."""
        loaded = self.get_loaded_models()
        total_footprint = sum(self._instances[k].memory_footprint_mb for k in loaded if k in self._instances)
        rss_mb = get_process_rss_mb()

        report: dict[str, Any] = {
            "target_hardware": getattr(self.policy, "profile_name", "laptop"),
            "device": self._device,
            "precision": self._precision,
            "eviction_enabled": self.enable_eviction,
            "max_resident_models": self.max_resident_models,
            "loaded_models": loaded,
            "loaded_count": len(loaded),
            "estimated_footprint_mb": round(total_footprint, 2),
            "registered_models": self.registry.list_models(),
            "cpu_rss_mb": rss_mb,
            "process_rss_mb": rss_mb,
            "cpu_threads": self._configured_threads,
            "cuda_vram": None,
        }

        # Query GPU VRAM metrics if CUDA is active
        if self._device == "cuda":
            try:
                import torch

                if torch.cuda.is_available():
                    props = torch.cuda.get_device_properties(0)
                    report["gpu_name"] = props.name
                    report["gpu_total_mb"] = round(props.total_memory / (1024 * 1024), 2)
                    report["gpu_allocated_mb"] = round(torch.cuda.memory_allocated(0) / (1024 * 1024), 2)
                    report["gpu_reserved_mb"] = round(torch.cuda.memory_reserved(0) / (1024 * 1024), 2)
                    report["gpu_peak_allocated_mb"] = round(torch.cuda.max_memory_allocated(0) / (1024 * 1024), 2)
                    report["cuda_vram"] = {
                        "name": props.name,
                        "total_mb": report["gpu_total_mb"],
                        "allocated_mb": report["gpu_allocated_mb"],
                        "reserved_mb": report["gpu_reserved_mb"],
                        "peak_allocated_mb": report["gpu_peak_allocated_mb"],
                    }
            except Exception as exc:
                report["gpu_query_error"] = str(exc)

        return report

    def get_memory_status(self) -> dict[str, Any]:
        return self.memory_report()
