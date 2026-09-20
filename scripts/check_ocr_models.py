"""Command line diagnostic tool to check the installation and execution status of all 5 OCR models."""

from __future__ import annotations

import sys
from models.manager import ModelManager


def check_models() -> int:
    manager = ModelManager(strict_mode=False)
    models_to_check = [
        ("PP-OCR", "ppocr"),
        ("TrOCR", "trocr"),
        ("RT-DETR", "rt-detr"),
        ("Restormer", "restormer"),
        ("LayoutLMv3", "layoutlmv3"),
    ]

    print("========================================================================")
    print("  KRAVOS SETU — OCR MODEL AVAILABILITY & TRUTHFUL STATUS REPORT")
    print("========================================================================")
    print(f"{'MODEL':<14} {'STATUS':<22} {'BACKEND / DETAILS'}")
    print("-" * 72)

    has_failures = False
    for display_name, key in models_to_check:
        try:
            adapter = manager.get_model(key)
            meta = adapter.get_metadata()
            status = meta.get("status", "UNKNOWN")
            backend = meta.get("backend", "unknown")
            device = meta.get("device", "cpu")
            checkpoint = meta.get("checkpoint")

            if meta.get("fallback"):
                details = f"Fallback: {meta.get('fallback_type')} ({device})"
            else:
                details = f"{backend} ({device})"
                if checkpoint:
                    details += f" [{checkpoint}]"

            print(f"{display_name:<14} {status:<22} {details}")
        except Exception as exc:
            has_failures = True
            print(f"{display_name:<14} {'FAILED':<22} Error: {exc}")

    print("========================================================================")
    return 1 if has_failures else 0


if __name__ == "__main__":
    sys.exit(check_models())
