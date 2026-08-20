"""The gold evaluation harness.

Two things are being protected here. The scoring arithmetic, which is easy to
get subtly wrong and would then misreport every number in `eval/report.md`;
and the integrity of `data/eval_set.jsonl` itself -- a gold set that quietly
drifts out of agreement with the corpus is worse than no gold set, because it
still produces confident-looking figures.

Scoring is tested against hand-built rows rather than the real corpus, so a
corpus change can never make an arithmetic test pass or fail for the wrong
reason.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from scripts.run_eval import _pr, aggregate, load_set

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_SET_PATH = REPO_ROOT / "data" / "eval_set.jsonl"


def _row(**overrides) -> dict:
    base = {
        "query_id": "q001",
        "query": "x",
        "archetype": "a",
        "patient_id": "p",
        "tp": 0,
        "fp": 0,
        "fn": 0,
        "gold_n": 0,
        "got_n": 0,
        "abstain_case": False,
        "clean_abstention": False,
        "qualities": [],
        "unresolved_spans": [],
        "ms": 1.0,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# scoring arithmetic
# --------------------------------------------------------------------------


def test_pr_perfect():
    assert _pr(10, 0, 0) == (1.0, 1.0, 1.0)


def test_pr_all_wrong():
    assert _pr(0, 5, 5) == (0.0, 0.0, 0.0)


def test_pr_half_recall():
    precision, recall, f1 = _pr(tp=5, fp=0, fn=5)
    assert precision == 1.0
    assert recall == 0.5
    assert f1 == pytest.approx(2 / 3)


def test_pr_never_divides_by_zero():
    assert _pr(0, 0, 0) == (0.0, 0.0, 0.0)


def test_aggregate_is_micro_averaged_not_macro():
    """One 40-fact query and one 1-fact query must not carry equal weight.

    Macro-averaging would report recall 0.5 here (one query perfect, one
    missed entirely). Micro-averaging reports 40/41 -- the pipeline found
    almost every fact it should have.
    """
    rows = [
        _row(tp=40, fp=0, fn=0, gold_n=40),
        _row(tp=0, fp=0, fn=1, gold_n=1),
    ]
    overall = aggregate(rows)
    assert overall["recall"] == pytest.approx(40 / 41)


def test_aggregate_excludes_abstention_cases_from_precision_recall():
    """Empty-gold queries have undefined precision and recall; counting them
    as zeros would drag the headline down for correct behaviour."""
    rows = [
        _row(tp=4, fp=0, fn=0, gold_n=4),
        _row(abstain_case=True, clean_abstention=True),
    ]
    overall = aggregate(rows)
    assert overall["scored"] == 1
    assert overall["abstain"] == 1
    assert overall["recall"] == 1.0
    assert overall["clean_abstentions"] == 1


def test_aggregate_counts_dirty_abstention():
    rows = [_row(abstain_case=True, clean_abstention=False, fp=3)]
    overall = aggregate(rows)
    assert overall["clean_abstentions"] == 0
    assert overall["abstain"] == 1


def test_aggregate_zero_recall_counts_only_scored_queries():
    rows = [
        _row(tp=0, fp=0, fn=2, gold_n=2),
        _row(abstain_case=True, clean_abstention=True),
    ]
    assert aggregate(rows)["zero_recall"] == 1


# --------------------------------------------------------------------------
# the gold set on disk
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def eval_set():
    if not EVAL_SET_PATH.exists():
        pytest.skip("data/eval_set.jsonl not built -- run scripts/build_eval_set.py")
    return load_set(EVAL_SET_PATH)


def test_meta_pins_a_reference_date(eval_set):
    """Relative-date gold windows are meaningless without a fixed today."""
    meta, _ = eval_set
    assert date.fromisoformat(meta["eval_today"])


def test_size_is_within_the_specified_range(eval_set):
    _, entries = eval_set
    assert 80 <= len(entries) <= 100


def test_query_ids_unique(eval_set):
    _, entries = eval_set
    ids = [e["query_id"] for e in entries]
    assert len(ids) == len(set(ids))


def test_no_duplicate_query_patient_pairs(eval_set):
    _, entries = eval_set
    pairs = [(e["patient_id"], e["query"]) for e in entries]
    assert len(pairs) == len(set(pairs))


def test_every_entry_carries_a_rationale(eval_set):
    """A gold entry nobody can justify later is a gold entry nobody can fix."""
    _, entries = eval_set
    assert all(e["rationale"].strip() for e in entries)


def test_gold_and_allowed_never_overlap(eval_set):
    """A fact that is both required and merely-allowed would be double counted."""
    _, entries = eval_set
    for entry in entries:
        assert not (set(entry["gold_fact_ids"]) & set(entry["allowed_fact_ids"])), entry["query_id"]


def test_abstention_archetypes_have_empty_gold(eval_set):
    """The whole point of these archetypes is that the right answer is nothing."""
    _, entries = eval_set
    for entry in entries:
        if "abstain" in entry["tags"]:
            assert entry["gold_fact_ids"] == [], entry["query_id"]


def test_non_abstention_archetypes_have_non_empty_gold(eval_set):
    """A query that was meant to find something but whose gold resolved empty
    is silently scored as a free pass. Catch it here instead."""
    _, entries = eval_set
    for entry in entries:
        if "abstain" not in entry["tags"]:
            assert entry["gold_fact_ids"], f"{entry['query_id']} {entry['query']!r} has empty gold"


def test_windowed_entries_carry_both_bounds(eval_set):
    _, entries = eval_set
    for entry in entries:
        spec = entry["gold_spec"]
        if spec["window_days"]:
            assert spec["date_from"] and spec["date_to"], entry["query_id"]
        else:
            assert spec["date_from"] is None and spec["date_to"] is None, entry["query_id"]


def test_gold_fact_ids_still_exist_in_the_corpus(eval_set):
    """Same discipline as tests/test_lexicon.py: gold that points at facts the
    corpus no longer contains must fail loudly rather than shrink quietly."""
    from backend.data_layer import get_patient_facts

    _, entries = eval_set
    checked = 0
    for entry in entries:
        if not entry["gold_fact_ids"]:
            continue
        known = {f.fact_id for f in get_patient_facts(entry["patient_id"])}
        if not known:
            pytest.skip("facts_by_patient/ not present -- run scripts/split_facts.py")
        missing = set(entry["gold_fact_ids"]) - known
        assert not missing, f"{entry['query_id']}: {len(missing)} gold ids no longer in corpus"
        checked += 1
    assert checked > 0


def test_gold_is_not_derived_from_the_pipeline():
    """A guard against the one mistake that would void every number here.

    If gold were produced by running `retrieve()`, the set would score a
    perfect 1.0 by construction. It does not, and this test fails the moment
    someone 'fixes' the eval by regenerating gold from the code under test.
    """
    import ast

    source = (REPO_ROOT / "scripts" / "build_eval_set.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Checked structurally, not by string match: the builder's comments discuss
    # parse_query at length precisely to explain why it is not used, and a
    # substring test would trip over its own documentation.
    banned_modules = {"retrieval.ner", "retrieval.lexicon", "backend.retrieve"}
    banned_names = {"parse_query", "retrieve", "QueryNER"}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in banned_modules:
            pytest.fail(f"gold builder imports from {node.module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in banned_modules, f"gold builder imports {alias.name}"
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            assert name not in banned_names, f"gold builder calls {name}()"
