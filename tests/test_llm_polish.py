"""backend.llm_polish -- every branch must degrade to the input Brief
unchanged except the one where a call actually succeeds.
"""

from __future__ import annotations

import pytest

from backend import llm_polish
from backend.llm_polish import _render_facts_digest, llm_polish_enabled, polish_brief
from backend.models import Brief, BriefLine, BriefSection, BriefSource, MissingItem, MissingReason


def _brief(**overrides) -> Brief:
    base = dict(
        patient_id="patient-1",
        headline="Test Patient, 40y male — 1 fact retrieved.",
        sections=[
            BriefSection(
                title="Medications",
                lines=[BriefLine(text="Aspirin 81 MG — active 2026-01-01", fact_ids=["f1"])],
            ),
            BriefSection(title="Allergies", lines=[], empty_note="No allergy records on file."),
        ],
        missing=[
            MissingItem(key="cbc", label="Recent CBC", reason=MissingReason.ABSENT),
        ],
    )
    base.update(overrides)
    return Brief(**base)


@pytest.fixture(autouse=True)
def _flag_unset(monkeypatch):
    monkeypatch.delenv("KRAVOS_LLM_POLISH_ENABLED", raising=False)


# --------------------------------------------------------------------- flag


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_flag_recognizes_truthy_values(monkeypatch, value):
    monkeypatch.setenv("KRAVOS_LLM_POLISH_ENABLED", value)
    assert llm_polish_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "", "no", "banana"])
def test_flag_recognizes_falsy_values(monkeypatch, value):
    monkeypatch.setenv("KRAVOS_LLM_POLISH_ENABLED", value)
    assert llm_polish_enabled() is False


def test_flag_defaults_to_disabled():
    assert llm_polish_enabled() is False


# ------------------------------------------------------------- polish_brief


def test_returns_brief_unchanged_when_flag_is_off():
    brief = _brief()
    result = polish_brief(brief)
    assert result is brief
    assert result.source is BriefSource.TEMPLATE
    assert result.prose is None


def test_returns_brief_unchanged_when_generation_raises(monkeypatch):
    monkeypatch.setenv("KRAVOS_LLM_POLISH_ENABLED", "1")
    monkeypatch.setattr(
        llm_polish, "_generate_prose", lambda brief, timeout: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    brief = _brief()
    result = polish_brief(brief)
    assert result is brief
    assert result.source is BriefSource.TEMPLATE
    assert result.prose is None


def test_returns_brief_unchanged_when_generation_returns_empty_string(monkeypatch):
    monkeypatch.setenv("KRAVOS_LLM_POLISH_ENABLED", "1")
    monkeypatch.setattr(llm_polish, "_generate_prose", lambda brief, timeout: "   ")
    brief = _brief()
    result = polish_brief(brief)
    assert result is brief


def test_successful_polish_sets_prose_and_source_without_mutating_input(monkeypatch):
    monkeypatch.setenv("KRAVOS_LLM_POLISH_ENABLED", "1")
    monkeypatch.setattr(llm_polish, "_generate_prose", lambda brief, timeout: "The patient is on aspirin.")

    brief = _brief()
    result = polish_brief(brief)

    assert result is not brief
    assert result.prose == "The patient is on aspirin."
    assert result.source is BriefSource.LLM_POLISHED
    # Grounded template output survives untouched -- the LLM path never
    # overwrites it, only adds prose alongside it.
    assert result.sections == brief.sections
    assert result.missing == brief.missing

    # Original object is untouched -- model_copy, not mutation.
    assert brief.prose is None
    assert brief.source is BriefSource.TEMPLATE


def test_missing_import_of_anthropic_degrades_gracefully(monkeypatch):
    """Simulates the package not being installed -- the real failure mode
    _generate_prose's lazy `import anthropic` exists to survive."""
    monkeypatch.setenv("KRAVOS_LLM_POLISH_ENABLED", "1")

    def _raise_import_error(brief, timeout):
        raise ModuleNotFoundError("No module named 'anthropic'")

    monkeypatch.setattr(llm_polish, "_generate_prose", _raise_import_error)
    brief = _brief()
    result = polish_brief(brief)
    assert result is brief


# --------------------------------------------------------------- the digest


def test_digest_includes_headline_lines_and_empty_notes():
    digest = _render_facts_digest(_brief())
    assert "Test Patient, 40y male" in digest
    assert "Aspirin 81 MG — active 2026-01-01" in digest
    assert "No allergy records on file." in digest


def test_digest_includes_missing_items_with_absent_reason():
    digest = _render_facts_digest(_brief())
    assert "Recent CBC (never recorded)" in digest


def test_digest_includes_stale_reason_with_last_seen_date():
    from datetime import datetime, timezone

    brief = _brief(
        missing=[
            MissingItem(
                key="cbc",
                label="Recent CBC",
                reason=MissingReason.STALE,
                last_seen=datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
        ]
    )
    digest = _render_facts_digest(brief)
    assert "Recent CBC (last seen 2025-01-01)" in digest
