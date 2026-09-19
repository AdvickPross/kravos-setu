"""Unit tests for ModelManager memory lifecycle management, device selection, and precision."""

from __future__ import annotations

import pytest

from config.loader import MemoryPolicy
from models.base import BaseModelAdapter
from models.manager import ModelManager
from models.registry import ModelRegistry


class DummyAdapter(BaseModelAdapter):

    def __init__(self, model_name: str, device: str = "cpu", precision: str = "auto", footprint: float = 50.0, **kwargs):
        super().__init__(model_name=model_name, device=device, precision=precision)
        self._footprint = footprint

    def load(self):
        self._is_loaded = True
        self._memory_footprint_mb = self._footprint
        self.touch()

    def unload(self):
        self._is_loaded = False
        self._memory_footprint_mb = 0.0

    def predict(self, *args, **kwargs):
        self.ensure_loaded()
        return "predicted"

    def get_model_version(self):
        return f"{self.model_name}-dummy-v1"


class DummyA(DummyAdapter):
    def __init__(self, model_name: str = "model_a", device: str = "cpu", **kw):
        super().__init__(model_name=model_name, device=device, footprint=100.0, **kw)


class DummyB(DummyAdapter):
    def __init__(self, model_name: str = "model_b", device: str = "cpu", **kw):
        super().__init__(model_name=model_name, device=device, footprint=200.0, **kw)


class DummyC(DummyAdapter):
    def __init__(self, model_name: str = "model_c", device: str = "cpu", **kw):
        super().__init__(model_name=model_name, device=device, footprint=300.0, **kw)


@pytest.fixture
def manager() -> ModelManager:
    reg = ModelRegistry()
    reg.register("model_a", DummyA)
    reg.register("model_b", DummyB)
    reg.register("model_c", DummyC)

    policy = MemoryPolicy(max_resident_models=2, enable_eviction=True, idle_timeout_seconds=5)
    return ModelManager(registry=reg, policy=policy, max_resident_models=2, enable_eviction=True)


def test_lazy_loading(manager: ModelManager):
    assert len(manager.get_loaded_models()) == 0

    m_a = manager.get_model("model_a")
    assert m_a.is_loaded
    assert manager.get_loaded_models() == ["model_a"]


def test_laptop_residency_keeps_all_models_loaded():
    """Verify that on laptop profile (enable_eviction=False), all models remain resident."""
    reg = ModelRegistry()
    reg.register("model_a", DummyA)
    reg.register("model_b", DummyB)
    reg.register("model_c", DummyC)

    policy = MemoryPolicy(max_resident_models=5, enable_eviction=False)
    mgr = ModelManager(registry=reg, policy=policy, enable_eviction=False)

    # Load all models sequentially
    mgr.get_model("model_a")
    mgr.get_model("model_b")
    mgr.get_model("model_c")

    loaded = mgr.get_loaded_models()
    assert len(loaded) == 3
    assert set(loaded) == {"model_a", "model_b", "model_c"}


def test_lru_eviction_strictly_enforces_budget(manager: ModelManager):
    # Load model A
    m_a = manager.get_model("model_a")
    # Load model B
    m_b = manager.get_model("model_b")
    assert set(manager.get_loaded_models()) == {"model_a", "model_b"}

    # Touch model A with a guaranteed higher timestamp
    m_a._last_used = m_b.last_used + 10.0

    # Loading model C must evict model B (older last_used)
    manager.get_model("model_c")
    loaded = manager.get_loaded_models()
    assert len(loaded) <= 2
    assert "model_c" in loaded
    assert "model_a" in loaded
    assert "model_b" not in loaded, "Oldest model B should have been evicted"


def test_explicit_unload(manager: ModelManager):
    manager.get_model("model_a")
    assert "model_a" in manager.get_loaded_models()

    unloaded = manager.unload("model_a")
    assert unloaded is True
    assert "model_a" not in manager.get_loaded_models()


def test_memory_report(manager: ModelManager):
    manager.get_model("model_a")
    report = manager.memory_report()
    assert report["max_resident_models"] == 2
    assert report["loaded_count"] == 1
    assert report["loaded_models"] == ["model_a"]
    assert report["estimated_footprint_mb"] == 100.0
    assert "process_rss_mb" in report


def test_device_auto_resolution():
    """Verify device auto resolution logic."""
    policy = MemoryPolicy(device_preference="cpu")
    mgr = ModelManager(policy=policy, device="cpu")
    assert mgr.device == "cpu"

    # Testing invalid device raises ValueError
    with pytest.raises(ValueError, match="Invalid device"):
        ModelManager(policy=policy, device="tpu")


def test_cuda_fallback_to_cpu_when_unavailable():
    """Verify that requesting cuda with fallback_to_cpu falls back gracefully to cpu if no cuda."""
    import torch

    policy = MemoryPolicy(device_preference="cuda", fallback_to_cpu=True)
    mgr = ModelManager(policy=policy, device="cuda")
    if not torch.cuda.is_available():
        assert mgr.device == "cpu"
    else:
        assert mgr.device == "cuda"


def test_precision_auto_resolution():
    """Verify precision auto-resolution based on device."""
    policy = MemoryPolicy(device_preference="cpu", precision="auto")
    mgr = ModelManager(policy=policy, device="cpu")
    assert mgr.precision == "fp32"

    # On CPU, FP16 safely falls back to FP32 for numerical stability
    policy_fp16 = MemoryPolicy(device_preference="cpu", precision="fp16")
    mgr_fp16 = ModelManager(policy=policy_fp16, device="cpu", precision="fp16")
    assert mgr_fp16.precision == "fp32"


def test_strict_mode_blocks_fallback_adapter():
    """Verify that strict_mode=True raises RuntimeError when a fallback adapter is requested."""
    from models.registry import default_registry

    mgr = ModelManager(registry=default_registry, strict_mode=True)
    with pytest.raises(RuntimeError, match="strict mode"):
        mgr.get_model("rt-detr")


def test_non_strict_mode_allows_fallback_adapter():
    """Verify that strict_mode=False allows fallback adapter with truthful metadata."""
    from models.base import ModelStatus
    from models.registry import default_registry

    mgr = ModelManager(registry=default_registry, strict_mode=False)
    m = mgr.get_model("rt-detr")
    assert m.is_fallback is True
    assert m.status == ModelStatus.FALLBACK
    assert m.fallback_type == "morphological_layout_segmentation"

