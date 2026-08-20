"""Score the pipeline against the gold evaluation set.

Unlike ``scripts/run_probes.py`` -- which records what resolved and claims no
accuracy -- this reports precision, recall and F1, because ``data/eval_set.jsonl``
carries gold ``fact_id`` lists derived independently of the code being scored.
These are the only numbers in this repo that may be quoted.

    pwsh> python scripts/run_eval.py
    pwsh> python scripts/run_eval.py --archetype allergy_positive --verbose

Scoring, per query:

* ``TP`` retrieved and required   * ``FN`` required but not retrieved
* ``FP`` retrieved, neither required nor allowed

Two-tier relevance matters here. ``allowed`` facts are defensible-but-not-
required answers (the "Allergic disposition (finding)" fact alongside a
specific allergy); counting them as false positives would score a judgement
call as a bug. They never count toward recall either.

Headline figures are **micro-averaged** -- TP/FP/FN summed across queries, then
divided once. Macro-averaging would let a one-fact query outweigh a
forty-fact one. Queries whose gold is deliberately empty are excluded from
precision/recall (both are undefined with nothing to find) and reported
separately as abstention accuracy: did the pipeline correctly return nothing?
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.data_layer import get_patient  # noqa: E402
from backend.retrieve import retrieve  # noqa: E402
from retrieval.models import MatchQuality  # noqa: E402
from retrieval.ner import parse_query  # noqa: E402

DEFAULT_SET = REPO_ROOT / "data" / "eval_set.jsonl"
DEFAULT_REPORT = REPO_ROOT / "eval" / "report.md"


def load_set(path: Path) -> tuple[dict, list[dict]]:
    meta: dict = {}
    entries: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if "_meta" in record:
                meta = record["_meta"]
                continue
            entries.append(record)
    return meta, entries


def score_one(entry: dict, today: date) -> dict:
    start = time.perf_counter()
    parsed = parse_query(entry["query"], today=today)
    result = retrieve(parsed, entry["patient_id"])
    elapsed_ms = (time.perf_counter() - start) * 1000

    got = {f.fact_id for f in result.all_facts()}
    gold = set(entry["gold_fact_ids"])
    allowed = set(entry["allowed_fact_ids"])

    tp = len(got & gold)
    fn = len(gold - got)
    fp = len(got - gold - allowed)

    return {
        "query_id": entry["query_id"],
        "query": entry["query"],
        "archetype": entry["archetype"],
        "patient_id": entry["patient_id"],
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "gold_n": len(gold),
        "got_n": len(got),
        "abstain_case": not gold,
        "clean_abstention": (not gold) and fp == 0,
        "qualities": sorted({e.quality.value for e in parsed.entities}),
        "unresolved_spans": result.unresolved_spans,
        "ms": elapsed_ms,
    }


def _pr(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def aggregate(rows: list[dict]) -> dict:
    scored = [r for r in rows if not r["abstain_case"]]
    abstain = [r for r in rows if r["abstain_case"]]
    tp = sum(r["tp"] for r in scored)
    fp = sum(r["fp"] for r in scored)
    fn = sum(r["fn"] for r in scored)
    precision, recall, f1 = _pr(tp, fp, fn)
    return {
        "queries": len(rows),
        "scored": len(scored),
        "abstain": len(abstain),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "clean_abstentions": sum(1 for r in abstain if r["clean_abstention"]),
        "perfect": sum(1 for r in scored if r["fp"] == 0 and r["fn"] == 0),
        "zero_recall": sum(1 for r in scored if r["tp"] == 0),
    }


def render(meta: dict, rows: list[dict], overall: dict, today: date) -> str:
    lines = [
        "# Gold evaluation",
        "",
        "**Generated** — rebuild the set, then rerun:",
        "",
        "```pwsh",
        "python scripts/build_eval_set.py",
        "python scripts/run_eval.py",
        "```",
        "",
        f"`data/eval_set.jsonl` — {overall['queries']} queries, reference date {today.isoformat()}.",
        "",
        "Gold `fact_id` lists are resolved from hand-specified clinical clauses and never "
        "from `parse_query()` or `retrieve()`, so these numbers measure the pipeline rather "
        "than reflecting it back. This is the artifact `tests/queries.md` explicitly is not: "
        "figures below are quotable, the probe file's are not.",
        "",
        "---",
        "",
        "## Headline",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| queries | {overall['queries']} |",
        f"| scored (non-empty gold) | {overall['scored']} |",
        f"| abstention cases (empty gold) | {overall['abstain']} |",
        f"| **precision** (micro) | **{overall['precision']:.3f}** |",
        f"| **recall** (micro) | **{overall['recall']:.3f}** |",
        f"| **F1** (micro) | **{overall['f1']:.3f}** |",
        f"| true positives | {overall['tp']:,} |",
        f"| false positives | {overall['fp']:,} |",
        f"| false negatives | {overall['fn']:,} |",
        f"| exact queries (no FP, no FN) | {overall['perfect']}/{overall['scored']} |",
        f"| queries retrieving nothing correct | {overall['zero_recall']}/{overall['scored']} |",
        f"| clean abstentions | {overall['clean_abstentions']}/{overall['abstain']} |",
        "",
        *(
            [
                "> **This set is saturated and has stopped discriminating.**",
                "> Every scored query is exact and every abstention is clean, which means "
                "the next regression will show up as a drop from 1.000 but the next "
                "*improvement* cannot show up at all. A perfect score is a signal to add "
                "harder queries, not a result to quote. Archetypes worth adding: queries "
                "mixing several categories at once, misspelled drug names, questions whose "
                "answer is a trend rather than a list, and the narrative REPORT path that "
                "no archetype touches yet.",
                "",
            ]
            if overall["f1"] >= 1.0 and overall["clean_abstentions"] == overall["abstain"]
            else []
        ),
        "### Reading these numbers",
        "",
        "**Recall is the informative figure; precision is structurally easy here.** "
        "Retrieval is a strict `category` + `code IN (...)` filter, so it can only return "
        "facts whose codes the parser already produced. A false positive therefore requires "
        "the parser to resolve a span to a confidently *wrong* code set -- much rarer than "
        "resolving it to nothing at all. A high precision number says the vocabulary is not "
        "mismapped; it does not say retrieval is good. Read the recall column, and the miss "
        "table below it, first.",
        "",
        "## By archetype",
        "",
        "| archetype | n | precision | recall | F1 | notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    archetypes: dict[str, list[dict]] = {}
    for row in rows:
        archetypes.setdefault(row["archetype"], []).append(row)

    for name in sorted(archetypes):
        group = archetypes[name]
        scored = [r for r in group if not r["abstain_case"]]
        if scored:
            tp = sum(r["tp"] for r in scored)
            fp = sum(r["fp"] for r in scored)
            fn = sum(r["fn"] for r in scored)
            precision, recall, f1 = _pr(tp, fp, fn)
            note = f"{sum(1 for r in scored if r['tp'] == 0)} retrieved nothing correct" if any(
                r["tp"] == 0 for r in scored
            ) else ""
            lines.append(
                f"| {name} | {len(group)} | {precision:.3f} | {recall:.3f} | {f1:.3f} | {note} |"
            )
        else:
            clean = sum(1 for r in group if r["clean_abstention"])
            lines.append(
                f"| {name} | {len(group)} | — | — | — | abstention: {clean}/{len(group)} clean |"
            )

    failures = [r for r in rows if (not r["abstain_case"] and (r["fn"] or r["fp"]))]
    lines += ["", "## Queries that missed", ""]
    if failures:
        lines += ["| query | archetype | gold | got | FN | FP | unresolved |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for row in sorted(failures, key=lambda r: (-r["fn"], -r["fp"])):
            spans = ", ".join(f"`{s}`" for s in row["unresolved_spans"]) or "—"
            lines.append(
                f"| `{row['query']}` | {row['archetype']} | {row['gold_n']} | {row['got_n']} "
                f"| {row['fn']} | {row['fp']} | {spans} |"
            )
    else:
        lines.append("- none")

    dirty = [r for r in rows if r["abstain_case"] and not r["clean_abstention"]]
    lines += ["", "## Abstention cases that returned something", ""]
    if dirty:
        lines += ["| query | archetype | facts returned |", "| --- | --- | --- |"]
        for row in dirty:
            lines.append(f"| `{row['query']}` | {row['archetype']} | {row['fp']} |")
    else:
        lines.append("- none: every empty-gold query correctly returned nothing")

    latency = sorted(r["ms"] for r in rows)
    if latency:
        mid = latency[len(latency) // 2]
        p95 = latency[max(0, int(len(latency) * 0.95) - 1)]
        lines += [
            "",
            "## Latency",
            "",
            "Parse plus retrieve, warm cache. Excludes brief rendering.",
            "",
            f"- median {mid:.1f} ms · p95 {p95:.1f} ms · max {latency[-1]:.1f} ms",
        ]

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", dest="set_path", type=Path, default=DEFAULT_SET)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--archetype", default=None, help="score only this archetype")
    parser.add_argument("--verbose", action="store_true", help="print every query")
    parser.add_argument("--no-report", action="store_true", help="print only, write nothing")
    args = parser.parse_args()

    if not args.set_path.exists():
        parser.error(f"{args.set_path} not found -- run scripts/build_eval_set.py first")

    meta, entries = load_set(args.set_path)
    today = date.fromisoformat(meta.get("eval_today") or date.today().isoformat())
    if args.archetype:
        entries = [e for e in entries if e["archetype"] == args.archetype]
        if not entries:
            parser.error(f"no queries with archetype {args.archetype!r}")

    rows = [score_one(entry, today) for entry in entries]
    overall = aggregate(rows)

    if args.verbose:
        for row in rows:
            flag = "ok " if not (row["fp"] or row["fn"]) else "MISS"
            print(
                f"  {flag} {row['query_id']} {row['query'][:52]:54} "
                f"gold={row['gold_n']:4d} got={row['got_n']:4d} fn={row['fn']:4d} fp={row['fp']:4d}"
            )

    print(
        f"\n{overall['scored']} scored + {overall['abstain']} abstention = {overall['queries']} queries\n"
        f"precision {overall['precision']:.3f}  recall {overall['recall']:.3f}  F1 {overall['f1']:.3f}\n"
        f"exact {overall['perfect']}/{overall['scored']}  "
        f"clean abstentions {overall['clean_abstentions']}/{overall['abstain']}"
    )

    if not args.no_report:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render(meta, rows, overall, today), encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
