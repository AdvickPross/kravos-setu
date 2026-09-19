"""End-to-end diagnostic tool for the Kravos Setu OCR pipeline.

Runs a medical document image or PDF through the complete 5-model pipeline
and prints a comprehensive per-stage diagnostic report.

Usage:
    py -3 -m scripts.test_ocr_pipeline <document_path> [options]

    py -3 -m scripts.test_ocr_pipeline prescription.png
    py -3 -m scripts.test_ocr_pipeline scan.pdf --patient P001 --json
    py -3 -m scripts.test_ocr_pipeline document.png --device cpu --verbose

Options:
    --patient    Patient ID (default: test-patient)
    --device     Execution device: auto, cpu, cuda (default: auto)
    --profile    Hardware profile: laptop, jetson (default: laptop)
    --dpi        PDF render DPI (default: 250)
    --json       Output raw JSON instead of formatted report
    --verbose    Show all OCR lines, not just summary
    --doc-type   Override document type (prescription, lab_report, etc.)
    --save       Save full JSON output to <document>.pipeline.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _rule(char: str = "-", width: int = 72) -> str:
    return char * width


def _truncate(text: str, maxlen: int = 80) -> str:
    return text if len(text) <= maxlen else text[: maxlen - 3] + "..."


def _format_pipeline_report(result, verbose: bool = False) -> None:
    """Print a formatted diagnostic report from a PipelineResult."""
    print(_rule("="))
    print("  KRAVOS SETU — OCR PIPELINE DIAGNOSTIC REPORT")
    print(_rule("="))

    print(f"\n[DOCUMENT]")
    print(f"  File:             {result.source_file}")
    print(f"  Document Type:    {getattr(result, 'document_type', 'unknown')}")
    print(f"  Pipeline Version: {result.pipeline_version}")
    print(f"  Total OCR Lines:  {len(result.ocr_results)}")
    print(f"  Mean Confidence:  {result.mean_confidence:.3f}")
    print(f"  Entities Found:   {len(result.entities)}")

    print(f"\n[STAGE TIMINGS]")
    for key, val in result.stage_timings.items():
        print(f"  {key:<32} {val:>8.1f} ms")

    print(f"\n[MODEL VERSIONS]")
    for key, val in result.stage_versions.items():
        print(f"  {key:<26} {val}")

    print(f"\n[QUALITY REPORTS]")
    for qr in result.quality_reports:
        passed_checks = [k for k, v in qr.checks.items() if v.passed]
        failed_checks = [k for k, v in qr.checks.items() if not v.passed]
        print(f"  Page {qr.page}:")
        print(f"    Overall Score:       {qr.overall_score:.3f}")
        print(f"    Enhancement Needed:  {qr.enhancement_required}")
        print(f"    Action Plan:         {', '.join(qr.action_plan) or 'none'}")
        print(f"    Checks Passed:       {', '.join(passed_checks) or 'none'}")
        print(f"    Checks Failed:       {', '.join(failed_checks) or 'none'}")
        print(f"    Latency:             {qr.latency_ms:.1f} ms")

    print(f"\n[DETECTED REGIONS ({len(result.regions)})]")
    for i, region in enumerate(result.regions[:20]):  # Limit to 20
        print(
            f"  {i+1:2d}. [{region.class_name:11}] order={region.reading_order:2d} "
            f"conf={region.confidence:.2f} "
            f"bbox=({region.bbox.x0:.0f},{region.bbox.y0:.0f},"
            f"{region.bbox.x1:.0f},{region.bbox.y1:.0f})"
        )
    if len(result.regions) > 20:
        print(f"  ... and {len(result.regions) - 20} more regions")

    # OCR results
    print(f"\n[OCR RESULTS ({len(result.ocr_results)} lines)]")
    reliable_count = sum(1 for r in result.ocr_results if r.is_reliable)
    hw_count = sum(1 for r in result.ocr_results if r.is_handwritten)
    print(f"  Reliable (>=0.50 conf): {reliable_count}/{len(result.ocr_results)}")
    print(f"  Handwritten:           {hw_count}/{len(result.ocr_results)}")
    if result.ocr_results:
        confs = [r.confidence for r in result.ocr_results]
        print(f"  Min confidence:        {min(confs):.3f}")
        print(f"  Max confidence:        {max(confs):.3f}")

    show_count = len(result.ocr_results) if verbose else min(30, len(result.ocr_results))
    for r in result.ocr_results[:show_count]:
        flag = "!!" if not r.is_reliable else "  "
        hand = "[H]" if r.is_handwritten else "   "
        model_short = r.model[:8]
        print(
            f"  {flag} {hand} p{r.page}L{r.line_no:03d} "
            f"conf={r.confidence:.2f} [{model_short}] "
            f"{_truncate(r.text, 55)}"
        )
    if not verbose and len(result.ocr_results) > show_count:
        print(f"  ... ({len(result.ocr_results) - show_count} more lines, use --verbose to show all)")

    # Structured Data
    structured_data = getattr(result, "structured_data", {})
    if structured_data:
        print(f"\n[STRUCTURED EXTRACTION]")
        print(f"  Document Type:    {structured_data.get('document_type', 'N/A')}")
        patient = structured_data.get("patient", {})
        if patient.get("name", {}).get("presence") == "PRESENT":
            print(f"  Patient Name:     {patient['name']['value']} (conf={patient['name']['confidence']:.2f})")
        if patient.get("age", {}).get("presence") == "PRESENT":
            sex_val = patient.get("sex", {}).get("value", "")
            print(f"  Age/Sex:          {patient['age']['value']}/{sex_val}")
        if patient.get("hospital_number", {}).get("presence") == "PRESENT":
            print(f"  Hospital No:      {patient['hospital_number']['value']}")
        if structured_data.get("doctor", {}).get("presence") == "PRESENT":
            print(f"  Doctor:           {structured_data['doctor']['value']}")
        if structured_data.get("date", {}).get("presence") == "PRESENT":
            print(f"  Date:             {structured_data['date']['value']}")
        if structured_data.get("department", {}).get("presence") == "PRESENT":
            print(f"  Department:       {structured_data['department']['value']}")

        meds = structured_data.get("medications", [])
        if meds:
            print(f"\n  MEDICATIONS ({len(meds)}):")
            for i, med in enumerate(meds):
                name = med.get("name", {}).get("value", "?")
                dosage = med.get("dosage", {}).get("value") or "?"
                freq_note = med.get("frequency_note", {}).get("value") or ""
                duration = med.get("duration", {}).get("value") or ""
                print(f"    {i+1}. {name} {dosage}  {freq_note}  {duration}")

        lab_tests = structured_data.get("lab_tests", [])
        if lab_tests:
            print(f"\n  LAB TESTS ({len(lab_tests)}):")
            for t in lab_tests[:10]:
                test_name = t.get("test_name", {}).get("value", "?")
                result_val = t.get("result", {}).get("value", "?")
                unit = t.get("unit", {}).get("value") or ""
                ref = t.get("reference_range", {}).get("value") or ""
                print(f"    {test_name:<30} {result_val:>8} {unit:<10} ref: {ref}")

        warnings = structured_data.get("extraction_warnings", [])
        if warnings:
            print(f"\n  WARNINGS:")
            for w in warnings:
                print(f"    ! {w}")

    # Clinical entities
    print(f"\n[CLINICAL ENTITIES ({len(result.entities)})]")
    print(f"  Sections recognised: {', '.join(result.sections_seen) or 'none'}")
    if result.entities:
        for e in result.entities:
            dose = f"{e.value} {e.unit}" if e.value else ""
            alt_str = f" +{len(e.also_in)} codes" if e.also_in else ""
            print(
                f"  {e.category:<12} {(e.display or '')[:40]:<42} "
                f"{dose:<10} conf={e.confidence:.3f}{alt_str}"
            )
    else:
        print("  No coded facts extracted (check sections_seen above)")

    # Unmatched / ambiguous
    if result.unmatched:
        print(f"\n[UNMATCHED LINES ({len(result.unmatched)})]")
        for line in result.unmatched[:10]:
            print(f"  ~ {_truncate(line, 70)}")

    if result.ambiguous:
        print(f"\n[AMBIGUOUS LINES ({len(result.ambiguous)})]")
        for line in result.ambiguous[:10]:
            print(f"  ? {_truncate(line, 70)}")

    if result.low_confidence:
        print(f"\n[LOW-CONFIDENCE LINES ({len(result.low_confidence)})]")
        for line in result.low_confidence[:10]:
            print(f"  ! {_truncate(line, 70)}")

    print(f"\n{_rule('=')}")
    print(f"  PIPELINE COMPLETE: {result.stage_timings.get('total_ms', 0):.0f} ms total")
    print(_rule("="))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.test_ocr_pipeline",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("document", type=Path, help="Image or PDF path to process")
    parser.add_argument("--patient", default="test-patient", help="Patient ID")
    parser.add_argument(
        "--device", choices=["auto", "cpu", "cuda"], default=None,
        help="Execution device override"
    )
    parser.add_argument("--profile", default=None, help="Hardware profile (laptop, jetson)")
    parser.add_argument("--dpi", type=int, default=None, help="PDF render DPI")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--verbose", action="store_true", help="Show all OCR lines")
    parser.add_argument("--doc-type", default="auto", help="Override document type")
    parser.add_argument("--save", action="store_true", help="Save JSON to <doc>.pipeline.json")
    args = parser.parse_args()

    if not args.document.exists():
        print(f"[ERROR] File not found: {args.document}", file=sys.stderr)
        return 2

    # Import pipeline components
    try:
        from config.loader import PolicyConfig
        from models.manager import ModelManager
        from pipeline.orchestrator import PipelineOrchestrator
    except ImportError as exc:
        print(f"[ERROR] Import failed: {exc}", file=sys.stderr)
        print("Make sure you are running from the kravos-setu repository root.", file=sys.stderr)
        return 1

    print(f"Loading pipeline for: {args.document.name}", file=sys.stderr)
    t_start = time.perf_counter()

    try:
        policy = PolicyConfig.load(profile=args.profile)
        manager = ModelManager(policy=policy.memory, device=args.device)
        orchestrator = PipelineOrchestrator(
            model_manager=manager,
            policy_config=policy,
        )

        result = orchestrator.process_document(
            args.document,
            patient_id=args.patient,
            document_type=args.doc_type,
            dpi=args.dpi,
        )
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        import traceback
        print(f"[ERROR] Pipeline failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    elapsed = (time.perf_counter() - t_start) * 1000

    if getattr(args, "json"):
        # JSON output
        output = {
            "source_file": result.source_file,
            "pipeline_version": result.pipeline_version,
            "document_type": getattr(result, "document_type", "unknown"),
            "mean_confidence": result.mean_confidence,
            "total_ocr_lines": len(result.ocr_results),
            "total_entities": len(result.entities),
            "stage_timings": result.stage_timings,
            "stage_versions": result.stage_versions,
            "structured_data": getattr(result, "structured_data", {}),
            "ocr_results": [
                {
                    "text": r.text,
                    "confidence": round(r.confidence, 3),
                    "page": r.page,
                    "line_no": r.line_no,
                    "is_handwritten": r.is_handwritten,
                    "is_reliable": r.is_reliable,
                    "model": r.model,
                    "bbox": r.bbox.to_tuple(),
                    "region_id": r.region_id,
                }
                for r in result.ocr_results
            ],
            "entities": [
                {
                    **e.as_fact_row(),
                    "_calibrated_confidence": e.confidence,
                    "_provenance": e.provenance.to_dict(),
                }
                for e in result.entities
            ],
            "sections_seen": result.sections_seen,
            "unmatched": result.unmatched,
            "ambiguous": result.ambiguous,
            "low_confidence": result.low_confidence,
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        _format_pipeline_report(result, verbose=args.verbose)

    if args.save:
        save_path = args.document.with_suffix(".pipeline.json")
        save_path.write_text(json.dumps({
            "source_file": result.source_file,
            "pipeline_version": result.pipeline_version,
            "document_type": getattr(result, "document_type", "unknown"),
            "structured_data": getattr(result, "structured_data", {}),
            "mean_confidence": result.mean_confidence,
            "ocr_results": [
                {
                    "text": r.text,
                    "confidence": round(r.confidence, 3),
                    "page": r.page,
                    "line_no": r.line_no,
                    "is_handwritten": r.is_handwritten,
                    "model": r.model,
                }
                for r in result.ocr_results
            ],
            "entities": [e.as_fact_row() for e in result.entities],
        }, indent=2, default=str), encoding="utf-8")
        print(f"\nSaved to: {save_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
