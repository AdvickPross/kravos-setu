"""Split facts.jsonl into one file per patient.

facts.jsonl is ~700 MB, so this streams line by line and never holds more
than one line -- plus per-patient counts -- in memory. Facts for a given
patient are contiguous in the source file (verified: 952 patient-id
transitions for 952 distinct patients), so a single output handle is open
at a time; if that ever stops holding, the fallback path below re-opens a
patient's file in append mode instead of assuming it's still memory-safe
to buffer.

Idempotent and re-runnable: each run starts by clearing
``facts_by_patient/`` so a rerun never mixes with a stale prior split (e.g.
after a corpus change).

    pwsh> python scripts/split_facts.py
    pwsh> python scripts/split_facts.py --facts facts.jsonl --out-dir facts_by_patient
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_FACT_COUNT = 1_083_231


def split(facts_path: Path, out_dir: Path, progress_every: int = 100_000) -> int:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    counts: dict[str, int] = {}
    line_count = 0
    current_patient: str | None = None
    handle = None

    def close_current() -> None:
        if handle is not None:
            handle.close()

    try:
        with facts_path.open(encoding="utf-8") as facts_file:
            for line in facts_file:
                if not line.strip():
                    continue
                line_count += 1
                record = json.loads(line)
                patient_id = record["patient_id"]

                if patient_id != current_patient:
                    close_current()
                    mode = "a" if patient_id in counts else "w"
                    handle = (out_dir / f"{patient_id}.jsonl").open(
                        mode, encoding="utf-8"
                    )
                    current_patient = patient_id

                handle.write(line if line.endswith("\n") else line + "\n")
                counts[patient_id] = counts.get(patient_id, 0) + 1

                if progress_every and line_count % progress_every == 0:
                    print(
                        f"  {line_count:,} facts -> {len(counts):,} patients",
                        file=sys.stderr,
                    )
    finally:
        close_current()

    index = {
        "total_facts": line_count,
        "total_patients": len(counts),
        "counts": counts,
    }
    (out_dir / "_index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True), encoding="utf-8"
    )

    print(
        f"{line_count:,} facts -> {len(counts):,} patient files -> {out_dir}",
        file=sys.stderr,
    )
    return line_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", type=Path, default=REPO_ROOT / "facts.jsonl")
    parser.add_argument(
        "--out-dir", type=Path, default=REPO_ROOT / "facts_by_patient"
    )
    parser.add_argument("--progress-every", type=int, default=100_000)
    parser.add_argument(
        "--expect",
        type=int,
        default=EXPECTED_FACT_COUNT,
        help="assert this many total facts were written (0 to skip)",
    )
    args = parser.parse_args()

    if not args.facts.exists():
        parser.error(f"facts file not found: {args.facts}")

    total = split(args.facts, args.out_dir, args.progress_every)

    if args.expect:
        assert total == args.expect, (
            f"expected {args.expect:,} facts, wrote {total:,}"
        )


if __name__ == "__main__":
    main()
