"""Scanned page in, lines of text out.

The only part of the OCR path that touches pixels. Everything above it works
on `OcrLine`, so the engine can be swapped -- for Tesseract, for a cloud
service, for a vision model -- without any of the clinical extraction
changing.

RapidOCR is the default because of what it does *not* require: no system
binary to install (unlike Tesseract on Windows), no PyTorch (unlike EasyOCR
and PaddleOCR, which pull ~2 GB), and no API key or network call. A scanned
discharge summary is a patient's health record; sending it to a third-party
OCR service is a disclosure decision, not an implementation detail, and this
package should not make that decision silently on anyone's behalf.

**Confidence is carried, not discarded.** Every other layer treats a fact as
true. A fact read off a scan at 0.55 confidence is not the same kind of
object as one parsed from a FHIR bundle, and the difference has to survive
all the way to the screen — a misread drug name is exactly the sort of error
this project exists to surface rather than launder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Below this, the text is more likely noise than a misreading. Lines under it
# are kept -- they still appear in the extracted text a reviewer reads -- but
# are never promoted into a clinical fact. See `ocr/extract.py`.
MIN_LINE_CONFIDENCE = 0.50

# Rendering DPI for PDF pages. 200 is the point where a 10pt clinical font
# stops losing its thin strokes; 300 doubles the pixels for a marginal gain.
PDF_RENDER_DPI = 200

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


@dataclass(frozen=True)
class OcrLine:
    """One line of recognised text, with everything needed to cite it."""

    text: str
    confidence: float
    page: int
    line_no: int
    # (x0, y0, x1, y1) in page pixels. Kept so a reviewer UI can box the
    # source region on the image; nothing in the clinical path reads it.
    box: tuple[float, float, float, float] | None = None

    @property
    def is_reliable(self) -> bool:
        return self.confidence >= MIN_LINE_CONFIDENCE


@dataclass(frozen=True)
class OcrDocument:
    source: Path
    lines: list[OcrLine]
    engine: str

    @property
    def text(self) -> str:
        """The document as a reviewer would read it, one line per line."""
        return "\n".join(line.text for line in self.lines)

    @property
    def mean_confidence(self) -> float:
        if not self.lines:
            return 0.0
        return sum(line.confidence for line in self.lines) / len(self.lines)

    def reliable_lines(self) -> list[OcrLine]:
        return [line for line in self.lines if line.is_reliable]


class OcrUnavailable(RuntimeError):
    """The engine is not installed. Distinct from a page that read badly."""


@lru_cache(maxsize=1)
def _reader() -> Any:
    """One engine instance, built once.

    RapidOCR loads three ONNX models (detection, classification,
    recognition). That takes a couple of seconds and a few hundred MB, so
    rebuilding it per page would dominate the runtime of a multi-page
    document.
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise OcrUnavailable(
            "rapidocr-onnxruntime is not installed. `pip install "
            "rapidocr-onnxruntime` -- it needs no system binary and no API key."
        ) from exc
    return RapidOCR()


def _page_images(path: Path) -> list[Any]:
    """Every page of the input as a numpy array.

    A PDF is rasterised even when it has a text layer. That looks wasteful and
    is deliberate: a PDF that *contains* a scan usually also carries a text
    layer from whatever produced it, and silently preferring that layer would
    mean the pipeline sometimes OCRs and sometimes does not, with no visible
    difference in the output. One path is easier to reason about than two, and
    `scripts/ocr_ingest.py --text-layer` exists for when the caller knows the
    PDF is born-digital.
    """
    import numpy as np

    if path.suffix.lower() == ".pdf":
        import fitz

        zoom = PDF_RENDER_DPI / 72
        pages = []
        with fitz.open(path) as doc:
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n
                )
                # RapidOCR wants 3 channels; a pixmap may carry an alpha.
                pages.append(image[:, :, :3] if pix.n == 4 else image)
        return pages

    if path.suffix.lower() in IMAGE_SUFFIXES:
        from PIL import Image

        with Image.open(path) as handle:
            return [np.array(handle.convert("RGB"))]

    raise ValueError(
        f"unsupported file type {path.suffix!r}; expected a PDF or one of "
        f"{', '.join(sorted(IMAGE_SUFFIXES))}"
    )


def read_document(path: str | Path) -> OcrDocument:
    """OCR every page of one scanned file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    reader = _reader()
    lines: list[OcrLine] = []

    for page_index, image in enumerate(_page_images(path), start=1):
        result, _elapsed = reader(image)
        # RapidOCR returns None rather than [] for a page with no text at all.
        for line_index, item in enumerate(result or [], start=1):
            box, text, score = item[0], item[1], item[2]
            text = (text or "").strip()
            if not text:
                continue
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            lines.append(
                OcrLine(
                    text=text,
                    confidence=float(score),
                    page=page_index,
                    line_no=line_index,
                    box=(min(xs), min(ys), max(xs), max(ys)),
                )
            )

    log.info(
        "OCR %s: %d lines across %d page(s), mean confidence %.2f",
        path.name,
        len(lines),
        max((line.page for line in lines), default=0),
        sum(line.confidence for line in lines) / len(lines) if lines else 0.0,
    )
    return OcrDocument(source=path, lines=lines, engine="rapidocr-onnxruntime")


def read_pdf_text_layer(path: str | Path) -> OcrDocument:
    """Pull an existing text layer instead of recognising pixels.

    For born-digital PDFs -- a discharge summary exported from an HMIS rather
    than scanned off paper. Confidence is reported as 1.0 because no
    recognition happened; nothing was guessed.
    """
    import fitz

    path = Path(path)
    lines: list[OcrLine] = []
    with fitz.open(path) as doc:
        for page_index, page in enumerate(doc, start=1):
            for line_index, raw in enumerate(page.get_text().splitlines(), start=1):
                text = raw.strip()
                if text:
                    lines.append(
                        OcrLine(
                            text=text,
                            confidence=1.0,
                            page=page_index,
                            line_no=line_index,
                        )
                    )
    return OcrDocument(source=path, lines=lines, engine="pdf-text-layer")
