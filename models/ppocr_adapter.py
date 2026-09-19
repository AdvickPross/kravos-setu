"""PP-OCR (PaddleOCR ONNX) adapter for printed text detection and recognition."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from PIL import Image

from models.base import BaseModelAdapter
from pipeline.schemas.canonical import BoundingBox, OCRResult

log = logging.getLogger(__name__)


class PPOCRAdapter(BaseModelAdapter):
    """Adapter wrapping RapidOCR (PaddleOCR ONNX) for printed text extraction."""

    def __init__(
        self,
        model_name: str = "ppocr",
        device: str = "cpu",
        precision: str = "auto",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(model_name=model_name, device=device, precision=precision, config=config)
        self._reader = None
        self._version = "ppocr-v4-onnx"
        self._checkpoint_path = self.config.get("checkpoint_path", "rapidocr-v4-builtin")

    def get_model_version(self) -> str:
        return self._version

    @property
    def is_fallback(self) -> bool:
        return False

    @property
    def fallback_type(self) -> str | None:
        return None

    @property
    def checkpoint_path(self) -> str | None:
        return self._checkpoint_path

    @property
    def backend_name(self) -> str:
        return "rapidocr-onnx"

    def _get_active_provider(self) -> str:
        if self._reader is not None:
            try:
                det = getattr(self._reader, "text_det", None)
                infer = getattr(det, "infer", None)
                sess = getattr(infer, "session", None)
                if sess and hasattr(sess, "get_providers"):
                    providers = sess.get_providers()
                    if providers:
                        return str(providers[0])
            except Exception:
                pass
        return "CUDAExecutionProvider" if self.device == "cuda" else "CPUExecutionProvider"

    def get_metadata(self) -> dict[str, Any]:
        """Return truthful runtime metadata describing active execution backend and model state."""
        meta = super().get_metadata()
        active_ep = self._get_active_provider() if self._is_loaded else "unloaded"
        meta["execution_provider"] = active_ep
        return meta

    def load(self) -> None:
        if self._is_loaded:
            return
        try:
            from rapidocr_onnxruntime import RapidOCR

            use_cuda = (self.device == "cuda")
            log.info("Loading RapidOCR (PP-OCR ONNX models, use_cuda=%s)...", use_cuda)
            self._reader = RapidOCR(use_cuda=use_cuda)
            self._memory_footprint_mb = 140.0
        except ImportError as exc:
            raise RuntimeError(
                "rapidocr-onnxruntime is required for PP-OCR. Install it via pip/uv."
            ) from exc

        self._is_loaded = True
        self.touch()

    def unload(self) -> None:
        self._reader = None
        self._is_loaded = False
        self._memory_footprint_mb = 0.0

    def predict(
        self,
        image: np.ndarray | Image.Image,
        page: int = 1,
        region_id: str | None = None,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> list[OCRResult]:
        """Perform printed OCR on an image or ROI crop.

        offset_x and offset_y map coordinates back to page-level space when running on a crop.
        """
        self.ensure_loaded()

        if isinstance(image, Image.Image):
            arr = np.array(image.convert("RGB"))
        elif isinstance(image, np.ndarray):
            arr = image
        else:
            raise TypeError(f"Expected numpy array or PIL Image, got {type(image)}")

        # RapidOCR returns (results, elapsed_time)
        results, _ = self._reader(arr)
        ocr_results: list[OCRResult] = []
        if not results:
            return ocr_results

        for line_idx, item in enumerate(results, start=1):
            box, text, score = item[0], item[1], item[2]
            text = (text or "").strip()
            if not text:
                continue

            xs = [point[0] + offset_x for point in box]
            ys = [point[1] + offset_y for point in box]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)

            conf = float(score) if score is not None else 0.0
            ocr_results.append(
                OCRResult(
                    text=text,
                    confidence=conf,
                    bbox=BoundingBox(x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1)),
                    page=page,
                    line_no=line_idx,
                    region_id=region_id,
                    model=self.model_name,
                    model_version=self.get_model_version(),
                    is_handwritten=False,
                    is_reliable=(conf >= 0.50),
                )
            )

        return ocr_results
