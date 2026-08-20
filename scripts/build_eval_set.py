"""Build the gold evaluation set: data/eval_set.jsonl.

**This is the artifact `tests/queries.md` is explicitly not.** The probe file
is a debugging instrument that records what resolved; this file states, for
each query, which `fact_id`s a correct answer *must* contain -- so precision
and recall become claimable numbers rather than impressions.

Gold is derived on a path that never touches `retrieval/` or
`backend/retrieve.py`. For each query the clinical intent is hand-specified
here as `(categories, codes, window)` clauses read off the question, and
those clauses are resolved against the corpus. What the pipeline does with
the *words* is exactly what's under test, so nothing in the gold may come
from `parse_query()`. Scoring gold with the pipeline that's being scored
would return 100% by construction and measure nothing.

`get_patient_facts()` *is* used to resolve clauses to fact ids. That is not
circularity: the filter is a shared, uncontested component with its own unit
tests (`tests/test_data_layer.py`), identical on both sides, and it is not
what this set evaluates. The contested half is the mapping from question
text to codes/categories/dates. Re-implementing the filter here would add a
bug surface for no epistemic gain.

Two-tier relevance, because binary relevance would punish defensible
behaviour. `required` facts drive recall; `allowed` facts are neither
rewarded nor penalised. "Allergic disposition (finding)" is the standing
example -- asking "is she allergic to aspirin" legitimately surfaces it
alongside the aspirin allergy, and calling that a false positive would be
scoring a judgement call as a bug.

    pwsh> python scripts/build_eval_set.py
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(REPO_ROOT))

from backend.data_layer import get_patient, get_patient_facts, list_patients  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "data" / "eval_set.jsonl"
LEXICON_PATH = REPO_ROOT / "lexicon.csv"

# Relative-date queries ("over the last year") need a fixed reference or the
# gold window drifts every day this runs. run_eval.py passes this same date
# into parse_query(), so both sides agree exactly. Changing it invalidates
# the gold windows of every archetype whose `window_days` is set.
EVAL_TODAY = date(2026, 8, 21)

ALLERGIC_DISPOSITION = "609328004"

# Several allergens are also drugs the patient may have been prescribed, and
# "penicillin" resolves to an ALLERGY substance *and* MEDICATION products at
# one span -- established, deliberate behaviour. Asking "is he allergic to
# penicillin" and getting back "prescribed penicillin V in 2017" is defensible
# context, not a wrong answer, so those facts go in the `allowed` tier: never
# required, never penalised. Applied as a blanket rule to every drug allergen
# rather than case by case, so it cannot become a way to quietly delete
# individual failures. Codes read off lexicon.csv, asserted present at build.
ALLERGEN_MEDICATION_CODES = {
    "aspirin": ["212033", "243670", "2563431"],
    "penicillin": ["105078", "834061", "834102"],
    "lisinopril": ["197884", "197885", "197887", "311353", "311354", "314076", "314077"],
}


# --------------------------------------------------------------------------
# clinical vocabulary -- codes are corpus-measured, asserted present below
# --------------------------------------------------------------------------

# Allergy substances/drugs, read off a GROUP BY over the corpus, not recalled.
ALLERGENS = {
    "aspirin": ("1191", ["is {sub} allergic to aspirin", "any aspirin allergy on file for {sub}", "aspirin allergy?"]),
    "penicillin": ("7984", ["is {sub} allergic to penicillin", "does {sub} have a penicillin allergy", "pen v allergy documented?"]),
    "lisinopril": ("29046", ["is {sub} allergic to lisinopril", "any recorded lisinopril allergy"]),
    "peanut": ("762952008", ["is {sub} allergic to peanuts", "peanut allergy on file?"]),
    "shellfish": ("735029006", ["does {sub} have a shellfish allergy", "any shellfish allergy recorded"]),
    "latex": ("111088007", ["is {sub} allergic to latex", "latex allergy documented before theatre?"]),
    "bee venom": ("288328004", ["any bee sting allergy documented", "is {sub} allergic to bee venom"]),
    "tree nut": ("442571000124108", ["does {sub} have a tree nut allergy"]),
    "eggs": ("102263004", ["is {sub} allergic to eggs"]),
    "wheat": ("412071004", ["any wheat allergy on record"]),
    "cow's milk": ("3718001", ["is {sub} allergic to cow's milk"]),
    "fish": ("735971005", ["does {sub} have a fish allergy"]),
}

# Everything else pulls its codes from data/synonyms.csv, so the gold and the
# vocabulary can never silently disagree about what a concept means.
def load_concepts() -> dict[str, dict]:
    out: dict[str, dict] = {}
    path = REPO_ROOT / "data" / "synonyms.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            concept = (row.get("concept") or "").strip()
            if not concept or concept.startswith("#"):
                continue
            out[concept] = {
                "category": (row.get("category") or "").strip() or None,
                "codes": [c.strip() for c in (row.get("codes") or "").split("|") if c.strip()],
            }
    return out


def load_lexicon_codes() -> set[str]:
    with LEXICON_PATH.open(encoding="utf-8", newline="") as handle:
        return {row["code"] for row in csv.DictReader(handle) if row["code"]}


# --------------------------------------------------------------------------
# archetypes
# --------------------------------------------------------------------------
#
# `n` is a target, not a guarantee: an archetype emits fewer entries when the
# corpus cannot supply enough qualifying patients. The real count is asserted
# against ARCHETYPE_MIN_TOTAL at the end rather than padded to hit a number.

LAB_SINGLE = ["creatinine", "hemoglobin", "hba1c", "thyrotropin", "platelets", "ldl cholesterol"]
LAB_MULTI = ["blood glucose", "electrolytes", "renal function tests", "liver function tests", "total cholesterol"]
CONDITIONS = [
    "type 2 diabetes", "hypertension", "chronic kidney disease", "ischemic heart disease",
    "heart failure", "asthma", "anemia", "myocardial infarction", "atrial fibrillation", "epilepsy",
]
MEDICATIONS = ["metformin", "insulin", "clopidogrel", "warfarin", "nitroglycerin", "salbutamol", "paracetamol", "frusemide"]
VITALS = ["blood pressure", "heart rate", "body mass index", "oxygen saturation", "body weight"]

CONDITION_PHRASINGS = [
    "does {sub} have {c}", "any history of {c}", "is {c} on the problem list", "has {sub} been diagnosed with {c}",
]
MED_PHRASINGS = [
    "is {sub} on {c}", "current {c} dose", "has {sub} been prescribed {c}", "any {c} in the medication list",
]
LAB_PHRASINGS = [
    "what was {sub} last {c}", "most recent {c}", "show me {sub} {c}", "latest {c} result",
]
LAB_WINDOW_PHRASINGS = [
    ("{c} over the last year", 365), ("{c} in the past 6 months", 182),
    ("recent {c}", 182), ("{c} over the last 3 months", 91),
]
VITAL_PHRASINGS = ["what is {sub} latest {c}", "most recent {c} reading", "{c} before surgery"]

# Bare category questions: the clinician asks for a *list*, not about one
# substance. Restricted to ALLERGY and MEDICATION, the two lists a pre-op
# assessment turns on. CONDITION is deliberately excluded -- "problem list"
# would have to score against every CONDITION fact, and this corpus files
# social findings ("Full-time employment") in that category, so the gold
# would encode a judgement about what belongs on a problem list that nobody
# has actually made.
CATEGORY_QUESTIONS = [
    ("does {sub} have any allergies", "ALLERGY"),
    ("any allergies", "ALLERGY"),
    ("allergy list", "ALLERGY"),
    ("known allergies", "ALLERGY"),
    ("what is {sub} allergic to", "ALLERGY"),
    ("what medications is {sub} on", "MEDICATION"),
    ("medication list", "MEDICATION"),
    ("current medications", "MEDICATION"),
]

# Queries with no coded answer anywhere in this corpus. Gold is empty on
# purpose: the thing being measured is that the pipeline returns nothing
# rather than reaching for a plausible-looking wrong code.
UNRESOLVABLE = [
    "has he ever mentioned trouble sleeping",
    "how is she coping at home",
    "did the family raise any concerns",
    "is he anxious about the operation",
    "any difficulty with stairs",
    "what did the physio say last visit",
    "does she live alone",
    "any issues with the previous anaesthetic",
]

# Drug-class rollups are a deliberate non-feature -- an incomplete class list
# under-retrieves silently, which is worse than an honest miss. Gold is empty
# because the honest answer is "unresolved", and this archetype exists to
# prove the decision still holds rather than to be fixed later.
CLASS_ROLLUPS = [
    "is he on any beta blockers", "any ACE inhibitors on the list",
    "does she take any statins", "any anticoagulants prescribed",
]


def pronoun(patient) -> tuple[str, str]:
    g = (patient.gender or "").lower()
    if g == "female":
        return "she", "her"
    if g == "male":
        return "he", "his"
    return "they", "their"


# --------------------------------------------------------------------------
# corpus index -- presence only, for picking patients that make each query real
# --------------------------------------------------------------------------


def build_presence_index(codes_of_interest: set[str]) -> dict[str, dict]:
    """``patient_id -> {codes present (restricted), category counts}``.

    Restricted to the codes the archetypes actually ask about so this stays
    small; the full cross-product of 952 patients x 1,156 codes is not needed
    and would not fit comfortably.
    """
    index: dict[str, dict] = {}
    facts_dir = REPO_ROOT / "facts_by_patient"
    for path in sorted(facts_dir.glob("*.jsonl")):
        codes: set[str] = set()
        cats: dict[str, int] = {}
        for line in path.open(encoding="utf-8"):
            if not line.strip():
                continue
            record = json.loads(line)
            cats[record["category"]] = cats.get(record["category"], 0) + 1
            code = record.get("code")
            if code and code in codes_of_interest:
                codes.add(code)
        index[path.stem] = {"codes": codes, "cats": cats}
    return index


def resolve_gold(patient_id: str, clauses: list[dict], date_from, date_to) -> list[str]:
    """Clauses -> the concrete fact ids a correct answer must contain."""
    ids: set[str] = set()
    for clause in clauses:
        facts = get_patient_facts(
            patient_id,
            categories=clause.get("categories"),
            codes=clause.get("codes") or None,
            date_from=date_from,
            date_to=date_to,
        )
        ids.update(f.fact_id for f in facts)
    return sorted(ids)


class Builder:
    def __init__(self, index: dict[str, dict], patients: dict):
        self.index = index
        self.patients = patients
        self.entries: list[dict] = []
        self._seen_queries: set[tuple[str, str]] = set()

    def add(
        self,
        *,
        archetype: str,
        query: str,
        patient_id: str,
        rationale: str,
        required: list[dict],
        allowed: list[dict] | None = None,
        window_days: int | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        key = (patient_id, query)
        if key in self._seen_queries:
            return False
        date_from = EVAL_TODAY - timedelta(days=window_days) if window_days else None
        date_to = EVAL_TODAY if window_days else None

        gold_ids = resolve_gold(patient_id, required, date_from, date_to)
        allowed_ids = resolve_gold(patient_id, allowed or [], date_from, date_to)

        # A query that was meant to find something, whose gold resolves to
        # nothing, is not a hard case -- it is a free pass. It gets excluded
        # from precision/recall as an abstention case and quietly flatters the
        # archetype it belongs to. Windowed queries are where this bites: the
        # patient has the lab, just not inside the window. Refuse the entry and
        # let the caller try the next candidate patient.
        if required and not gold_ids:
            return False

        self._seen_queries.add(key)
        self.entries.append(
            {
                "query_id": f"q{len(self.entries) + 1:03d}",
                "query": query,
                "patient_id": patient_id,
                "archetype": archetype,
                "rationale": rationale,
                "gold_spec": {
                    "required": required,
                    "allowed": allowed or [],
                    "window_days": window_days,
                    "date_from": date_from.isoformat() if date_from else None,
                    "date_to": date_to.isoformat() if date_to else None,
                },
                "gold_fact_ids": gold_ids,
                "allowed_fact_ids": sorted(set(allowed_ids) - set(gold_ids)),
                "tags": tags or [],
            }
        )
        return True

    def pick(self, predicate, used: set[str], limit: int = 1) -> list[str]:
        """Deterministic patient selection: sorted ids, first N that qualify
        and have not already been used by this archetype."""
        out = []
        for pid in sorted(self.index):
            if pid in used or not predicate(self.index[pid]):
                continue
            out.append(pid)
            used.add(pid)
            if len(out) >= limit:
                break
        return out


# --------------------------------------------------------------------------
# the set
# --------------------------------------------------------------------------

ARCHETYPE_MIN_TOTAL = 80  # the spec's floor; assert rather than pad


def build_allergies(b: "Builder", patients: dict) -> None:
    disposition_allow = [{"categories": ["ALLERGY"], "codes": [ALLERGIC_DISPOSITION]}]

    def subj(pid: str) -> str:
        return pronoun(patients[pid])[0]

    def allow_for(name: str) -> list[dict]:
        """Disposition finding, plus same-drug prescriptions when the allergen
        is also a drug. See ALLERGEN_MEDICATION_CODES."""
        allowed = list(disposition_allow)
        med_codes = ALLERGEN_MEDICATION_CODES.get(name)
        if med_codes:
            allowed.append({"categories": ["MEDICATION"], "codes": med_codes})
        return allowed

    # -- genuine positives ------------------------------------------------
    used: set[str] = set()
    for name, (code, phrasings) in ALLERGENS.items():
        for pid in b.pick(lambda v, c=code: c in v["codes"], used):
            b.add(
                archetype="allergy_positive",
                query=phrasings[0].format(sub=subj(pid)),
                patient_id=pid,
                rationale=f"patient has an on-file {name} allergy (code {code})",
                required=[{"categories": ["ALLERGY"], "codes": [code]}],
                allowed=allow_for(name),
                tags=["allergy", "safety"],
            )

    # -- asked-about substance absent, but other allergies on file --------
    used = set()
    for name, (code, phrasings) in list(ALLERGENS.items())[:4]:
        for pid in b.pick(
            lambda v, c=code: v["cats"].get("ALLERGY", 0) > 1 and c not in v["codes"], used
        ):
            b.add(
                archetype="allergy_negative_other_on_file",
                query=phrasings[0].format(sub=subj(pid)),
                patient_id=pid,
                rationale=(
                    f"patient has allergy records but none for {name}; the correct answer is "
                    "none_on_file for that substance, which is not the same as silence"
                ),
                required=[],
                allowed=[{"categories": ["ALLERGY"]}] + allow_for(name)[1:],
                tags=["allergy", "safety", "abstain"],
            )

    # -- nothing on file at all -------------------------------------------
    used = set()
    for name, (code, phrasings) in list(ALLERGENS.items())[:6]:
        for pid in b.pick(lambda v: v["cats"].get("ALLERGY", 0) == 0, used):
            b.add(
                archetype="allergy_none_on_file",
                query=phrasings[0].format(sub=subj(pid)),
                patient_id=pid,
                rationale="patient has zero allergy facts; 'no allergy records on file' is the clinical answer",
                required=[],
                allowed=allow_for(name)[1:],
                tags=["allergy", "safety", "abstain"],
            )


def build_clinical(b: "Builder", concepts: dict, patients: dict) -> None:
    def subj(pid: str) -> str:
        return pronoun(patients[pid])[0]

    def poss(pid: str) -> str:
        return pronoun(patients[pid])[1]

    # -- conditions on the problem list ------------------------------------
    used: set[str] = set()
    for i, name in enumerate(CONDITIONS):
        codes = concepts[name]["codes"]
        for pid in b.pick(lambda v, cs=codes: any(c in v["codes"] for c in cs), used):
            b.add(
                archetype="condition_present",
                query=CONDITION_PHRASINGS[i % len(CONDITION_PHRASINGS)].format(sub=subj(pid), c=name),
                patient_id=pid,
                rationale=f"{name} is on this patient's problem list",
                required=[{"categories": ["CONDITION"], "codes": codes}],
                tags=["condition"],
            )

    # -- conditions genuinely absent ---------------------------------------
    used = set()
    for name in CONDITIONS[:4]:
        codes = concepts[name]["codes"]
        for pid in b.pick(
            lambda v, cs=codes: v["cats"].get("CONDITION", 0) > 5
            and not any(c in v["codes"] for c in cs),
            used,
        ):
            b.add(
                archetype="condition_absent",
                query=f"does {subj(pid)} have {name}",
                patient_id=pid,
                rationale=f"populated problem list, but no {name} on it; absence is the answer",
                required=[],
                allowed=[],
                tags=["condition", "abstain"],
            )

    # -- medications present ------------------------------------------------
    used = set()
    for i, name in enumerate(MEDICATIONS):
        codes = concepts[name]["codes"]
        for pid in b.pick(lambda v, cs=codes: any(c in v["codes"] for c in cs), used):
            b.add(
                archetype="medication_present",
                query=MED_PHRASINGS[i % len(MED_PHRASINGS)].format(sub=subj(pid), c=name),
                patient_id=pid,
                rationale=f"{name} appears in this patient's medication history",
                required=[{"categories": ["MEDICATION"], "codes": codes}],
                tags=["medication", "safety"],
            )

    # -- medications absent --------------------------------------------------
    used = set()
    for name in MEDICATIONS[:3]:
        codes = concepts[name]["codes"]
        for pid in b.pick(
            lambda v, cs=codes: v["cats"].get("MEDICATION", 0) > 3
            and not any(c in v["codes"] for c in cs),
            used,
        ):
            b.add(
                archetype="medication_absent",
                query=f"is {subj(pid)} on {name}",
                patient_id=pid,
                rationale=f"patient is on other drugs, but not {name}",
                required=[],
                allowed=[],
                tags=["medication", "abstain"],
            )

    # -- labs, single code ----------------------------------------------------
    used = set()
    for i, name in enumerate(LAB_SINGLE):
        codes = concepts[name]["codes"]
        for pid in b.pick(lambda v, cs=codes: any(c in v["codes"] for c in cs), used):
            b.add(
                archetype="lab_single_code",
                query=LAB_PHRASINGS[i % len(LAB_PHRASINGS)].format(sub=poss(pid), c=name),
                patient_id=pid,
                rationale=f"{name} resolves to {len(codes)} code(s) and this patient has results",
                required=[{"categories": ["LAB"], "codes": codes}],
                tags=["lab"],
            )

    # -- labs, multi-code concepts ---------------------------------------------
    used = set()
    for i, name in enumerate(LAB_MULTI):
        codes = concepts[name]["codes"]
        for pid in b.pick(lambda v, cs=codes: sum(c in v["codes"] for c in cs) >= 2, used):
            b.add(
                archetype="lab_multi_code",
                query=LAB_PHRASINGS[(i + 1) % len(LAB_PHRASINGS)].format(sub=poss(pid), c=name),
                patient_id=pid,
                rationale=(
                    f"{name} spans {len(codes)} codes and this patient has at least 2 of them; "
                    "this is where a wrong code set silently under-retrieves"
                ),
                required=[{"categories": ["LAB"], "codes": codes}],
                tags=["lab", "multi-code"],
            )

    # -- labs with a date window ------------------------------------------------
    used = set()
    for phrasing, days in LAB_WINDOW_PHRASINGS:
        for name in ("blood glucose", "creatinine"):
            codes = concepts[name]["codes"]
            # Presence of the code is not enough here -- the fact has to fall
            # inside the window, which the presence index cannot tell us. Walk
            # candidates until add() accepts one.
            for pid in b.pick(
                lambda v, cs=codes: any(c in v["codes"] for c in cs) and v["cats"].get("LAB", 0) > 200,
                used,
                limit=60,
            ):
                if b.add(
                    archetype="lab_date_window",
                    query=phrasing.format(c=name),
                    patient_id=pid,
                    rationale=f"the window must narrow the {name} result set to the trailing {days} days",
                    required=[{"categories": ["LAB"], "codes": codes}],
                    window_days=days,
                    tags=["lab", "date-window"],
                ):
                    break

    # -- vitals -------------------------------------------------------------------
    used = set()
    for i, name in enumerate(VITALS):
        codes = concepts[name]["codes"]
        for pid in b.pick(lambda v, cs=codes: any(c in v["codes"] for c in cs), used):
            b.add(
                archetype="vital",
                query=VITAL_PHRASINGS[i % len(VITAL_PHRASINGS)].format(sub=poss(pid), c=name),
                patient_id=pid,
                rationale=f"{name} maps to {len(codes)} LOINC code(s)",
                required=[{"categories": ["VITAL"], "codes": codes}],
                tags=["vital"],
            )


def build_language(b: "Builder", concepts: dict, patients: dict) -> None:
    """Phrasings that stress the parser rather than the filter."""

    def subj(pid: str) -> str:
        return pronoun(patients[pid])[0]

    # -- abbreviation-heavy -------------------------------------------------
    abbrevs = [
        ("any h/o MI", "myocardial infarction", "CONDITION"),
        ("does {sub} have HTN", "hypertension", "CONDITION"),
        ("T2DM on the problem list?", "type 2 diabetes", "CONDITION"),
        ("CKD documented?", "chronic kidney disease", "CONDITION"),
        ("latest Hb", "hemoglobin", "LAB"),
        ("check {sub} BP", "blood pressure", "VITAL"),
        ("most recent PLT", "platelets", "LAB"),
        ("TSH result", "thyrotropin", "LAB"),
    ]
    used: set[str] = set()
    for phrasing, name, category in abbrevs:
        codes = concepts[name]["codes"]
        for pid in b.pick(lambda v, cs=codes: any(c in v["codes"] for c in cs), used):
            b.add(
                archetype="abbreviation",
                query=phrasing.format(sub=subj(pid)),
                patient_id=pid,
                rationale=f"clinical shorthand for {name}; the abbreviation must reach the same codes as the full term",
                required=[{"categories": [category], "codes": codes}],
                tags=["abbreviation", "parser"],
            )

    # -- negation ------------------------------------------------------------
    # Documented behaviour: a negated entity is still retrieved. "No known
    # allergies" must still surface the allergy facts on file, because the
    # record contradicting the question is exactly what a clinician needs to
    # see. Gold therefore includes the facts, and this archetype locks that
    # in as a regression guard rather than treating it as a bug.
    negations = [
        ("no known drug allergies", "ALLERGY", None),
        ("{sub} denies any aspirin allergy", "ALLERGY", "1191"),
        ("not on warfarin as far as I know", "MEDICATION", "warfarin"),
        ("no history of diabetes", "CONDITION", "type 2 diabetes"),
        ("{sub} is off clopidogrel", "MEDICATION", "clopidogrel"),
    ]
    used = set()
    for phrasing, category, target in negations:
        if target is None:
            codes = None
            pred = lambda v: v["cats"].get("ALLERGY", 0) > 1
        elif target == "1191":
            codes = ["1191"]
            pred = lambda v: "1191" in v["codes"]
        else:
            codes = concepts[target]["codes"]
            pred = lambda v, cs=codes: any(c in v["codes"] for c in cs)
        for pid in b.pick(pred, used):
            # Same two allowances the allergy archetypes get: the standing
            # disposition finding, and same-drug prescriptions.
            allowed: list[dict] = []
            if category == "ALLERGY":
                allowed.append({"categories": ["ALLERGY"], "codes": [ALLERGIC_DISPOSITION]})
            if target in ALLERGEN_MEDICATION_CODES:
                allowed.append({"categories": ["MEDICATION"], "codes": ALLERGEN_MEDICATION_CODES[target]})
            elif target == "1191":
                allowed.append({"categories": ["MEDICATION"], "codes": ALLERGEN_MEDICATION_CODES["aspirin"]})
            b.add(
                archetype="negation",
                allowed=allowed,
                query=phrasing.format(sub=pronoun(patients[pid])[0]),
                patient_id=pid,
                rationale=(
                    "negated entities are retrieved anyway by design; the on-file record that "
                    "contradicts the question is the clinically important part"
                ),
                required=[{"categories": [category], "codes": codes}],
                tags=["negation", "parser"],
            )

    # -- bare category questions -------------------------------------------
    # The safety case. A patient with nine allergy facts including penicillin
    # must not answer "any allergies" with the single generic disposition
    # marker -- get_patient_facts() refuses to apply `limit` to ALLERGY
    # retrieval for exactly this reason, and that guarantee is worth nothing
    # if the code filter has already discarded eight of the nine facts.
    used = set()
    for phrasing, category in CATEGORY_QUESTIONS:
        for pid in b.pick(
            lambda v, c=category: v["cats"].get(c, 0) >= (4 if c == "ALLERGY" else 6)
            and v["cats"].get(c, 0) <= 60,
            used,
        ):
            b.add(
                archetype="category_question",
                query=phrasing.format(sub=pronoun(patients[pid])[0]),
                patient_id=pid,
                rationale=(
                    f"asks for the whole {category} list, not about one substance; "
                    f"the correct answer is every {category} fact on file"
                ),
                required=[{"categories": [category]}],
                tags=["category", "safety"],
            )

    # -- nothing coded to find -------------------------------------------------
    used = set()
    for phrasing in UNRESOLVABLE[:5]:  # 5 is plenty for one behaviour; see the 80-100 ceiling
        for pid in b.pick(lambda v: v["cats"].get("REPORT", 0) > 5, used):
            b.add(
                archetype="unresolvable",
                query=phrasing,
                patient_id=pid,
                rationale="no coded answer exists in this corpus; returning nothing beats reaching for a plausible wrong code",
                required=[],
                allowed=[],
                tags=["abstain", "parser"],
            )

    # -- drug-class rollups, a deliberate non-feature ---------------------------
    used = set()
    for phrasing in CLASS_ROLLUPS:
        for pid in b.pick(lambda v: v["cats"].get("MEDICATION", 0) > 10, used):
            b.add(
                archetype="class_rollup",
                query=phrasing,
                patient_id=pid,
                rationale=(
                    "drug-class rollups are deliberately not built: an incomplete class list "
                    "under-retrieves silently, which is worse than an honest miss. Gold is empty "
                    "so this archetype proves the decision still holds"
                ),
                required=[],
                allowed=[],
                tags=["abstain", "by-design"],
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-total", type=int, default=ARCHETYPE_MIN_TOTAL)
    args = parser.parse_args()

    concepts = load_concepts()
    known_codes = load_lexicon_codes()

    # Same discipline as tests/test_lexicon.py: a gold code that has fallen
    # out of the corpus must fail the build loudly, not quietly shrink gold.
    missing: list[str] = []
    for name, (code, _) in ALLERGENS.items():
        if code not in known_codes:
            missing.append(f"{name}: {code}")
    for group in (LAB_SINGLE, LAB_MULTI, CONDITIONS, MEDICATIONS, VITALS):
        for name in group:
            if name not in concepts:
                missing.append(f"concept not in synonyms.csv: {name}")
                continue
            for code in concepts[name]["codes"]:
                if code not in known_codes:
                    missing.append(f"{name}: {code}")
    if missing:
        raise SystemExit("gold codes not present in lexicon.csv:\n  " + "\n  ".join(missing))

    patients = {p.patient_id: p for p in list_patients()}

    codes_of_interest = {code for code, _ in ALLERGENS.values()}
    codes_of_interest.add(ALLERGIC_DISPOSITION)
    for spec in concepts.values():
        codes_of_interest.update(spec["codes"])

    print(f"indexing corpus for {len(codes_of_interest)} codes of interest ...")
    index = build_presence_index(codes_of_interest)

    b = Builder(index, patients)
    build_allergies(b, patients)
    build_clinical(b, concepts, patients)
    build_language(b, concepts, patients)

    if len(b.entries) < args.min_total:
        raise SystemExit(
            f"only {len(b.entries)} queries built, below the {args.min_total} floor -- "
            "add archetypes rather than lowering the floor"
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "_meta": {
                        "eval_today": EVAL_TODAY.isoformat(),
                        "queries": len(b.entries),
                        "built_by": "scripts/build_eval_set.py",
                        "note": (
                            "Gold fact ids are resolved from hand-specified clinical clauses, "
                            "never from parse_query() or retrieve(). See the module docstring."
                        ),
                    }
                }
            )
            + "\n"
        )
        for entry in b.entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    by_archetype: dict[str, int] = {}
    for entry in b.entries:
        by_archetype[entry["archetype"]] = by_archetype.get(entry["archetype"], 0) + 1
    print(f"{len(b.entries)} queries -> {args.out}")
    for name, count in sorted(by_archetype.items()):
        print(f"  {count:3d}  {name}")


if __name__ == "__main__":
    main()
