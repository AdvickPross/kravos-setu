# Demo patients and rehearsed queries

Four patients (the prompt asks for 3-5), each picked from the real corpus
for a specific thing it demonstrates, not because they're "interesting" in
general. Every fact quoted below came from actually running
`retrieve()` + `build_brief()` against the split corpus (`facts_by_patient/`)
on 2026-08-20 — nothing here is guessed. IDs are real `patient_id` values
from `patients.jsonl`; look a patient up with `GET /api/patients/{id}` or
type their name into the picker.

## Two findings from curating this list

Both matter more than the patients themselves — write them down before the
demo so a question about either doesn't catch you flat.

1. **`status` never contains `stopped`, `discontinued`, or
   `entered-in-error` anywhere in the 1,083,231-fact corpus.** Measured
   directly (`GROUP BY status` equivalent over every `facts_by_patient/*.jsonl`
   file): the only values that occur are `final`, `active`, `resolved`,
   `completed` (medications specifically only ever use `active` or
   `completed`). The prompt asked this list to include "a stopped
   medication" for status variation — that fact doesn't exist in this data,
   so patient #3 below demonstrates the status variation that *does* exist
   (`active` vs `completed` dosing history for the same drug across years)
   instead. Don't claim a "stopped medication" demo exists; it doesn't,
   and claiming otherwise would be exactly the kind of overconfident
   misstatement this product is supposed to avoid making about patient data.

2. **The word "hypertension" alone resolves `UNRESOLVED`; "high blood
   pressure" resolves correctly** to the same SNOMED code (`59621000`,
   `synonym` quality). Checked directly against `QueryNER` outside of any
   patient context, so it's a lexicon/surface-form gap in
   `data/synonyms.csv`, not something patient-specific. `retrieval/` is out
   of scope for this pass, so this isn't being fixed here — but it means
   query phrasing for the demo (and for real use) needs "high blood
   pressure," not "hypertension," until that surface form is added.
   Diabetes has no such gap ("diabetes" itself resolves fine).

## 1 — Allergy check with a real positive, plus overlapping entities

**Patient:** Hyo54 Coral377 Krajcik437 (`0e207639-9926-7286-10cb-304b27c5dc10`) — female, born 1950-07-01, **died 1982-12-21 aged 32**, 751 facts.

The brief headline reads `32y`, not `76y`: `PatientSummary.age_years` correctly
reports age *at death* for a deceased patient. An earlier draft of this file
said "76-year-old", which is what she would be today had she lived — don't
repeat that live, and expect her record to end in 1982.

**Query:** `is she allergic to aspirin, and is she on albuterol or budesonide`

**Demonstrates:**
- A genuine on-file allergy (aspirin, code `1191`) rather than another
  "no records" demo — most patients in this corpus have no allergy facts at
  all (only 174 of 952 do), so this one was deliberately hand-picked to
  show the positive case.
- **Overlapping entities at one span**, per the corpus's established
  behaviour: "aspirin" resolves to *two* entities at identical
  offsets — ALLERGY (code `1191`) and MEDICATION (RxNorm aspirin
  products) — and the brief correctly shows the allergy as `FOUND` while
  the medication line shows nothing for aspirin specifically (she isn't on
  it), without the two getting confused into one answer.
- Real `active`/`completed` status mix on her asthma inhalers (budesonide,
  albuterol) — 36 medication lines total, most recent from 1982 in this
  patient's Synthea timeline, oldest from 1981.

**Expect:** Allergies section with 2 lines (the allergic-disposition finding
and the aspirin allergy itself, both `active`); Medications section with
~36 lines, most-recent-first; 4 missingness items (renal function,
electrolytes, CBC, cardiac history — allergy documentation is *not* missing
here, correctly, since she has allergy facts on file).

## 2 — Genuine missingness gap

**Patient:** Abby752 Elenora790 Braun514 (`bc2bb0d1-a0e6-c9f0-f250-32ff7934b2dd`) — 12-year-old female, 258 facts.

**Query:** `is he on aspirin, any allergies, and what was his last glucose`

**Demonstrates:** the product's actual differentiator. Every entity
resolves cleanly (aspirin x2, allergies, glucose — no UNRESOLVED spans),
but this patient — a healthy child with no chronic disease workup — simply
has none of the facts asked about. All three brief sections render their
explicit empty note (`"No allergy records on file."` etc.) instead of
silently vanishing, and the missingness box lists 5 of 6 checklist items —
**4 `absent` plus CBC `stale`**, not 5 `absent` as an earlier draft of this
file claimed. She has 8 CBC facts, most recent 2025-04-12, outside the
90-day window; that is a different missingness reason from the other four,
and it happens to make this patient demonstrate both reasons at once. This
is the "not present" vs. "not retrieved" distinction from the spec, visible
in one screen: nothing failed, there's just nothing there.

**Expect:** headline `"...12y female — 0 facts retrieved; 5 item(s) not on
file."`; all three sections (Allergies/Medications/Labs) empty with their
notes; 5 missingness items — renal function, electrolytes, allergy
documentation and cardiac history `absent`, CBC `stale`. Current-medications is *not* flagged: she
has one MEDICATION fact on file (a pediatric fluoride gel), which is enough
to satisfy that checklist item's category-only presence check even though
it's clinically unrelated to what the query asked about — worth pointing
out live, since it's a real, honest edge in how coarse a category-only
missingness check can be.

## 3 — Medication status variation (the real kind, not "stopped")

**Patient:** Abdul218 Bennie663 Schroeder447 (`bdf5f21e-bea8-7970-286f-26a346c1dea4`) — 32-year-old male, 549 facts.

**Query:** `is he on ibuprofen or lisinopril`

**Demonstrates:** the same drug prescribed repeatedly across years, cycling
`active` (most recent, 2026-04-23) then `completed` for every prior fill
going back to 2016 — 16 lines total, most-recent-first, status and date
inline on every line (`"Ibuprofen 400 MG Oral Tablet [Ibu] — active
2026-04-23"`, then `"...— completed 2025-04-17"`, etc.). This is the
closest real analogue in this corpus to "was he on X and is he still" —
see finding #1 above for why it isn't literally a stopped-drug demo.

**Expect:** Allergies section empty (`"No allergy records on file."`); 16
medication lines; CBC flagged `stale` in missingness (`"last seen
2025-04-17 (expected within 90 days)"`) rather than `absent` — he has CBC
facts, they're just outside the recency window, a different missingness
reason than patient #2's.

## 4 — Comorbid conditions + a clean multi-reading lab trend

**Patient:** Lynwood354 Al123 Feest103 (`dea898ed-b6cd-5dba-b108-42f48c62fffe`) — deceased male (born 1916, died at 62), 1,317 facts.

**Query:** `does he have diabetes or high blood pressure, and what has his glucose been`

**Demonstrates:**
- Two real comorbid CONDITION facts (`Diabetes mellitus type 2`,
  `Essential hypertension`, both `active`) — see finding #2 above for why
  the query says "high blood pressure," not "hypertension."
- A **verified-clean lab trend**: "glucose" resolves to 4 LOINC codes
  corpus-wide (blood, serum/plasma, two urine-strip variants), which is
  exactly the kind of multi-code entity that can silently mix unrelated
  tests into one section. Checked this patient's 12 glucose facts
  individually before picking him — all 12 are the same code (`2339-0`,
  Glucose in Blood), so the Labs section reads as one coherent trend, not a
  mix of blood draws and urine dipsticks. This is what "verify no
  multi-code collisions" (from the prompt) means in practice, and this
  patient is the one case in this set where it was checked because a
  4-code entity is exactly where it could go wrong.
- Also on record: metformin, lisinopril, low-dose aspirin, nitroglycerin —
  a cardiac-relevant medication list, though this query doesn't ask about
  them (a natural follow-up query at demo time if there's time for one).

**Expect:** Conditions section with 2 lines; Labs section with 12 lines,
most-recent-first (his data runs into the 1970s in Synthea's synthetic
timeline — dates will look historical, that's the source data, not a bug);
4 missingness items (allergy documentation is one of them — he has no
allergy facts on file, unlike patient #1).

---

## Running the rehearsal

```pwsh
docker compose ps          # only relevant once Path B/Postgres exists -- not yet
python -m uvicorn backend.main:app --port 8000
# separate terminal
cd frontend
npm run dev
```

Open `http://localhost:3000`, search the picker by surname (e.g.
"Krajcik437", "Schroeder447", "Feest103" — "Braun514" for patient #2), run
the query above, and confirm against the "Expect" line. All four were last
verified end-to-end (including in a real headless-Chromium browser pass,
not just the API) on 2026-08-20 — see `kravos_hackathon_progress.md` for
that session's notes if something here doesn't match a later run.

Everything above runs with the LLM polish flag off, because that flag
doesn't exist yet (step 9, not started). There is nothing to toggle.
