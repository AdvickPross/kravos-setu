# Gold evaluation

**Generated** — rebuild the set, then rerun:

```pwsh
python scripts/build_eval_set.py
python scripts/run_eval.py
```

`data/eval_set.jsonl` — 100 queries, reference date 2026-08-21.

Gold `fact_id` lists are resolved from hand-specified clinical clauses and never from `parse_query()` or `retrieve()`, so these numbers measure the pipeline rather than reflecting it back. This is the artifact `tests/queries.md` explicitly is not: figures below are quotable, the probe file's are not.

---

## Headline

| metric | value |
| --- | --- |
| queries | 100 |
| scored (non-empty gold) | 74 |
| abstention cases (empty gold) | 26 |
| **precision** (micro) | **1.000** |
| **recall** (micro) | **1.000** |
| **F1** (micro) | **1.000** |
| true positives | 506 |
| false positives | 0 |
| false negatives | 0 |
| exact queries (no FP, no FN) | 74/74 |
| queries retrieving nothing correct | 0/74 |
| clean abstentions | 26/26 |

> **This set is saturated and has stopped discriminating.**
> Every scored query is exact and every abstention is clean, which means the next regression will show up as a drop from 1.000 but the next *improvement* cannot show up at all. A perfect score is a signal to add harder queries, not a result to quote. Archetypes worth adding: queries mixing several categories at once, misspelled drug names, questions whose answer is a trend rather than a list, and the narrative REPORT path that no archetype touches yet.

### Reading these numbers

**Recall is the informative figure; precision is structurally easy here.** Retrieval is a strict `category` + `code IN (...)` filter, so it can only return facts whose codes the parser already produced. A false positive therefore requires the parser to resolve a span to a confidently *wrong* code set -- much rarer than resolving it to nothing at all. A high precision number says the vocabulary is not mismapped; it does not say retrieval is good. Read the recall column, and the miss table below it, first.

## By archetype

| archetype | n | precision | recall | F1 | notes |
| --- | --- | --- | --- | --- | --- |
| abbreviation | 8 | 1.000 | 1.000 | 1.000 |  |
| allergy_negative_other_on_file | 4 | — | — | — | abstention: 4/4 clean |
| allergy_none_on_file | 6 | — | — | — | abstention: 6/6 clean |
| allergy_positive | 12 | 1.000 | 1.000 | 1.000 |  |
| category_question | 8 | 1.000 | 1.000 | 1.000 |  |
| class_rollup | 4 | — | — | — | abstention: 4/4 clean |
| condition_absent | 4 | — | — | — | abstention: 4/4 clean |
| condition_present | 10 | 1.000 | 1.000 | 1.000 |  |
| lab_date_window | 7 | 1.000 | 1.000 | 1.000 |  |
| lab_multi_code | 5 | 1.000 | 1.000 | 1.000 |  |
| lab_single_code | 6 | 1.000 | 1.000 | 1.000 |  |
| medication_absent | 3 | — | — | — | abstention: 3/3 clean |
| medication_present | 8 | 1.000 | 1.000 | 1.000 |  |
| negation | 5 | 1.000 | 1.000 | 1.000 |  |
| unresolvable | 5 | — | — | — | abstention: 5/5 clean |
| vital | 5 | 1.000 | 1.000 | 1.000 |  |

## Queries that missed

- none

## Abstention cases that returned something

- none: every empty-gold query correctly returned nothing

## Latency

Parse plus retrieve, warm cache. Excludes brief rendering.

- median 0.8 ms · p95 16.6 ms · max 67.0 ms
