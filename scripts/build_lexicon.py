"""Regenerate lexicon.csv from facts.jsonl.

The lexicon is the distinct ``(category, code_system, code, display)`` tuples
present across the corpus. Rerun this whenever the corpus changes -- nothing
downstream may assume the current 1,170 rows.

facts.jsonl is ~700 MB, so this streams line by line and never holds more
than the distinct-tuple set in memory.

    pwsh> python scripts/build_lexicon.py
    pwsh> python scripts/build_lexicon.py --facts facts.jsonl --out lexicon.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def build(facts_path: Path, out_path: Path, progress_every: int = 100_000) -> int:
    tuples: set[tuple[str, str, str, str]] = set()
    line_count = 0

    with facts_path.open(encoding="utf-8") as handle:
        for line in handle:
            line_count += 1
            if not line.strip():
                continue
            record = json.loads(line)
            tuples.add(
                (
                    record.get("category"),
                    record.get("code_system"),
                    record.get("code"),
                    record.get("display"),
                )
            )
            if progress_every and line_count % progress_every == 0:
                print(
                    f"  {line_count:,} facts -> {len(tuples):,} distinct",
                    file=sys.stderr,
                )

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["category", "code_system", "code", "display"])
        writer.writerows(sorted(tuples, key=lambda row: tuple(str(x) for x in row)))

    print(
        f"{line_count:,} facts -> {len(tuples):,} lexicon rows -> {out_path}",
        file=sys.stderr,
    )
    return len(tuples)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", type=Path, default=REPO_ROOT / "facts.jsonl")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "lexicon.csv")
    parser.add_argument("--progress-every", type=int, default=100_000)
    args = parser.parse_args()

    if not args.facts.exists():
        parser.error(f"facts file not found: {args.facts}")
    build(args.facts, args.out, args.progress_every)


if __name__ == "__main__":
    main()
