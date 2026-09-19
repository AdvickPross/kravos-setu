"""Comprehensive evaluation tooling for OCR (CER/WER), detection (IoU/mAP), and pipeline latency."""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config.loader import PolicyConfig
from models.manager import ModelManager, get_process_rss_mb
from ocr.engine import read_document
from ocr.extract import extract_facts
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


def benchmark_legacy(doc_path: Path, warm_runs: int = 3) -> dict[str, Any]:
    """Benchmark the baseline Legacy RapidOCR pipeline."""
    # Cold start run
    t0 = time.perf_counter()
    cold_doc = read_document(doc_path)
    cold_latency_ms = (time.perf_counter() - t0) * 1000

    # Warm runs
    warm_latencies: list[float] = []
    last_doc = cold_doc
    for _ in range(warm_runs):
        t_start = time.perf_counter()
        last_doc = read_document(doc_path)
        warm_latencies.append((time.perf_counter() - t_start) * 1000)

    p50 = statistics.median(warm_latencies) if warm_latencies else cold_latency_ms
    p95 = max(warm_latencies) if warm_latencies else cold_latency_ms

    extracted_text = "\n".join(line.text for line in last_doc.lines)
    cer = compute_cer(GROUND_TRUTH_TEXT, extracted_text)
    wer = compute_wer(GROUND_TRUTH_TEXT, extracted_text)

    # Facts extraction
    report = extract_facts(last_doc, "eval-patient")

    return {
        "engine": "Legacy RapidOCR (Single Model)",
        "cold_start_latency_ms": round(cold_latency_ms, 2),
        "warm_latency_ms": {
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "all_runs": [round(x, 2) for x in warm_latencies],
        },
        "ocr_metrics": {
            "cer_percentage": round(cer * 100, 2),
            "wer_percentage": round(wer * 100, 2),
            "mean_confidence": round(last_doc.mean_confidence, 3),
            "lines_extracted": len(last_doc.lines),
        },
        "facts_extracted_count": len(report.facts),
        "sections_seen": report.sections_seen,
    }


def benchmark_pipeline(
    doc_path: Path,
    profile_name: str = "laptop",
    device: str = "auto",
    warm_runs: int = 3,
    dpi: int | None = None,
) -> dict[str, Any]:
    """Benchmark the 5-Model Document Intelligence Pipeline."""
    policy = PolicyConfig.load(profile=profile_name)
    manager = ModelManager(policy=policy.memory, device=device)
    orchestrator = PipelineOrchestrator(model_manager=manager, policy_config=policy)

    # Cold start run (instantiates and loads models)
    t0 = time.perf_counter()
    cold_result = orchestrator.process_document(doc_path, patient_id="eval-patient", dpi=dpi)
    cold_latency_ms = (time.perf_counter() - t0) * 1000

    # Warm runs (models remain resident in memory)
    warm_latencies: list[float] = []
    last_res = cold_result
    for _ in range(warm_runs):
        t_start = time.perf_counter()
        last_res = orchestrator.process_document(doc_path, patient_id="eval-patient", dpi=dpi)
        warm_latencies.append((time.perf_counter() - t_start) * 1000)

    p50 = statistics.median(warm_latencies) if warm_latencies else cold_latency_ms
    p95 = max(warm_latencies) if warm_latencies else cold_latency_ms

    extracted_text = "\n".join(r.text for r in last_res.ocr_results)
    cer = compute_cer(GROUND_TRUTH_TEXT, extracted_text)
    wer = compute_wer(GROUND_TRUTH_TEXT, extracted_text)

    mem_status = manager.get_memory_status()
    models_meta = manager.get_all_metadata()

    return {
        "engine": f"5-Model Pipeline (v{last_res.pipeline_version})",
        "profile": policy.default_profile,
        "resolved_device": manager.device,
        "resolved_precision": manager.precision,
        "cold_start_latency_ms": round(cold_latency_ms, 2),
        "warm_latency_ms": {
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "all_runs": [round(x, 2) for x in warm_latencies],
        },
        "ocr_metrics": {
            "cer_percentage": round(cer * 100, 2),
            "wer_percentage": round(wer * 100, 2),
            "mean_confidence": round(last_res.mean_confidence, 3),
            "lines_extracted": len(last_res.ocr_results),
            "handwritten_lines_count": sum(1 for r in last_res.ocr_results if r.is_handwritten),
        },
        "detection_metrics": {
            "total_regions_detected": len(last_res.regions),
            "region_classes_found": sorted(set(r.class_name for r in last_res.regions)),
        },
        "facts_extracted_count": len(last_res.entities),
        "sections_seen": last_res.sections_seen,
        "stage_timings_ms": last_res.stage_timings,
        "memory_status": mem_status,
        "models_metadata": models_meta,
    }


def run_evaluation(
    num_runs: int = 3,
    image_path: str | Path | None = None,
    device: str = "auto",
    profile: str = "laptop",
    output_path: str | Path | None = None,
    run_legacy: bool = True,
    run_pipeline: bool = True,
) -> dict[str, Any]:
    """Execute complete benchmark suite and print comparative results."""
    doc_path = Path(image_path) if image_path else DEMO_SCAN
    if not doc_path.exists():
        raise FileNotFoundError(f"Evaluation target scan not found: {doc_path}")

    print("=" * 76)
    print("      KRAVOS SETU - OCR & DOCUMENT INTELLIGENCE BENCHMARK SUITE")
    print("=" * 76)
    print(f"Target Scan:      {doc_path.name} ({doc_path.stat().st_size / 1024:.1f} KB)")
    print(f"Hardware Profile: {profile}")
    print(f"Device Request:   {device}")
    print(f"Warm Iterations:  {num_runs}")
    print("-" * 76)

    overall_report: dict[str, Any] = {
        "benchmark_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_document": doc_path.name,
        "hardware_profile": profile,
        "requested_device": device,
        "warm_runs_count": num_runs,
    }

    if run_legacy:
        print("\n--> Benchmarking Legacy RapidOCR Pipeline...")
        legacy_res = benchmark_legacy(doc_path, warm_runs=num_runs)
        overall_report["legacy_rapidocr"] = legacy_res
        print(f"    Cold Start: {legacy_res['cold_start_latency_ms']} ms")
        print(f"    Warm P50:   {legacy_res['warm_latency_ms']['p50']} ms (P95: {legacy_res['warm_latency_ms']['p95']} ms)")
        print(f"    CER:        {legacy_res['ocr_metrics']['cer_percentage']}% | WER: {legacy_res['ocr_metrics']['wer_percentage']}%")
        print(f"    Facts Extracted: {legacy_res['facts_extracted_count']}")

    if run_pipeline:
        print("\n--> Benchmarking 5-Model Document Intelligence Pipeline...")
        pipeline_res = benchmark_pipeline(doc_path, profile_name=profile, device=device, warm_runs=num_runs)
        overall_report["five_model_pipeline"] = pipeline_res
        print(f"    Execution:  {pipeline_res['resolved_device'].upper()}:{pipeline_res['resolved_precision'].upper()}")
        print(f"    Cold Start: {pipeline_res['cold_start_latency_ms']} ms")
        print(f"    Warm P50:   {pipeline_res['warm_latency_ms']['p50']} ms (P95: {pipeline_res['warm_latency_ms']['p95']} ms)")
        print(f"    CER:        {pipeline_res['ocr_metrics']['cer_percentage']}% | WER: {pipeline_res['ocr_metrics']['wer_percentage']}%")
        print(f"    Facts Extracted: {pipeline_res['facts_extracted_count']} (Sections: {', '.join(pipeline_res['sections_seen'])})")
        rss_val = pipeline_res.get("memory_status", {}).get("process_rss_mb")
        rss_str = f"{rss_val:.1f} MB" if rss_val is not None else "N/A"
        print(f"    Process RSS: {rss_str}")

    # Comparative Summary Table
    if run_legacy and run_pipeline:
        leg = overall_report["legacy_rapidocr"]
        pip = overall_report["five_model_pipeline"]

        print("\n" + "=" * 76)
        print("                  COMPARATIVE BENCHMARK SUMMARY")
        print("=" * 76)
        header = f"{'Metric':<28} | {'Legacy RapidOCR':<20} | {'5-Model Pipeline':<20}"
        print(header)
        print("-" * len(header))
        print(f"{'Cold Start Latency':<28} | {leg['cold_start_latency_ms']:>17.1f} ms | {pip['cold_start_latency_ms']:>17.1f} ms")
        print(f"{'Warm Latency (P50)':<28} | {leg['warm_latency_ms']['p50']:>17.1f} ms | {pip['warm_latency_ms']['p50']:>17.1f} ms")
        print(f"{'Warm Latency (P95)':<28} | {leg['warm_latency_ms']['p95']:>17.1f} ms | {pip['warm_latency_ms']['p95']:>17.1f} ms")
        print(f"{'Character Error Rate (CER)':<28} | {leg['ocr_metrics']['cer_percentage']:>19.2f}% | {pip['ocr_metrics']['cer_percentage']:>19.2f}%")
        print(f"{'Word Error Rate (WER)':<28} | {leg['ocr_metrics']['wer_percentage']:>19.2f}% | {pip['ocr_metrics']['wer_percentage']:>19.2f}%")
        print(f"{'Mean OCR Confidence':<28} | {leg['ocr_metrics']['mean_confidence']:>20.3f} | {pip['ocr_metrics']['mean_confidence']:>20.3f}")
        print(f"{'Clinical Facts Extracted':<28} | {leg['facts_extracted_count']:>20} | {pip['facts_extracted_count']:>20}")
        det_count = pip["detection_metrics"]["total_regions_detected"]
        det_str = f"{det_count} regions"
        print(f"{'Document Layout Detection':<28} | {'None (Flat OCR)':>20} | {det_str:>20}")
        print(f"{'Provenance Tracking':<28} | {'Line-Level':>20} | {'Region+Model+BBox':>20}")
        print("=" * 76)

    out_file = Path(output_path) if output_path else (REPO_ROOT / "eval" / "pipeline_benchmark.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(overall_report, indent=2), encoding="utf-8")
    print(f"\n[OK] Structured benchmark report written to: {out_file.resolve()}\n")
    return overall_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Kravos Setu Document Intelligence Benchmark Suite")
    parser.add_argument("--image", type=str, default=str(DEMO_SCAN), help="Path to input scan document")
    parser.add_argument("--device", "-d", type=str, default="auto", help="Compute device (auto, cpu, cuda)")
    parser.add_argument("--profile", "-p", type=str, default="laptop", help="Hardware profile (laptop, jetson)")
    parser.add_argument("--runs", "-n", type=int, default=3, help="Number of warm benchmark iterations")
    parser.add_argument("--output", "-o", type=str, default=None, help="Output JSON benchmark file path")
    parser.add_argument("--legacy-only", action="store_true", help="Only benchmark legacy RapidOCR")
    parser.add_argument("--pipeline-only", action="store_true", help="Only benchmark 5-model pipeline")
    args = parser.parse_args()

    run_leg = not args.pipeline_only
    run_pip = not args.legacy_only

    run_evaluation(
        num_runs=args.runs,
        image_path=args.image,
        device=args.device,
        profile=args.profile,
        output_path=args.output,
        run_legacy=run_leg,
        run_pipeline=run_pip,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
