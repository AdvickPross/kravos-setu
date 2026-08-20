# Claude Code Prompt — Kravos Setu Query-Side NER

Paste everything below into Claude Code from the repo root.

---

## Context

I'm building **Kravos Setu**, an AI clinical-context layer over Indian health records (ABDM/NDHM ecosystem, FHIR R4). The product answers a doctor's natural-language question about a patient by retrieving from that patient's records, and — critically — also flags what *should* be known but is missing from the record. Current wedge: pre-operative assessment.

The system is **cloud/server-shaped, running locally on a Windows laptop for now**. There is no edge deployment, no Jetson, no quantized or distilled models. Ignore any such notion.

### What already exists

An ingestion pipeline that reads Synthea-generated FHIR R4 bundles and emits flat "facts":

- `extract.py` — the extractor (~14KB), handles one bundle
- `batch.py` — runs the extractor over a directory of bundles
- `facts.jsonl` — **701 MB, 1,083,231 fact records** from 952 synthetic patients
- `patients.jsonl` — patient index
- `lexicon.csv` — already generated: the 1,170 distinct `(category, code_system, code, display)` tuples present across the whole corpus
- `synthea-with-dependencies.jar` — regeneration path
- `definitions.json/` — NDHM/NRCES FHIR implementation-guide CodeSystems. **Insurance/claims only. Irrelevant to this task — do not read or use.**

### Fact record shape (one line of facts.jsonl)

```json
{
  "fact_id": "d9780cfcf933f208",
  "patient_id": "bc2bb0d1-...",
  "category": "CONDITION",
  "safety_class": "CLINICAL",
  "text": "Condition: Medication review due (situation) [resolved] (resolved 2016-09-03)",
  "code_system": "http://snomed.info/sct",
  "code": "314529007",
  "display": "Medication review due (situation)",
  "value": null,
  "unit": null,
  "date": "2016-03-05T00:34:26+05:30",
  "status": "resolved",
  "source_resource_type": "Condition",
  "source_resource_id": "bc2bb0d1-...",
  "encounter_id": "bc2bb0d1-...",
  "also_in": []
}
```

### Corpus composition (measured, not estimated)

Code systems across 1,083,231 facts:

| Code system | Facts |
|---|---|
| `http://loinc.org` | 798,321 |
| `http://snomed.info/sct` | 200,981 |
| `http://www.nlm.nih.gov/research/umls/rxnorm` | 70,197 |
| `http://hl7.org/fhir/sid/cvx` | 13,732 |

Categories:

| Category | Facts | Distinct codes |
|---|---|---|
| LAB | 600,024 | 253 |
| PROCEDURE | 163,236 | 333 |
| VITAL | 141,593 | 24 |
| MEDICATION | 70,114 | 253 |
| REPORT | 57,987 | 1 |
| CONDITION | 35,665 | 260 |
| IMMUNIZATION | 13,732 | 24 |
| ALLERGY | 880 | 22 |

Category → code system is clean: LAB/VITAL → LOINC, CONDITION/PROCEDURE → SNOMED, MEDICATION → RxNorm, IMMUNIZATION → CVX. REPORT is a single LOINC document code (34117-2) whose `text` field holds the full free-text clinical note.

---

## What I want you to build

**Query-side NER only.** This parses *the doctor's question*, not patient records. It never reads `facts.jsonl` at runtime — it only consumes `lexicon.csv`.

Input: `"any drug allergies before I prescribe amoxicillin"`
Output: a `ParsedQuery` with resolved entities, each carrying code(s), category, match quality, and a negation flag.

This is **rules and dictionary lookup. No model training, no ML libraries, no embeddings.** The corpus vocabulary is only 1,170 codes — it fits in a dict.

### Target structure

```
retrieval/
  __init__.py
  models.py        # Pydantic types
  normalize.py     # per-code-system display normalization
  lexicon.py       # loads lexicon.csv + synonyms.csv, builds surface-form index
  ner.py           # the resolver
data/
  synonyms.csv     # hand-authored, concept-shaped
tests/
  test_normalize.py
  test_ner.py
  queries.md       # 30 hand-written probe queries
```

Leave `extract.py`, `batch.py`, and the `.jsonl` files where they are — don't reorganize existing code.

### 1. `models.py`

```python
class MatchQuality(str, Enum):
    EXACT = "exact"
    SYNONYM = "synonym"
    FUZZY = "fuzzy"
    UNRESOLVED = "unresolved"

class ResolvedEntity(BaseModel):
    span: str                     # text as it appeared in the query
    start: int
    end: int
    category: str | None          # LAB / CONDITION / MEDICATION / ...
    code_system: str | None
    codes: list[str]              # MUST be a list — see below
    display: str | None
    quality: MatchQuality
    negated: bool = False

class ParsedQuery(BaseModel):
    raw: str
    entities: list[ResolvedEntity]
    date_from: date | None = None
    date_to: date | None = None
```

**`codes` is a list, not a string.** This is measured, not defensive: 23 lab analytes in this corpus map to multiple LOINC codes once normalized. `glucose` → 4 codes (blood, serum/plasma, urine strip, etc.), `bilirubin.total` → 4, `creatinine` → 2, `potassium` → 2, `sodium` → 2, `hemoglobin` → 2. A bare "glucose" query must return the whole set so SQL can filter `code IN (...)`.

It also future-proofs a decision I've already made (see "Vocabulary strategy" below).

### 2. `normalize.py`

One normalization function per code system. Keep them separate and independently testable — when real ABDM data arrives with different vocabulary conventions, this is the only file that should need changing.

**LOINC (LAB, VITAL).** Displays follow `Analyte [Property] in Specimen by Method`. Strip from the first `[` onward, then strip a trailing ` in <specimen>` clause. Bracket-strip must run first — `Cholesterol in HDL [Mass/volume] in Serum or Plasma` has an `in` before the bracket that must survive, so only the *trailing* specimen phrase is removed.

Examples:
- `Creatinine [Mass/volume] in Serum or Plasma` → `creatinine`
- `Cholesterol in HDL [Mass/volume] in Serum or Plasma` → `cholesterol in hdl`
- `Body Height` → `body height`

**Guard against meaningless residues.** Some LOINC entries are questionnaire scores whose stripped form is generic and unsearchable — `total score` currently collapses 4 distinct codes. Maintain a stoplist (`total score`, and anything under ~4 chars) of forms that must NOT be indexed as surface forms. The full display should still be indexed for these.

**RxNorm (MEDICATION).** Displays carry dose form, strength, packaging and brand. Extract:
- **Ingredients** — drop leading quantity/duration prefixes (`1 ML`, `24 HR`, `168 HR`, `21 DAY`, `Abuse-Deterrent`), split combination products on `/`, cut each fragment at the first digit, strip salt/ester suffixes (`hydrochloride`, `sodium`, `acetate`, `maleate`, `hyclate`, `monohydrate`, `bromide`, `succinate`, `fumarate`, `hydrobromide`, `porcine`, `human`)
- **Brand** — the bracketed trailing name, e.g. `[Percocet]`, `[Entresto]`, `[Oxycontin]`, `[Humulin]`, `[Macrobid]`

`Acetaminophen 325 MG / Oxycodone Hydrochloride 5 MG Oral Tablet [Percocet]` should index under `acetaminophen`, `oxycodone`, `percocet`, and the full display. A query for "oxycodone" then hits every product containing it — this is the RxNorm ingredient-rollup behaviour, achieved without downloading RxNorm.

Synthea has inconsistent casing (`medroxyPROGESTERone` vs `medroxyprogesterone`, `Oxycodone` vs `oxyCODONE`). Lowercasing handles it, but log any case where two entries normalize identically but have different codes — I want to know if that's a genuine distinction or an extractor duplicate.

**SNOMED (CONDITION, PROCEDURE).** Strip the trailing parenthetical semantic tag: `(disorder)`, `(situation)`, `(finding)`, `(procedure)`, `(regime/therapy)`, `(morphologic abnormality)`, `(qualifier value)`.

**CVX (IMMUNIZATION).** Lowercase and trim; no structured pattern to strip.

### 3. `lexicon.py`

Load `lexicon.csv` and `data/synonyms.csv` at startup, build an in-memory index: **surface form → list of `(category, code_system, code, display)`**.

Index every form the normalizer produces per row: full display (lowercased), normalized form, and for medications each ingredient plus the brand. Multiple codes sharing a surface form is normal and expected — the index value is always a list.

Provide a `reload()` path or make lexicon file paths a constructor argument. The lexicon is regenerated whenever the corpus changes, so nothing should hardcode it.

For reference, the lexicon was generated by:

```python
import json, csv
s = set()
for line in open('facts.jsonl', encoding='utf-8'):
    d = json.loads(line)
    s.add((d.get('category'), d.get('code_system'), d.get('code'), d.get('display')))
w = csv.writer(open('lexicon.csv', 'w', newline='', encoding='utf-8'))
w.writerow(['category', 'code_system', 'code', 'display'])
w.writerows(sorted(s))
```

Add this as a small script (`scripts/build_lexicon.py`) so it's repeatable.

### 4. `data/synonyms.csv`

Hand-authored abbreviation → concept mapping. **Concept-shaped, not code-shaped**: columns `concept, surface_forms, category, code_system, codes` where `surface_forms` and `codes` are pipe-delimited. One row per clinical concept, possibly many codes.

This shape matters because real ABDM data will introduce codes for the same concept from different systems (Indian brand names with no RxNorm code, SNOMED CT India concepts). A concept row can then accumulate codes across systems without restructuring.

Seed it with roughly these — **verify every code against `lexicon.csv` before writing it in; do not trust codes from memory, mine them from the file**:

labs: hba1c/a1c, k/k+/potassium, na/sodium, creat/creatinine, bun/urea, hb/hgb/haemoglobin, hct/haematocrit, wbc/leukocytes/white count, plt/platelets, ldl, hdl, tg/triglycerides, crp, alt/sgpt, ast/sgot, ef/ejection fraction

vitals: bp/blood pressure (systolic 8480-6, diastolic 8462-4, mean 8478-0), spo2/sats/o2 sat (2708-6), hr/pulse (8867-4), rr (9279-1), temp (8310-5), bmi (39156-5)

conditions: htn/high bp, t2dm/dm/diabetes/sugar, ckd, mi/heart attack, sob/breathlessness/dyspnea

Where a synonym legitimately spans several codes (bp → three LOINC codes), put them all in the `codes` column.

**Note:** the VITAL category is a rough extractor bucket, not a clean taxonomy — it contains NT-proBNP, KCCQ-12 questionnaire scores, pain-severity ratings, and a `Medication management note`. Don't write logic that assumes VITAL means "vital sign."

### 5. `ner.py`

The resolver. Given a query string:

1. Normalize: lowercase, collapse whitespace, strip punctuation (keep `+` for `k+`, keep `/` for `fev1/fvc`)
2. Match **longest span first** against the index, so `blood pressure` wins over `pressure` and `cholesterol in hdl` wins over `cholesterol`
3. Set `quality`: `EXACT` if the span matched a display or normalized display verbatim; `SYNONYM` if via `synonyms.csv`; `FUZZY` if via edit-distance/token-overlap fallback; `UNRESOLVED` for spans that look clinical but matched nothing
4. **Negation window**: scan a few tokens before each matched entity for `no`, `not`, `never`, `denies`, `without`, `ruled out`, `negative for`. Set `negated=True`. This is essential — the extractor already distinguishes `ALLERGY_NEGATED` on the fact side, and "no known drug allergies" must not resolve identically to "allergic to penicillin"
5. **Date window**: regex for `last N months/years`, `since YYYY`, `past year`, `recent`, `before surgery` → populate `date_from`/`date_to`

Fuzzy matching should be conservative and last-resort — prefer returning `UNRESOLVED` over a confident wrong code. The downstream router branches on match quality, so an honest `UNRESOLVED` is more useful than a plausible-looking mistake.

### 6. `tests/queries.md` + tests

Write 30 probe queries spanning: medication reconciliation, lab trends, allergy checks, symptom recall, full history briefs. Include abbreviation-heavy ones ("HTN and T2DM meds?"), negated ones ("any patient with no known allergies"), multi-code ones ("glucose over the last year"), and deliberately unresolvable ones.

For each, record what resolved and what didn't. **This is a debugging instrument, not an evaluation set** — the real gold eval set (80–100 queries with gold fact IDs) comes later and is a separate artifact. Don't conflate them or report accuracy numbers from these 30.

Unit tests for `normalize.py` should assert the exact strip behaviours above, including the `Cholesterol in HDL` case and the `total score` stoplist.

---

## Vocabulary strategy — decided, don't revisit

Facts are stored with **whatever code system the source sent, unmodified** (Option B). We do not normalize incoming codes to a single canonical vocabulary at ingestion. Reasons: the fact schema already carries `code_system` per row so it's built for heterogeneity; discarding a hospital's original code is an irreversible provenance loss; and an Indian-brand → RxNorm crosswalk would be a whole project with no clean target for drugs absent from RxNorm.

Consequence: the lexicon is regenerated from the corpus whenever the corpus changes, and resolution returns code *sets* spanning systems. A concept layer (one concept, many codes across systems) is the eventual home for this — `synonyms.csv` is already shaped for it.

---

## Constraints

- Python, Pydantic v2, standard library where possible. **No ML libraries, no embeddings, no model downloads.**
- **Windows / PowerShell.** Any commands you give me must be pwsh, not bash.
- Never load `facts.jsonl` into memory — 701 MB. Stream line by line if you must touch it at all (you shouldn't need to for this task).
- Terminology lives in data files, never hardcoded in logic.
- Don't build: intent classification, the routing engine, SQL retrieval, vector retrieval, embeddings, reranking, or briefing generation. Those come after NER and are explicitly out of scope.

## Order of work

Build `models.py` → `normalize.py` (+ tests) → `scripts/build_lexicon.py` → `lexicon.py` → `data/synonyms.csv` → `ner.py` → probe queries. Show me `models.py` and `normalize.py` before proceeding to the lexicon index, so I can check the entity shape and strip rules before anything is built on top of them.
