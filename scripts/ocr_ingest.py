"""Scanned document -> text -> JSON facts -> Postgres -> queryable.

The whole point is that each stage is visible. Run it and you see the file
that went in, the text that came off it, the JSON that was derived, and the
rows that landed in the corpus -- after which the existing `/api/query` path
answers questions about a scan exactly as it answers questions about a FHIR
bundle, because by then the facts are indistinguishable in shape.

    pwsh> python scripts/make_demo_scan.py
    pwsh> python scripts/ocr_ingest.py demo/scans/discharge_summary.png --patient <id>
    pwsh> python scripts/ocr_ingest.py <file> --patient <id> --commit

**Nothing is written without `--commit`.** The default is a dry run that
prints what it *would* insert, because these facts are guesses made by a
recognition model about a clinical document, and the difference between
reviewing them and inserting them should be a deliberate keystroke.

The JSON is written either way. It is the reviewable artifact -- one object
per fact, carrying the page, line, OCR confidence and the raw line the fact
was read from, so every row can be traced back to a region of the original
image.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ocr.engine import OcrUnavailable, read_document, read_pdf_text_layer  # noqa: E402
from ocr.extract import ExtractionReport, extract_facts  # noqa: E402

DEFAULT_OUT_DIR = REPO_ROOT / "demo" / "scans"


def _rule(title: str) -> None:
    # ASCII deliberately: the Windows console is cp1252 and raises
    # UnicodeEncodeError on box-drawing characters, which would crash the
    # script before it printed anything useful.
    print(f"\n{'-' * 72}\n{title}\n{'-' * 72}")


def _write_json(report: ExtractionReport, source: Path, out_dir: Path) -> Path:
    """The reviewable artifact: facts plus the evidence for each one."""
    payload = {
        "source_file": source.name,
        "extracted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sections_seen": report.sections_seen,
        "counts": {
            "facts": len(report.facts),
            "unmatched_lines": len(report.unmatched),
            "ambiguous_lines": len(report.ambiguous),
            "low_confidence_lines": len(report.low_confidence),
        },
        "facts": [
            {
                **fact.as_fact_row(),
                "_ocr": {
                    "confidence": round(fact.confidence, 3),
                    "page": fact.page,
                    "line_no": fact.line_no,
                    "line_text": fact.line_text,
                },
            }
            for fact in report.facts
        ],
        # Kept in the artifact, not just the console: a reviewer checking
        # whether the extraction missed something needs the misses.
        "not_extracted": {
            "unmatched": [line.text for line in report.unmatched],
            "ambiguous": [line.text for line in report.ambiguous],
            "low_confidence": [
                {"text": line.text, "confidence": round(line.confidence, 3)}
                for line in report.low_confidence
            ],
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{source.stem}.facts.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _commit(rows: list[dict]) -> int:
    """Insert into the same `facts` table the FHIR loader writes.

    `ON CONFLICT DO NOTHING` on the primary key makes re-ingesting the same
    scan idempotent -- the fact ids are derived from file, page, line and
    code, so a second run of an unchanged document is a no-op.
    """
    from psycopg.types.json import Jsonb

    from retrieval.db import DbSettings, FACTS_TABLE

    settings = DbSettings.load()
    inserted = 0
    with settings.connect() as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    f"""
                    INSERT INTO {FACTS_TABLE}
                        (fact_id, patient_id, category, safety_class, text,
                         code_system, code, display, value, unit, date, status,
                         source_resource_type, source_resource_id, encounter_id,
                         also_in)
                    VALUES (%(fact_id)s, %(patient_id)s, %(category)s,
                            %(safety_class)s, %(text)s, %(code_system)s, %(code)s,
                            %(display)s, %(value)s, %(unit)s, %(date)s, %(status)s,
                            %(source_resource_type)s, %(source_resource_id)s,
                            %(encounter_id)s, %(also_in)s)
                    ON CONFLICT (fact_id) DO NOTHING
                    """,
                    {**row, "also_in": Jsonb(row.get("also_in") or [])},
                )
                inserted += cur.rowcount
        conn.commit()
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="scanned image or PDF")
    parser.add_argument("--patient", required=True, help="patient_id to attach facts to")
    parser.add_argument(
        "--commit", action="store_true", help="actually insert into Postgres"
    )
    parser.add_argument(
        "--text-layer",
        action="store_true",
        help="read a born-digital PDF's text layer instead of recognising pixels",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="use legacy RapidOCR engine instead of 5-model pipeline",
    )
    parser.add_argument(
        "--profile",
        "-p",
        default=None,
        help="hardware execution profile (e.g. laptop, jetson)",
    )
    parser.add_argument(
        "--device",
        "-d",
        choices=["auto", "cuda", "cpu"],
        default=None,
        help="execution device override (auto, cuda, cpu)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=None,
        help="PDF rasterization DPI (default: profile setting, e.g. 250)",
    )
    parser.add_argument(
        "--date",
        help="the date the document is about (YYYY-MM-DD). Defaults to today; "
        "a wrong date moves facts in and out of every recency window.",
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"[FAIL] no such file: {args.source}")
        return 2

    _rule(f"1. SOURCE  {args.source.name}")
    print(f"   {args.source.resolve()}")
    print(f"   {args.source.stat().st_size / 1024:.0f} KB")

    document_date = (
        datetime.fromisoformat(args.date).astimezone() if args.date else None
    )

    if args.legacy or args.text_layer:
        try:
            document = (
                read_pdf_text_layer(args.source) if args.text_layer else read_document(args.source)
            )
        except OcrUnavailable as exc:
            print(f"[FAIL] {exc}")
            return 2

        _rule(f"2. OCR  ({document.engine})")
        print(f"   {len(document.lines)} lines | mean confidence {document.mean_confidence:.2f}")
        for line in document.lines:
            mark = "  " if line.is_reliable else "!!"
            print(f"   {mark} {line.confidence:.2f}  {line.text}")

        report = extract_facts(document, args.patient, document_date=document_date)
        rows = [fact.as_fact_row() for fact in report.facts]
        json_path = _write_json(report, args.source, args.out_dir)
        facts_count = len(report.facts)
    else:
        from config.loader import PolicyConfig
        from models.manager import ModelManager
        from pipeline.orchestrator import PipelineOrchestrator

        policy = PolicyConfig.load(profile=args.profile)
        manager = ModelManager(policy=policy.memory, device=args.device)
        orchestrator = PipelineOrchestrator(model_manager=manager, policy_config=policy)
        result = orchestrator.process_document(
            args.source,
            patient_id=args.patient,
            document_date=document_date,
            dpi=args.dpi,
        )

        _rule(
            f"2. 5-MODEL PIPELINE  (v{result.pipeline_version}) "
            f"[{manager.device.upper()}:{manager.precision.upper()} | Profile: {policy.default_profile}]"
        )
        print(f"   {len(result.ocr_results)} lines | mean confidence {result.mean_confidence:.2f}")
        print(f"   stages: {', '.join(k.replace('_ms', '') for k in result.stage_timings.keys())}")
        for line in result.ocr_results:
            mark = "  " if line.is_reliable else "!!"
            hand = "[H]" if line.is_handwritten else "   "
            print(f"   {mark} {hand} {line.confidence:.2f}  {line.text}")

        _rule("3. EXTRACTED FACTS (with Provenance & Calibrated Confidence)")
        print(f"   sections recognised: {', '.join(result.sections_seen) or 'none'}")
        print(f"   {len(result.entities)} facts | {len(result.unmatched)} unmatched | {len(result.ambiguous)} ambiguous\n")
        for fact in result.entities:
            dose = f"{fact.value} {fact.unit}" if fact.value else ""
            alt = f"  +{len(fact.also_in)} other code(s)" if fact.also_in else ""
            print(f"   {fact.category:11} {(fact.display or '')[:42]:44} {dose:9} "
                  f"{fact.confidence:.2f}  {fact.provenance.document_id}#p{fact.provenance.page}L{fact.line_no}{alt}")

        # Reviewable JSON artifact
        json_payload = {
            "source_file": args.source.name,
            "pipeline_version": result.pipeline_version,
            "profile": policy.default_profile,
            "device": manager.device,
            "precision": manager.precision,
            "extracted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sections_seen": result.sections_seen,
            "mean_confidence": result.mean_confidence,
            "stage_timings": result.stage_timings,
            "stage_versions": result.stage_versions,
            "memory_status": result.memory_status,
            "model_metadata": result.model_metadata,
            "counts": {
                "facts": len(result.entities),
                "unmatched_lines": len(result.unmatched),
                "ambiguous_lines": len(result.ambiguous),
                "low_confidence_lines": len(result.low_confidence),
            },
            "facts": [
                {
                    **e.as_fact_row(),
                    "_provenance": e.provenance.to_dict(),
                    "_calibrated_confidence": e.confidence,
                }
                for e in result.entities
            ],
            "not_extracted": {
                "unmatched": result.unmatched,
                "ambiguous": result.ambiguous,
                "low_confidence": result.low_confidence,
            },
        }
        args.out_dir.mkdir(parents=True, exist_ok=True)
        json_path = args.out_dir / f"{args.source.stem}.facts.json"
        json_path.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")
        rows = [e.as_fact_row() for e in result.entities]
        facts_count = len(result.entities)

    _rule("4. JSON ARTIFACT")
    print(f"   {json_path}")
    print(f"   {json_path.stat().st_size / 1024:.1f} KB | {facts_count} fact objects")

    _rule("5. CORPUS")
    if not args.commit:
        print(f"   dry run: {len(rows)} rows NOT written.")
        print("   Review the JSON above, then re-run with --commit.")
        return 0

    try:
        inserted = _commit(rows)
    except Exception as exc:  # noqa: BLE001 - a CLI reports, it does not raise
        print(f"[FAIL] could not write to Postgres: {exc}")
        print("       is it up?  docker compose up -d postgres")
        return 1

    print(f"   inserted {inserted} of {len(rows)} rows into `facts`")
    if inserted < len(rows):
        print(f"   ({len(rows) - inserted} already present; re-ingest is idempotent)")
    print(f"\n   Now ask about patient {args.patient} through /api/query.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
