"""Run the probe queries and regenerate tests/queries.md.

    pwsh> python scripts/run_probes.py
    pwsh> python scripts/run_probes.py --today 2026-08-19

A debugging instrument: it records what resolved and what didn't, so a
change to the normalizer or the synonym file shows up as a diff. It is NOT
an evaluation set and reports no accuracy number -- the gold eval set
(80-100 queries with gold fact IDs) is a separate artifact.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from pathlib import Path

import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from retrieval.lexicon import Lexicon  # noqa: E402
from retrieval.models import MatchQuality, ParsedQuery  # noqa: E402
from retrieval.ner import QueryNER  # noqa: E402

DEFAULT_QUERIES = REPO_ROOT / "data" / "probe_queries.txt"
DEFAULT_OUT = REPO_ROOT / "tests" / "queries.md"


def load_queries(path: Path) -> list[str]:
    queries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            queries.append(line)
    return queries


def _codes(entity) -> str:
    if not entity.codes:
        return "—"
    shown = ", ".join(f"`{c}`" for c in entity.codes[:4])
    if len(entity.codes) > 4:
        shown += f" +{len(entity.codes) - 4}"
    return shown


def render(queries: list[str], results: list[ParsedQuery], today: date) -> str:
    counts: Counter[str] = Counter()
    unresolved: list[tuple[str, str]] = []

    lines = [
        "# Probe queries",
        "",
        "**Generated** — edit `data/probe_queries.txt`, then rerun:",
        "",
        "```pwsh",
        "python scripts/run_probes.py",
        "```",
        "",
        f"Run against `lexicon.csv` + `data/synonyms.csv`, with today = {today.isoformat()}.",
        "",
        "This is a **debugging instrument, not an evaluation set**. It records what "
        "resolved and what didn't so that a change in the normalizer or the synonym "
        "file shows up as a diff. No accuracy number is claimed or implied; the gold "
        "eval set (80–100 queries with gold fact IDs) is a separate artifact.",
        "",
        "---",
        "",
    ]

    for number, (query, parsed) in enumerate(zip(queries, results), start=1):
        window = "none"
        if parsed.date_from or parsed.date_to:
            window = f"{parsed.date_from or '…'} → {parsed.date_to or '…'}"
        lines += [f"### {number}. `{query}`", "", f"*date window:* {window}", ""]
        if not parsed.entities:
            lines += ["Nothing resolved.", ""]
            unresolved.append((query, "(no entities)"))
            continue
        lines += [
            "| span | quality | category | codes | display | neg |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for entity in parsed.entities:
            counts[entity.quality.value] += 1
            if entity.quality is MatchQuality.UNRESOLVED:
                unresolved.append((query, entity.span))
            lines.append(
                f"| `{entity.span}` | {entity.quality.value} | {entity.category or '—'} "
                f"| {_codes(entity)} | {entity.display or '—'} | {'yes' if entity.negated else ''} |"
            )
        lines.append("")

    lines += ["---", "", "## What resolved", "", "| quality | entities |", "| --- | --- |"]
    for quality in ("exact", "synonym", "fuzzy", "unresolved"):
        lines.append(f"| {quality} | {counts.get(quality, 0)} |")

    lines += ["", "## What did not resolve", ""]
    if unresolved:
        for query, span in unresolved:
            lines.append(f"- `{span}` — in *{query}*")
    else:
        lines.append("- nothing")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--lexicon", type=Path, default=None)
    parser.add_argument("--synonyms", type=Path, default=None)
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()

    kwargs = {}
    if args.lexicon:
        kwargs["lexicon_path"] = args.lexicon
    if args.synonyms:
        kwargs["synonyms_path"] = args.synonyms
    ner = QueryNER(lexicon=Lexicon(**kwargs) if kwargs else None)

    queries = load_queries(args.queries)
    results = [ner.parse(q, today=args.today) for q in queries]
    args.out.write_text(render(queries, results, args.today), encoding="utf-8")
    print(f"{len(queries)} probes -> {args.out}")


if __name__ == "__main__":
    main()
