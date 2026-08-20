"""Pydantic types for query-side NER."""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class MatchQuality(str, Enum):
    """How an entity span was resolved to codes.

    The downstream router branches on this, so an honest ``UNRESOLVED``
    is more useful than a plausible-looking wrong code.
    """

    EXACT = "exact"
    SYNONYM = "synonym"
    FUZZY = "fuzzy"
    UNRESOLVED = "unresolved"


class ResolvedEntity(BaseModel):
    """One span of the query, resolved (or not) to corpus codes."""

    span: str
    start: int
    end: int
    category: str | None = None
    code_system: str | None = None
    # A list, not a string: 23 lab analytes in this corpus map to several
    # LOINC codes once normalized (glucose -> 4, bilirubin.total -> 4,
    # creatinine/potassium/sodium/hemoglobin -> 2), and the concept layer
    # will later span code systems. SQL filters on `code IN (...)`.
    codes: list[str] = Field(default_factory=list)
    display: str | None = None
    quality: MatchQuality
    negated: bool = False


class ParsedQuery(BaseModel):
    """The parsed form of a doctor's natural-language question."""

    raw: str
    entities: list[ResolvedEntity] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
