"""Optional LLM prose rewrite of an already-built Brief. Strictly isolated:
this is the only module that calls an LLM, and every failure mode --
disabled flag, missing/misconfigured client, timeout, API error, malformed
response -- degrades to returning the input Brief completely unchanged.

The template path (``backend/brief.py``) is grounded by construction: every
line is built straight from a Fact. This module never re-derives facts; it
only asks the model to restate a fixed digest of what ``build_brief()``
already produced, and is instructed to add nothing. No groundedness checker
is built here on purpose -- the prompt spec calls that out as unnecessary
scope, since the template output it's grounded against is trustworthy by
construction and the LLM path is a stretch feature layered on top of it.
"""

from __future__ import annotations

import os

from backend.models import Brief, BriefSource

_ENABLED_ENV_VAR = "KRAVOS_LLM_POLISH_ENABLED"
_TRUE_VALUES = {"1", "true", "yes", "on"}

_SYSTEM_PROMPT = (
    "You rewrite a structured clinical brief as flowing prose for a clinician "
    "to skim before surgery. The text you are given is the complete set of "
    "facts retrieved from the patient's record -- treat it as the entire "
    "truth, not a partial excerpt.\n\n"
    "Rules, no exceptions:\n"
    "- Do not add any medication, value, date, diagnosis, or allergy that is "
    "not explicitly present in the input.\n"
    "- Do not infer, estimate, or fill a gap the input doesn't state.\n"
    "- Preserve every specific value, date, and status exactly as given -- "
    "do not round, approximate, or paraphrase them.\n"
    "- If the input says something is not on file, say so plainly; do not "
    "omit it or soften it.\n"
    "- Output only the rewritten prose -- no headers, no markdown, no "
    "preamble, no closing remarks."
)


def llm_polish_enabled() -> bool:
    return os.environ.get(_ENABLED_ENV_VAR, "").strip().lower() in _TRUE_VALUES


def _render_facts_digest(brief: Brief) -> str:
    lines = [f"Patient summary: {brief.headline}", ""]
    for section in brief.sections:
        lines.append(f"{section.title}:")
        if section.lines:
            lines.extend(f"- {line.text}" for line in section.lines)
        else:
            lines.append(f"- {section.empty_note}")
        lines.append("")
    if brief.missing:
        lines.append("Not on file:")
        for item in brief.missing:
            detail = (
                f"last seen {item.last_seen.date().isoformat()}"
                if item.last_seen
                else "never recorded"
            )
            lines.append(f"- {item.label} ({detail})")
    return "\n".join(lines)


def _generate_prose(brief: Brief, *, timeout: float) -> str:
    import anthropic  # Lazy: this module must stay importable with no key set.

    client = anthropic.Anthropic(timeout=timeout)
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": _render_facts_digest(brief)}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return text.strip()


def polish_brief(brief: Brief, *, timeout: float = 10.0) -> Brief:
    """Return a prose-polished copy of ``brief``, or ``brief`` unchanged.

    Unchanged whenever the flag is off, the client can't be built (no
    package, no key), the call errors, or it times out -- every branch
    through this function is safe to call from the request path.
    """
    if not llm_polish_enabled():
        return brief

    try:
        prose = _generate_prose(brief, timeout=timeout)
    except Exception:
        return brief

    if not prose.strip():
        return brief

    return brief.model_copy(update={"prose": prose, "source": BriefSource.LLM_POLISHED})
