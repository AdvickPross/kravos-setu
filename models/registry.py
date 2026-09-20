"""Centralized model registry for document intelligence models."""

from __future__ import annotations

import logging
from typing import Callable, Type

from models.base import BaseModelAdapter

log = logging.getLogger(__name__)


class ModelRegistry:
    """Registry maintaining factory mappings for vision and OCR model adapters."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], Type[BaseModelAdapter]]] = {}
        self._classes: dict[str, Type[BaseModelAdapter]] = {}

    def register(
        self, name: str, adapter_cls_or_factory: Type[BaseModelAdapter] | Callable[[], Type[BaseModelAdapter]]
    ) -> None:
        key = name.lower().strip()
        if isinstance(adapter_cls_or_factory, type) and issubclass(adapter_cls_or_factory, BaseModelAdapter):
            self._classes[key] = adapter_cls_or_factory
        elif callable(adapter_cls_or_factory):
            self._factories[key] = adapter_cls_or_factory
        else:
            raise TypeError(f"Expected BaseModelAdapter subclass or factory, got {type(adapter_cls_or_factory)}")
        log.debug("Registered model adapter '%s'", key)

    def get(self, name: str) -> Type[BaseModelAdapter]:
        key = name.lower().strip()
        if key in self._classes:
            return self._classes[key]
        if key in self._factories:
            cls = self._factories[key]()
            self._classes[key] = cls
            return cls
        raise KeyError(f"No model adapter registered for '{name}'. Available: {self.list_models()}")

    def list_models(self) -> list[str]:
        return sorted(set(list(self._classes.keys()) + list(self._factories.keys())))

    def has_model(self, name: str) -> bool:
        return name.lower().strip() in self._classes or name.lower().strip() in self._factories


# Global default registry
registry = ModelRegistry()
default_registry = registry


def _get_restormer():
    from models.restormer_adapter import RestormerAdapter

    return RestormerAdapter


def _get_rtdetr():
    from models.rtdetr_adapter import RTDetrAdapter

    return RTDetrAdapter


def _get_layoutlmv3():
    from models.layoutlmv3_adapter import LayoutLMv3Adapter

    return LayoutLMv3Adapter


def _get_ppocr():
    from models.ppocr_adapter import PPOCRAdapter

    return PPOCRAdapter


def _get_trocr():
    from models.trocr_adapter import TrOCRAdapter

    return TrOCRAdapter


registry.register("restormer", _get_restormer)
registry.register("rt-detr", _get_rtdetr)
registry.register("rtdetr", _get_rtdetr)
registry.register("layoutlmv3", _get_layoutlmv3)
registry.register("ppocr", _get_ppocr)
registry.register("paddleocr", _get_ppocr)
registry.register("trocr", _get_trocr)
