"""The contracts everything downstream depends on.

Four primary models -- `Fact`, `PatientSummary`, `RetrievalResult`, `Brief` --
plus the supporting types they need. `ResolvedEntity` and `ParsedQuery` are
imported from `retrieval.models` rather than re-declared: the NER layer is
done, and duplicating its shapes here would create two truths.

Design notes worth reading before building on these:

* `Fact.value` is `str | None`, not a number. Measured over the corpus: every
  populated value arrives as a string ("91.3"), never a JSON number. Casting
  belongs at the point of use, not at the boundary.
* `EntityFacts.outcome` exists so that "this patient has no allergy records"
  and "nothing in the query resolved" are never the same answer.
* `EntityFacts.coverage_note` is the seat reserved for drug-class rollups.
  When classes land, "class list covers 14 agents, reviewed 2026-03-01"
  attaches here without restructuring anything.
* `Brief.prose` never replaces `Brief.sections`. The LLM polish pass adds a
  field; it does not overwrite the grounded template.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from retrieval.models import ParsedQuery, ResolvedEntity

__all__ = [
    "Brief",
    "BriefLine",
    "BriefSection",
    "BriefSource",
    "EntityFacts",
    "Fact",
    "MissingItem",
    "MissingReason",
    "NarrativeMatch",
    "PatientSummary",
    "QueryResponse",
    "RetrievalOutcome",
    "RetrievalResult",
    "StageTimings",
]


# --------------------------------------------------------------------------
# facts and patients
# --------------------------------------------------------------------------


class Fact(BaseModel):
    """One row of facts.jsonl, mirroring the ingestion schema exactly.

    Provenance fields (`source_resource_type`, `source_resource_id`,
    `encounter_id`, `also_in`) are part of the record, not decoration: the UI
    shows them on click and the brief cites `fact_id` for every line.
    """

    fact_id: str
    patient_id: str
    category: str | None = None
    safety_class: str | None = None
    text: str | None = None
    code_system: str | None = None
    code: str | None = None
    display: str | None = None
    value: str | None = None
    unit: str | None = None
    date: datetime | None = None
    status: str | None = None
    source_resource_type: str | None = None
    source_resource_id: str | None = None
    encounter_id: str | None = None
    also_in: list[str] = Field(default_factory=list)

    @property
    def value_with_unit(self) -> str | None:
        if self.value is None:
            return None
        return f"{self.value} {self.unit}".strip() if self.unit else self.value


class PatientSummary(BaseModel):
    """One row of patients.jsonl. The underscore-prefixed source keys are
    aliased to clean names; both spellings are accepted on input."""

    model_config = ConfigDict(populate_by_name=True)

    patient_id: str
    name: str | None = None
    birth_date: date | None = None
    gender: str | None = None
    deceased: datetime | None = None
    n_facts: int = Field(default=0, alias="_n_facts")
    source_file: str | None = Field(default=None, alias="_source_file")

    @property
    def age_years(self) -> int | None:
        """Age today, or age at death. None when birth_date is missing."""
        if self.birth_date is None:
            return None
        end = self.deceased.date() if self.deceased else date.today()
        years = end.year - self.birth_date.year
        if (end.month, end.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1
        return max(years, 0)

    @property
    def is_deceased(self) -> bool:
        return self.deceased is not None


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------


class RetrievalOutcome(str, Enum):
    """Why an entity's fact list looks the way it does.

    The distinction this enum exists for: an empty `FOUND_NONE` means the
    patient genuinely has nothing on file, which is a clinical finding.
    `NOT_RETRIEVED` means we never asked, which is not.
    """

    FOUND = "found"
    FOUND_NONE = "none_on_file"
    NOT_RETRIEVED = "not_retrieved"


class MissingReason(str, Enum):
    ABSENT = "absent"  # never recorded
    STALE = "stale"  # recorded, but outside the expected recency window


class StageTimings(BaseModel):
    """Per-stage latency as structured fields, not log lines.

    Populated as the request moves through the pipeline; a stage that did not
    run stays None. `vector_ms` is reserved for stage 2 and stays None until
    the Pinecone path exists.
    """

    parse_ms: float | None = None
    retrieve_ms: float | None = None
    brief_ms: float | None = None
    vector_ms: float | None = None
    llm_ms: float | None = None
    total_ms: float | None = None


class EntityFacts(BaseModel):
    """Facts retrieved for one resolved entity.

    Kept per-entity rather than merged into one pile because entities can
    share a span: `aspirin` resolves to both an ALLERGY substance and four
    MEDICATION products, and those are two different clinical answers.
    """

    entity: ResolvedEntity
    facts: list[Fact] = Field(default_factory=list)
    outcome: RetrievalOutcome = RetrievalOutcome.NOT_RETRIEVED
    negated_in_query: bool = False
    truncated: bool = False
    limit_applied: int | None = None
    # Reserved for drug-class rollups: "class list covers N agents, reviewed
    # <date>". Coverage is disclosed with the answer, never hidden.
    coverage_note: str | None = None

    @property
    def code_count(self) -> int:
        """A class rollup carries 30+ codes here as naturally as glucose
        carries 4 -- nothing downstream may assume a small set."""
        return len(self.entity.codes)


class MissingItem(BaseModel):
    """Something a pre-op assessment expects to see that is not on file.

    A set difference over coded facts, not a model. The product
    differentiator is naming what is absent, so this is a first-class result
    field rather than a footnote.
    """

    key: str
    label: str
    reason: MissingReason = MissingReason.ABSENT
    categories: list[str] = Field(default_factory=list)
    codes: list[str] = Field(default_factory=list)
    window_days: int | None = None
    last_seen: datetime | None = None
    detail: str | None = None


class NarrativeMatch(BaseModel):
    """One hit from the REPORT-note vector-search fallback.

    Stage 2 only, and a fallback only: ``retrieve()`` attempts this path
    solely when nothing in the query resolved to a code at all. Never a
    replacement for coded retrieval -- REPORT is the ~5% of the corpus with
    no code to filter on, everything else stays on the structured path.
    """

    fact_id: str
    encounter_id: str | None = None
    date: datetime | None = None
    section: str
    text: str
    score: float


class RetrievalResult(BaseModel):
    """Everything the retrieval stage found, and everything it did not."""

    patient_id: str
    query: str
    entities: list[EntityFacts] = Field(default_factory=list)
    unresolved_spans: list[str] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    missing: list[MissingItem] = Field(default_factory=list)
    # Populated only on the vector fallback path (stage 2, off by default --
    # see backend/vector_store.py). Additive: existing callers that never
    # look at this field see the same RetrievalResult shape as before.
    narrative_matches: list[NarrativeMatch] = Field(default_factory=list)
    # parse_ms / retrieve_ms are filled here; brief_ms and total_ms are added
    # by the caller that renders the brief.
    timings: StageTimings = Field(default_factory=StageTimings)

    @property
    def resolved(self) -> bool:
        """False means nothing in the query mapped to a code -- which is a
        different answer from "the patient has no such facts"."""
        return any(e.outcome is not RetrievalOutcome.NOT_RETRIEVED for e in self.entities)

    @property
    def fact_count(self) -> int:
        return len({f.fact_id for e in self.entities for f in e.facts})

    def all_facts(self) -> list[Fact]:
        """Every retrieved fact, de-duplicated across entities that overlap."""
        seen: dict[str, Fact] = {}
        for entity in self.entities:
            for fact in entity.facts:
                seen.setdefault(fact.fact_id, fact)
        return list(seen.values())


# --------------------------------------------------------------------------
# brief
# --------------------------------------------------------------------------


class BriefSource(str, Enum):
    TEMPLATE = "template"
    LLM_POLISHED = "llm_polished"


class BriefLine(BaseModel):
    """One rendered line, carrying the fact ids that justify it.

    Provenance is not optional: the UI resolves these back to source
    resources on click.
    """

    text: str
    fact_ids: list[str] = Field(default_factory=list)
    category: str | None = None
    status: str | None = None
    date: datetime | None = None


class BriefSection(BaseModel):
    """A titled group of lines.

    `empty_note` carries the explicit negative -- "No allergy records on
    file." -- so an empty section still says something.
    """

    title: str
    lines: list[BriefLine] = Field(default_factory=list)
    empty_note: str | None = None
    coverage_note: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.lines


class Brief(BaseModel):
    """The rendered answer.

    `sections` is always the deterministic template output. The optional LLM
    polish pass writes `prose` and flips `source`; it never edits or replaces
    `sections`, so the grounded version survives a bad rewrite and the demo
    works with the flag off.
    """

    patient_id: str
    headline: str
    sections: list[BriefSection] = Field(default_factory=list)
    missing: list[MissingItem] = Field(default_factory=list)
    source: BriefSource = BriefSource.TEMPLATE
    prose: str | None = None
    generated_at: datetime = Field(default_factory=datetime.now)

    @property
    def fact_ids(self) -> list[str]:
        seen: list[str] = []
        for section in self.sections:
            for line in section.lines:
                for fact_id in line.fact_ids:
                    if fact_id not in seen:
                        seen.append(fact_id)
        return seen


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


class QueryRequest(BaseModel):
    patient_id: str
    query: str


class QueryResponse(BaseModel):
    """What POST /api/query returns.

    `parsed` is included so the UI can render the question with its resolved
    entities highlighted by match quality -- the NER work is visible rather
    than invisible.
    """

    patient: PatientSummary
    query: str
    parsed: ParsedQuery
    brief: Brief
    result: RetrievalResult
    timings: StageTimings = Field(default_factory=StageTimings)
