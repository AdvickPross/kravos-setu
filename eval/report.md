# Gold evaluation

**Generated** — rebuild the set, then rerun:

```pwsh
python scripts/build_eval_set.py
python scripts/run_eval.py
```

`data/eval_set.jsonl` — 97 queries, reference date 2026-08-21.

Gold `fact_id` lists are resolved from hand-specified clinical clauses and never from `parse_query()` or `retrieve()`, so these numbers measure the pipeline rather than reflecting it back. This is the artifact `tests/queries.md` explicitly is not: figures below are quotable, the probe file's are not.

---

## Headline

| metric | value |
| --- | --- |
| queries | 97 |
| scored (non-empty gold) | 66 |
| abstention cases (empty gold) | 31 |
| **precision** (micro) | **0.940** |
| **recall** (micro) | **0.833** |
| **F1** (micro) | **0.883** |
| true positives | 343 |
| false positives | 22 |
| false negatives | 69 |
| exact queries (no FP, no FN) | 50/66 |
| queries retrieving nothing correct | 10/66 |
| clean abstentions | 31/31 |

### Reading these numbers

**Recall is the informative figure; precision is structurally easy here.** Retrieval is a strict `category` + `code IN (...)` filter, so it can only return facts whose codes the parser already produced. A false positive therefore requires the parser to resolve a span to a confidently *wrong* code set -- much rarer than resolving it to nothing at all. A high precision number says the vocabulary is not mismapped; it does not say retrieval is good. Read the recall column, and the miss table below it, first.

## By archetype

| archetype | n | precision | recall | F1 | notes |
| --- | --- | --- | --- | --- | --- |
| abbreviation | 8 | 1.000 | 0.949 | 0.974 | 1 retrieved nothing correct |
| allergy_negative_other_on_file | 6 | — | — | — | abstention: 6/6 clean |
| allergy_none_on_file | 6 | — | — | — | abstention: 6/6 clean |
| allergy_positive | 12 | 1.000 | 0.750 | 0.857 | 3 retrieved nothing correct |
| class_rollup | 4 | — | — | — | abstention: 4/4 clean |
| condition_absent | 4 | — | — | — | abstention: 4/4 clean |
| condition_present | 10 | 1.000 | 0.824 | 0.903 | 1 retrieved nothing correct |
| lab_date_window | 7 | 0.450 | 1.000 | 0.621 |  |
| lab_multi_code | 5 | 1.000 | 0.695 | 0.820 | 2 retrieved nothing correct |
| lab_single_code | 6 | 1.000 | 0.737 | 0.848 | 2 retrieved nothing correct |
| medication_absent | 3 | — | — | — | abstention: 3/3 clean |
| medication_present | 8 | 1.000 | 1.000 | 1.000 |  |
| negation | 5 | 1.000 | 0.950 | 0.974 |  |
| unresolvable | 8 | — | — | — | abstention: 8/8 clean |
| vital | 5 | 1.000 | 0.759 | 0.863 | 1 retrieved nothing correct |

## Queries that missed

| query | archetype | gold | got | FN | FP | unresolved |
| --- | --- | --- | --- | --- | --- | --- |
| `most recent blood glucose` | lab_multi_code | 24 | 0 | 24 | 0 | `blood` |
| `most recent total cholesterol` | lab_multi_code | 12 | 0 | 12 | 0 | — |
| `most recent body weight reading` | vital | 10 | 0 | 10 | 0 | — |
| `most recent heart rate reading` | vital | 10 | 1 | 9 | 0 | — |
| `most recent ldl cholesterol` | lab_single_code | 3 | 0 | 3 | 0 | — |
| `has he been diagnosed with myocardial infarction` | condition_present | 3 | 1 | 2 | 0 | `diagnosed` |
| `most recent hemoglobin` | lab_single_code | 2 | 0 | 2 | 0 | — |
| `most recent PLT` | abbreviation | 2 | 0 | 2 | 0 | — |
| `is she allergic to peanuts` | allergy_positive | 1 | 1 | 1 | 0 | `peanuts` |
| `any bee sting allergy documented` | allergy_positive | 1 | 1 | 1 | 0 | `bee sting` |
| `is she allergic to eggs` | allergy_positive | 1 | 1 | 1 | 0 | `eggs` |
| `any history of hypertension` | condition_present | 1 | 0 | 1 | 0 | `hypertension` |
| `no known drug allergies` | negation | 2 | 1 | 1 | 0 | — |
| `recent blood glucose` | lab_date_window | 7 | 19 | 0 | 12 | `blood` |
| `blood glucose in the past 6 months` | lab_date_window | 4 | 12 | 0 | 8 | `blood` |
| `blood glucose over the last 3 months` | lab_date_window | 2 | 4 | 0 | 2 | `blood` |

## Abstention cases that returned something

- none: every empty-gold query correctly returned nothing

## Latency

Parse plus retrieve, warm cache. Excludes brief rendering.

- median 1.7 ms · p95 17.5 ms · max 123.3 ms
