"""OCR lines in, coded clinical facts out.

This reuses `retrieval.lexicon.Lexicon` rather than writing a second
extractor, and that is the most consequential decision in the file. The
lexicon is the same surface-form index the *query* side uses, so "Penicillin"
read off a scan resolves to exactly the code a doctor's question about
penicillin resolves to. A parallel extractor would drift from it within a
month, and the failure would be silent: facts that exist in the corpus but
cannot be retrieved, because one side spells a concept differently from the
other.

**Section headers set the category, and this is a safety mechanism.** A line
reading `Penicillin` under `ALLERGIES:` is an allergy. The identical line
under `MEDICATIONS:` is a prescription. Nothing about the token distinguishes
them, and getting it backwards inverts the clinical meaning -- a drug the
patient must never receive, recorded as one they take daily. So a bare drug
name outside any recognised section is deliberately **not** promoted to a
fact: the ambiguity is reported rather than guessed.

**Nothing here is trusted the way a FHIR fact is trusted.** Every fact
carries the OCR confidence of the line it came from, and provenance down to
the page and line, so the source region can be shown to a human. Low
confidence lines never become facts at all.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from ocr.engine import OcrDocument, OcrLine
from retrieval.lexicon import Lexicon
from retrieval.normalize import normalize_query_text

log = logging.getLogger(__name__)

# Headings as they actually appear on Indian discharge summaries and OPD
# slips, mapped to the corpus's categories. Matched case-insensitively
# against the start of a line, after OCR has had its way with the spacing.
SECTION_HEADINGS: dict[str, tuple[str, ...]] = {
    "ALLERGY": ("allergy", "allergies", "drug allergy", "known allergies", "allergen"),
    "MEDICATION": (
        "medication",
        "medications",
        "drugs",
        "treatment",
        "rx",
        "prescription",
        "discharge medication",
        "discharge medications",
        "advice on discharge",
    ),
    "CONDITION": (
        "diagnosis",
        "diagnoses",
        "final diagnosis",
        "provisional diagnosis",
        "problem list",
        "problems",
        "comorbidities",
        "past history",
        "history",
    ),
    "PROCEDURE": ("procedure", "procedures", "surgery", "operation", "operative notes"),
    "LAB": ("investigation", "investigations", "lab", "labs", "laboratory", "results"),
    "VITAL": ("vitals", "vital signs", "observations"),
    "IMMUNIZATION": ("immunisation", "immunization", "vaccination", "vaccines"),
}

# A heading line looks like "ALLERGIES:" or "Discharge Medications -".
# The character class tolerates the stray dots and apostrophes OCR sprinkles
# through capitalised text: the demo scan reads "DISCHARGE.MEDICATIONS:", and
# a stricter pattern silently loses the whole medication block -- every drug
# in it then falls out as "outside any section".
_HEADING = re.compile(r"^\s*([A-Za-z][A-Za-z ./'`\-]{2,44}?)\s*[:\-–]\s*(.*)$")

# Characters OCR invents between real ones. Replaced with a space rather than
# deleted, so "Essential.hypertension" becomes two words instead of one.
_OCR_NOISE_CHARS = re.compile(r"[.:;'\"`|_]+")


def clean_ocr_text(raw: str) -> str:
    """Undo the punctuation a scanner hallucinates into clean text.

    Applied to each candidate phrase before it reaches the lexicon. Without
    it "Essential.hypertension" and "Type .2: diabetes. mellitus" match
    nothing at all -- the concepts are present and readable to a human, and
    invisible to an exact-form index.

    Runs *after* item splitting, because the comma it would erase is what
    separates two drugs on one line.
    """
    text = _OCR_NOISE_CHARS.sub(" ", raw)
    # "Atorvastatin20mg" -- OCR drops the space before a dose.
    text = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -")

# "Aspirin 75 mg OD" -> value 75, unit mg. Dose is recorded when present and
# never inferred when absent.
_DOSE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|units?|iu)\b", re.I)

# Lines that carry no clinical content and would otherwise generate noise.
_NOISE = re.compile(
    r"^\s*(page\s*\d+|\d+\s*/\s*\d+|[-_=.*]{3,}|signature|stamp|seal)\s*$", re.I
)

SOURCE_RESOURCE_TYPE = "ScannedDocument"


@dataclass(frozen=True)
class OcrFact:
    """A fact read off a scan, plus why it should or should not be believed.

    Deliberately not `backend.models.Fact`: that model is the contract for
    facts the system already trusts, and widening it with OCR-only fields
    would put confidence and page numbers on every FHIR-derived row too.
    `as_fact_row()` converts at the boundary.
    """

    fact_id: str
    patient_id: str
    category: str
    safety_class: str
    text: str
    code_system: str | None
    code: str | None
    display: str | None
    value: str | None
    unit: str | None
    date: datetime | None
    status: str
    source_resource_id: str
    confidence: float
    page: int
    line_no: int
    line_text: str
    # Codes the same phrase also reached, in preference order. See _pick_code.
    also_in: list[str] = field(default_factory=list)

    def as_fact_row(self) -> dict:
        """The shape `facts` in Postgres expects."""
        return {
            "fact_id": self.fact_id,
            "patient_id": self.patient_id,
            "category": self.category,
            "safety_class": self.safety_class,
            "text": self.text,
            "code_system": self.code_system,
            "code": self.code,
            "display": self.display,
            "value": self.value,
            "unit": self.unit,
            "date": self.date.isoformat() if self.date else None,
            "status": self.status,
            "source_resource_type": SOURCE_RESOURCE_TYPE,
            "source_resource_id": self.source_resource_id,
            "encounter_id": None,
            "also_in": list(self.also_in),
        }


@dataclass
class ExtractionReport:
    """What was found, and -- more usefully -- what was not.

    The skipped lists are the point. A scan that yields eight facts and
    silently drops four drug names is worse than one that yields eight and
    says so, because the first looks complete.
    """

    facts: list[OcrFact] = field(default_factory=list)
    low_confidence: list[OcrLine] = field(default_factory=list)
    unmatched: list[OcrLine] = field(default_factory=list)
    ambiguous: list[OcrLine] = field(default_factory=list)
    sections_seen: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{len(self.facts)} facts | {len(self.unmatched)} lines matched no "
            f"concept | {len(self.ambiguous)} outside any section | "
            f"{len(self.low_confidence)} below the confidence floor"
        )


def _heading_category(line: str) -> tuple[str | None, str]:
    """Returns (category, remainder-of-line) when this line is a heading."""
    match = _HEADING.match(line)
    if not match:
        return None, line
    label = normalize_query_text(clean_ocr_text(match.group(1)))
    for category, headings in SECTION_HEADINGS.items():
        if label in headings:
            return category, match.group(2).strip()
    return None, line


def _fact_id(*parts: object) -> str:
    """Same scheme as `extract.py`: sha1 of the joined parts, 16 chars.

    Keyed on **patient**, source file, page, line and code -- not on the text.
    Every part of that is load-bearing:

    * the file, page and line make re-ingesting an unchanged scan idempotent,
      and keep two different scans that happen to share a line distinct;
    * the **patient id** stops one person's document colliding with another's.
      Without it, uploading the same discharge summary for a second patient
      produced ids that already existed, `ON CONFLICT DO NOTHING` swallowed
      every row, and the second patient's chart came back empty with the API
      reporting a cheerful "inserted 0 of 12". A shared document between two
      patients is not exotic -- a family brings in one folder, or the same
      template is scanned twice -- and the failure is silent on the side that
      matters.
    """
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _split_items(text: str) -> list[str]:
    """One line may list several things: "Aspirin 75mg, Metformin 500mg"."""
    parts = re.split(r"[,;]|\s{3,}|•", text)
    return [p.strip(" .-•") for p in parts if p.strip(" .-•")]


def extract_facts(
    document: OcrDocument,
    patient_id: str,
    *,
    lexicon: Lexicon | None = None,
    document_date: datetime | None = None,
) -> ExtractionReport:
    """Turn one OCR'd document into coded facts for `patient_id`.

    `document_date` is the date the scan is *about* -- a discharge summary
    dated last March describes last March. It is a parameter rather than
    something guessed off the page, because a wrong date silently moves a
    fact in and out of every recency window the brief computes.
    """
    lexicon = lexicon or Lexicon()
    report = ExtractionReport()
    current: str | None = None
    date = document_date or datetime.now(timezone.utc)

    for line in document.lines:
        if _NOISE.match(line.text):
            continue

        heading, remainder = _heading_category(line.text)
        if heading:
            current = heading
            if heading not in report.sections_seen:
                report.sections_seen.append(heading)
            if not remainder:
                continue
            body = remainder
        else:
            body = line.text

        if not line.is_reliable:
            report.low_confidence.append(line)
            continue

        matched_any = False
        for item in _split_items(body):
            fact = _fact_from_item(
                item, line, current, patient_id, lexicon, date, document
            )
            if fact is None:
                continue
            if fact == "ambiguous":
                report.ambiguous.append(line)
                matched_any = True
                continue
            report.facts.append(fact)
            matched_any = True

        if not matched_any and len(body) > 2:
            report.unmatched.append(line)

    log.info("extracted from %s: %s", document.source.name, report.summary())
    return report


def _fact_from_item(
    item: str,
    line: OcrLine,
    category: str | None,
    patient_id: str,
    lexicon: Lexicon,
    date: datetime,
    document: OcrDocument,
) -> OcrFact | str | None:
    """One candidate phrase -> a fact, the string "ambiguous", or nothing."""
    # Dose is stripped before lookup ("Aspirin 75 mg" is not a surface form)
    # but kept for the fact.
    item = clean_ocr_text(item)
    dose = _DOSE.search(item)
    phrase = _DOSE.sub("", item).strip(" .-")
    if len(phrase) < 3:
        return None

    hits = lexicon.lookup(normalize_query_text(phrase))
    if not hits:
        return None

    if category is None:
        # A recognised concept with no section to tell us what it means. The
        # allergy/medication inversion lives here, so this is reported and
        # not guessed.
        return "ambiguous"

    in_category = [hit for hit in hits if hit.entry.category == category]
    if not in_category:
        # The phrase is known, but not as the kind of thing this section
        # holds -- a lab name under MEDICATIONS, say. Also a judgement the
        # extractor declines to make.
        return "ambiguous"

    entry, alternates = _pick_code(in_category)
    return OcrFact(
        fact_id=_fact_id(
            patient_id, document.source.name, line.page, line.line_no, entry.code
        ),
        patient_id=patient_id,
        category=category,
        safety_class="SAFETY" if category == "ALLERGY" else "CLINICAL",
        text=f"{category.title()}: {entry.display} (scanned, OCR {line.confidence:.0%})",
        code_system=entry.code_system,
        code=entry.code,
        display=entry.display,
        value=dose.group(1) if dose else None,
        unit=dose.group(2).lower() if dose else None,
        date=date,
        status="active",
        source_resource_id=f"{document.source.name}#p{line.page}L{line.line_no}",
        confidence=line.confidence,
        page=line.page,
        line_no=line.line_no,
        line_text=line.text,
        also_in=alternates,
    )


# Phrases in a lab display that name a specimen other than blood. "hemoglobin"
# reaches both a serum assay and a urine dipstick code, and which one the
# lexicon returns first is an accident of file order.
_NON_BLOOD_SPECIMEN = re.compile(
    r"(urine|urinary|dipstick|test strip|stool|faec|csf|saliva|sputum)", re.I
)


def _pick_code(hits: list) -> tuple[object, list[str]]:
    """Choose one code when a phrase legitimately reaches several.

    The README documents this collision and calls it intended -- on the
    *query* side. Asking about hemoglobin and being shown both the blood
    result and the urine dipstick is defensible, because a human reads both.

    Writing is different. A fact gets exactly one code, and picking the urine
    dipstick for a line that says "Hemoglobin" under INVESTIGATIONS puts a
    wrong code into the corpus, where every later query inherits it. The
    first-hit-wins version of this function did precisely that on the demo
    scan.

    So: prefer a curated concept from synonyms.csv, then a display that names
    no specimen other than blood, then the shortest display, which is the
    least qualified and so the most general. The rejected codes are kept in
    `also_in` rather than discarded -- they are real alternatives, and a
    reviewer deciding whether the extraction was right needs to see them.
    """

    def rank(hit) -> tuple[int, int, int, str]:
        display = hit.entry.display or ""
        return (
            0 if hit.entry.concept else 1,
            1 if _NON_BLOOD_SPECIMEN.search(display) else 0,
            len(display),
            display,
        )

    ordered = sorted(hits, key=rank)
    chosen = ordered[0].entry
    alternates = [h.entry.code for h in ordered[1:] if h.entry.code != chosen.code]
    return chosen, alternates


def to_rows(facts: Iterable[OcrFact]) -> list[dict]:
    return [fact.as_fact_row() for fact in facts]
