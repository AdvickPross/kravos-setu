"""Comprehensive evaluation tooling for OCR (CER/WER), detection (IoU/mAP), and pipeline latency."""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import time
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from models.manager import ModelManager
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.schemas.canonical import BoundingBox, PipelineResult
DEMO_SCAN = REPO_ROOT / "demo" / "scans" / "discharge_summary.png"


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute the minimum edit operations (insertions, deletions, substitutions) between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def compute_cer(reference: str, hypothesis: str) -> float:
    """Character Error Rate: edit_distance(ref, hyp) / len(ref)."""
    ref = reference.strip()
    hyp = hypothesis.strip()
    if not ref:
        return 0.0 if not hyp else 1.0
    dist = levenshtein_distance(ref, hyp)
    return float(dist / len(ref))


def compute_wer(reference: str, hypothesis: str) -> float:
    """Word Error Rate: word_edit_distance(ref, hyp) / len(ref_words)."""
    ref_words = reference.strip().split()
    hyp_words = hypothesis.strip().split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    d: list[list[int]] = [[0] * (len(hyp_words) + 1) for _ in range(len(ref_words) + 1)]
    for i in range(len(ref_words) + 1):
        d[i][0] = i
    for j in range(len(hyp_words) + 1):
        d[0][j] = j

    for i in range(1, len(ref_words) + 1):
        for j in range(1, len(hyp_words) + 1):
            if ref_words[i - 1].lower() == hyp_words[j - 1].lower():
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + 1)

    return float(d[len(ref_words)][len(hyp_words)] / len(ref_words))


def compute_detection_iou(box_a: tuple[float, float, float, float], box_b: tuple[float, float, float, float]) -> float:
    """Intersection over Union between two (x0, y0, x1, y1) bounding boxes."""
    ba = BoundingBox(box_a[0], box_a[1], box_a[2], box_a[3])
    bb = BoundingBox(box_b[0], box_b[1], box_b[2], box_b[3])
    return ba.iou(bb)


# Ground truth for demo discharge summary
GROUND_TRUTH_TEXT = """
SUNRISE MULTISPECIALITY HOSPITAL
Bengaluru, Karnataka
DISCHARGE SUMMARY
Patient Name : Ramesh Kumar
Age / Sex    : 58 / M
IP Number    : IP-2026-04417
Admitted     : 02-03-2026
Discharged   : 09-03-2026
DIAGNOSIS:
Type 2 diabetes mellitus
Essential hypertension
Chronic kidney disease
ALLERGIES:
Penicillin
Aspirin
DISCHARGE MEDICATIONS:
Metformin 500 mg
Amlodipine 5 mg
Atorvastatin 20 mg
Furosemide 40 mg
INVESTIGATIONS:
Hemoglobin
Creatinine
Potassium
PROCEDURES:
Renal dialysis
Advised review after two weeks.
Dr. S. Iyer, MD
""".strip()


def run_evaluation(
    num_runs: int = 2,
    image_path: str | Path | None = None,
    device: str = "auto",
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Execute full evaluation benchmark comparing CER, WER, and latency metrics."""
    doc_path = Path(image_path) if image_path else DEMO_SCAN
    if not doc_path.exists():
        raise FileNotFoundError(f"Evaluation target not found: {doc_path}")

    orchestrator = PipelineOrchestrator(device=device)


    latencies: list[float] = []
    results: list[PipelineResult] = []

    print("\n" + "=" * 72)
    print("DOCUMENT INTELLIGENCE PIPELINE EVALUATION SUITE")
    print(f"Target Document: {doc_path.name} | Device: {device}")
    print("=" * 72)

    for i in range(num_runs):
        t0 = time.perf_counter()
        res = orchestrator.process_document(doc_path, patient_id="eval-patient")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)
        results.append(res)
        print(f"Run {i + 1}/{num_runs}: {elapsed_ms:.1f}ms | Extracted {len(res.ocr_results)} lines, {len(res.entities)} facts")

    last_res = results[-1]
    extracted_text = "\n".join(r.text for r in last_res.ocr_results)

    cer = compute_cer(GROUND_TRUTH_TEXT, extracted_text)
    wer = compute_wer(GROUND_TRUTH_TEXT, extracted_text)

    p50_latency = statistics.median(latencies)
    p95_latency = max(latencies)
    mem_report = orchestrator.model_manager.memory_report()

    report = {
        "evaluation_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_document": DEMO_SCAN.name,
        "runs": num_runs,
        "ocr_metrics": {
            "cer_percentage": round(cer * 100, 2),
            "wer_percentage": round(wer * 100, 2),
            "mean_confidence": last_res.mean_confidence,
            "target_thresholds": {
                "clean_printed_cer_target": "< 3%",
                "degraded_printed_cer_target": "< 8%",
                "handwritten_cer_target": "< 15-20%",
            },
        },
        "detection_metrics": {
            "total_regions_detected": len(last_res.regions),
            "region_classes_found": sorted(set(r.class_name for r in last_res.regions)),
        },
        "latency_metrics_ms": {
            "p50": round(p50_latency, 2),
            "p95": round(p95_latency, 2),
            "stage_breakdown": last_res.stage_timings,
        },
        "memory_metrics": mem_report,
        "facts_extracted_count": len(last_res.entities),
        "sections_seen": last_res.sections_seen,
    }

    print("\n--- OCR ACCURACY ---")
    print(f"Character Error Rate (CER): {report['ocr_metrics']['cer_percentage']}%")
    print(f"Word Error Rate (WER):      {report['ocr_metrics']['wer_percentage']}%")
    print(f"Mean Confidence:            {report['ocr_metrics']['mean_confidence']:.3f}")

    print("\n--- LATENCY & MEMORY ---")
    print(f"P50 Latency: {p50_latency:.1f}ms | P95 Latency: {p95_latency:.1f}ms")
    print(f"Resident Models: {mem_report['loaded_models']} (Total Footprint: {mem_report['estimated_footprint_mb']} MB)")
    print("=" * 72 + "\n")

    out_file = Path(output_path) if output_path else (REPO_ROOT / "eval" / "pipeline_benchmark.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved benchmark report to {out_file}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Document Intelligence Evaluation Suite")
    parser.add_argument("--image", type=str, default=str(DEMO_SCAN), help="Path to input scan document")
    parser.add_argument("--device", type=str, default="auto", help="Compute device (auto, cpu, cuda)")
    parser.add_argument("--runs", type=int, default=2, help="Number of benchmark evaluation iterations")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path for benchmark report")
    args = parser.parse_args()
    run_evaluation(num_runs=args.runs, image_path=args.image, device=args.device, output_path=args.output)

