"""Scanned clinical documents -> coded facts.

    engine.py   pixels -> lines of text with confidence (RapidOCR)
    extract.py  lines -> facts, using the same lexicon the query side uses

The split is the point: `extract.py` never sees an image, so the recognition
engine can be swapped for Tesseract, a cloud service or a vision model
without any clinical logic changing.

    pwsh> python scripts/make_demo_scan.py
    pwsh> python scripts/ocr_ingest.py demo/scans/discharge_summary.png --patient <id>
"""

from ocr.engine import OcrDocument, OcrLine, OcrUnavailable, read_document
from ocr.extract import ExtractionReport, OcrFact, extract_facts

__all__ = [
    "ExtractionReport",
    "OcrDocument",
    "OcrFact",
    "OcrLine",
    "OcrUnavailable",
    "PipelineOrchestrator",
    "extract_facts",
    "read_document",
]


def __getattr__(name: str):
    if name == "PipelineOrchestrator":
        from pipeline.orchestrator import PipelineOrchestrator
        return PipelineOrchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


