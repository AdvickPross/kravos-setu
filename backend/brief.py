"""Deterministic template rendering: RetrievalResult -> Brief.

Template only, no LLM. Grounded by construction: every line is built
straight from a Fact and carries that fact's id, so there's nothing here
that could hallucinate. The optional LLM polish pass is a later, separate
step that rewrites this output as prose without being allowed to touch
which facts got cited.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.models import Brief, BriefLine, BriefSection, BriefSource, Fact, PatientSummary, RetrievalResult

_MIN_DATETIME = datetime.min.replace(tzinfo=timezone.utc)

# Clinical priority order for sections that are present; anything outside
# this list (should not happen -- category is corpus-derived) is appended
# afterwards, alphabetically, rather than dropped.
_CATEGORY_ORDER = [
    "ALLERGY",
    "MEDICATION",
    "CONDITION",
    "LAB",
    "VITAL",
    "PROCEDURE",
    "IMMUNIZATION",
    "REPORT",
]

_CATEGORY_TITLES = {
    "ALLERGY": "Allergies",
    "MEDICATION": "Medications",
    "CONDITION": "Conditions",
    "LAB": "Labs",
    "VITAL": "Vitals",
    "PROCEDURE": "Procedures",
    "IMMUNIZATION": "Immunizations",
    "REPORT": "Reports",
}

# Allergies get an explicit negative rather than the generic phrasing --
# "no known allergies" is a clinical statement a doctor reads differently
# from "nothing retrieved."
_EMPTY_NOTES = {
    "ALLERGY": "No allergy records on file.",
}


def _section_title(category: str) -> str:
    return _CATEGORY_TITLES.get(category, category.title())


def _empty_note(category: str) -> str:
    return _EMPTY_NOTES.get(category, f"No {_section_title(category).lower()} on file for this query.")


def _render_line(fact: Fact) -> BriefLine:
    label = fact.display or fact.code or fact.fact_id
    bits = [label]

    if fact.value_with_unit:
        bits.append(fact.value_with_unit)

    date_str = fact.date.date().isoformat() if fact.date else None
    status_date = " ".join(x for x in (fact.status, date_str) if x)
    if status_date:
        bits.append(status_date)

    return BriefLine(
        text=" — ".join(bits),
        fact_ids=[fact.fact_id],
        category=fact.category,
        status=fact.status,
        date=fact.date,
    )


def _build_headline(patient: PatientSummary, result: RetrievalResult) -> str:
    who = patient.name or patient.patient_id
    age_bit = f", {patient.age_years}y" if patient.age_years is not None else ""
    gender_bit = f" {patient.gender}" if patient.gender else ""
    fact_bit = f"{result.fact_count} fact" + ("" if result.fact_count == 1 else "s")
    missing_bit = f"; {len(result.missing)} item(s) not on file" if result.missing else ""
    return f"{who}{age_bit}{gender_bit} — {fact_bit} retrieved{missing_bit}."


def build_brief(result: RetrievalResult, patient: PatientSummary) -> Brief:
    facts_by_category: dict[str, dict[str, Fact]] = {}
    for entity_facts in result.entities:
        category = entity_facts.entity.category or "OTHER"
        bucket = facts_by_category.setdefault(category, {})
        for fact in entity_facts.facts:
            bucket.setdefault(fact.fact_id, fact)

    ordered_categories = [c for c in _CATEGORY_ORDER if c in facts_by_category]
    ordered_categories += sorted(c for c in facts_by_category if c not in _CATEGORY_ORDER)

    sections: list[BriefSection] = []
    for category in ordered_categories:
        facts = sorted(
            facts_by_category[category].values(),
            key=lambda f: f.date or _MIN_DATETIME,
            reverse=True,
        )
        lines = [_render_line(f) for f in facts]
        sections.append(
            BriefSection(
                title=_section_title(category),
                lines=lines,
                empty_note=None if lines else _empty_note(category),
            )
        )

    return Brief(
        patient_id=result.patient_id,
        headline=_build_headline(patient, result),
        sections=sections,
        missing=result.missing,
        source=BriefSource.TEMPLATE,
    )
