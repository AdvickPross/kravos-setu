#!/usr/bin/env python3
"""Helper script to pre-download the TrOCR small handwritten model to local HuggingFace cache.

Usage:
    py -3 scripts/download_trocr.py
"""

from __future__ import annotations

import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

MODEL_ID = "microsoft/trocr-small-handwritten"


def download():
    log.info("Downloading HuggingFace TrOCR checkpoint: %s...", MODEL_ID)
    try:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel

        log.info("Downloading processor...")
        processor = TrOCRProcessor.from_pretrained(MODEL_ID)
        log.info("Downloading model weights...")
        model = VisionEncoderDecoderModel.from_pretrained(MODEL_ID)
        log.info("TrOCR successfully cached locally!")
        return 0
    except Exception as exc:
        log.error("Failed to download TrOCR model: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(download())
