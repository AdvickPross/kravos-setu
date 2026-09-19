"""TrOCR adapter for handwritten text recognition in medical document ROIs."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from PIL import Image

from models.base import BaseModelAdapter, has_local_hf_checkpoint
from pipeline.schemas.canonical import BoundingBox, OCRResult

log = logging.getLogger(__name__)


class TrOCRAdapter(BaseModelAdapter):
    """Adapter for Microsoft TrOCR (Transformer OCR) specialized for handwritten text."""

    def __init__(
        self,
        model_name: str = "trocr",
        device: str = "cpu",
        checkpoint: str = "microsoft/trocr-small-handwritten",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(model_name=model_name, device=device, config=config)
        self.checkpoint = checkpoint
        self._processor = None
        self._model = None
        self._version = "trocr-v1.0"
        self._has_hf_model = False

    def get_model_version(self) -> str:
        if self._has_hf_model:
            return f"{self._version}-hf-{self.checkpoint}"
        return f"{self._version}-offline-fallback"

    def load(self) -> None:
        if self._is_loaded:
            return

        if not has_local_hf_checkpoint(self.checkpoint):
            log.info(
                "Local HuggingFace TrOCR checkpoint '%s' not cached. Using offline handwritten recognizer.",
                self.checkpoint,
            )
            self._processor = None
            self._model = None
            self._has_hf_model = False
            self._memory_footprint_mb = 10.0
            self._is_loaded = True
            self.touch()
            return

        try:
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel

            log.info("Loading TrOCR model from %s on %s...", self.checkpoint, self.device)
            self._processor = TrOCRProcessor.from_pretrained(self.checkpoint, local_files_only=True)
            self._model = VisionEncoderDecoderModel.from_pretrained(
                self.checkpoint, local_files_only=True
            ).to(self.device)
            self._has_hf_model = True
            self._memory_footprint_mb = 320.0
        except Exception as exc:
            log.info(
                "Local HuggingFace TrOCR checkpoint '%s' not cached (%s). Using offline handwritten recognizer.",
                self.checkpoint,
                exc,
            )
            self._processor = None
            self._model = None
            self._has_hf_model = False
            self._memory_footprint_mb = 10.0

        self._is_loaded = True
        self.touch()

    def unload(self) -> None:
        self._processor = None
        self._model = None
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
        """Recognise handwritten text in an ROI crop."""
        self.ensure_loaded()

        if isinstance(image, np.ndarray):
            pil_img = Image.fromarray(image).convert("RGB")
        elif isinstance(image, Image.Image):
            pil_img = image.convert("RGB")
        else:
            raise TypeError(f"Expected PIL Image or numpy array, got {type(image)}")

        w, h = pil_img.width, pil_img.height
        if w < 10 or h < 10:
            return []

        if self._has_hf_model and self._model is not None and self._processor is not None:
            return self._predict_neural(pil_img, page, region_id, offset_x, offset_y)
        else:
            return self._predict_fallback(pil_img, page, region_id, offset_x, offset_y)

    def _predict_neural(
        self,
        pil_img: Image.Image,
        page: int,
        region_id: str | None,
        offset_x: float,
        offset_y: float,
    ) -> list[OCRResult]:
        import torch

        pixel_values = self._processor(pil_img, return_tensors="pt").pixel_values.to(self.device)
        with torch.no_grad():
            outputs = self._model.generate(pixel_values, return_dict_in_generate=True, output_scores=True)
            generated_ids = outputs.sequences
            generated_text = self._processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()

            # Estimate confidence from token score probabilities
            confidence = 0.85
            if hasattr(outputs, "scores") and outputs.scores:
                token_probs = [
                    torch.softmax(s, dim=-1).max().item() for s in outputs.scores
                ]
                if token_probs:
                    confidence = float(sum(token_probs) / len(token_probs))

        if not generated_text:
            return []

        bbox = BoundingBox(
            x0=float(offset_x),
            y0=float(offset_y),
            x1=float(offset_x + pil_img.width),
            y1=float(offset_y + pil_img.height),
        )
        return [
            OCRResult(
                text=generated_text,
                confidence=round(confidence, 3),
                bbox=bbox,
                page=page,
                line_no=1,
                region_id=region_id,
                model=self.model_name,
                model_version=self.get_model_version(),
                is_handwritten=True,
                is_reliable=(confidence >= 0.45),
            )
        ]

    def _predict_fallback(
        self,
        pil_img: Image.Image,
        page: int,
        region_id: str | None,
        offset_x: float,
        offset_y: float,
    ) -> list[OCRResult]:
        """Offline fallback using RapidOCR with handwritten confidence calibration."""
        from rapidocr_onnxruntime import RapidOCR

        arr = np.array(pil_img)
        reader = RapidOCR()
        results, _ = reader(arr)
        if not results:
            return []

        ocr_results: list[OCRResult] = []
        for line_idx, item in enumerate(results, start=1):
            box, text, score = item[0], item[1], item[2]
            text = (text or "").strip()
            if not text:
                continue
            xs = [p[0] + offset_x for p in box]
            ys = [p[1] + offset_y for p in box]
            # Lower confidence for handwritten heuristic
            conf = float(score) * 0.90 if score is not None else 0.50
            ocr_results.append(
                OCRResult(
                    text=text,
                    confidence=round(conf, 3),
                    bbox=BoundingBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys)),
                    page=page,
                    line_no=line_idx,
                    region_id=region_id,
                    model=self.model_name,
                    model_version=self.get_model_version(),
                    is_handwritten=True,
                    is_reliable=(conf >= 0.45),
                )
            )
        return ocr_results
