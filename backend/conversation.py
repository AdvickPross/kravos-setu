"""Conversational RAG orchestrator behind ``POST /api/chat``.

Given a patient, an optional ``conversation_id``, and a new message: parse
it through the same NER every single-shot query uses (`retrieval.ner.
parse_query`), retrieve through the same `backend.retrieve.retrieve`
every single-shot query uses (Postgres-backed coded facts, Pinecone
narrative fallback), then add the two things neither of those has on its
own -- conversation memory, and a model that can actually converse about
what was found rather than render a fixed template -- and persist the
exchange so the next question can build on it.

No router, no intent classifier: turn handling here is a small set of
explicit rules (`_looks_narrative`), not a separate module -- the
conversational layer this backs stays legible without one.

Vector search stops being a fallback attempted only when nothing resolved
(the contract `backend/retrieve.py` still keeps for `/api/query`). Here it
is also attempted whenever a follow-up reads as narrative-shaped --
"why", "notes", "tell me more" -- even when this turn's own entities did
resolve, because the doctor is very often asking to go deeper on something
already on screen, not asking a fresh coded question.
"""

from __future__ import annotations

import time
import uuid

from psycopg.types.json import Jsonb

from backend.brief import build_brief
from backend.data_layer import connection, get_patient
from backend.groq_client import generate_reply, groq_enabled
from backend.models import Brief, ChatResponse, NarrativeMatch, RetrievalResult
from backend.retrieve import retrieve
from backend.vector_store import VectorStore, vector_search_enabled
from retrieval.ner import parse_query

# Both roles counted -- keeps the prompt bounded on a long-running
# conversation instead of resending every turn ever had.
_HISTORY_TURN_WINDOW = 12

_NARRATIVE_CUES = (
    "why",
    "notes",
    "note say",
    "mention",
    "explain",
    "more about",
    "tell me more",
    "context",
    "elaborate",
    "detail",
)

_SYSTEM_PROMPT = (
    "You are a clinical assistant answering a doctor's follow-up questions "
    "about one patient, in an ongoing conversation. For every turn you are "
    "given the patient's structured records and any matching clinical notes "
    "retrieved for that turn -- treat that as the complete and only truth.\n\n"
    "Rules, no exceptions:\n"
    "- Never state a medication, value, date, diagnosis, or allergy that is "
    "not explicitly present in what you were given for this turn.\n"
    "- Never infer, estimate, or fill a gap the input doesn't state.\n"
    "- Preserve every specific value, date, and status exactly as given.\n"
    "- If nothing given to you answers the question, say so plainly rather "
    "than guessing.\n"
    "- Answer conversationally and concisely, the way a colleague would "
    "answer over a phone call -- not as a formal report.\n"
    "- Use the earlier turns of this conversation for context, but ground "
    "every clinical claim in what this turn's retrieval gave you."
)


class ConversationNotFoundError(Exception):
    """Raised for a conversation_id that doesn't exist, or belongs to a
    different patient -- backend.main turns this into a 404."""


def _looks_narrative(message: str) -> bool:
    lowered = message.lower()
    return any(cue in lowered for cue in _NARRATIVE_CUES)


def _load_turns(conversation_id: str) -> list[dict]:
    with connection().cursor() as cur:
        cur.execute(
            "SELECT role, text FROM conversation_turns "
            "WHERE conversation_id = %s ORDER BY turn_index",
            (conversation_id,),
        )
        return [{"role": role, "text": text} for role, text in cur.fetchall()]


def _conversation_belongs_to(conversation_id: str, patient_id: str) -> bool:
    with connection().cursor() as cur:
        cur.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = %s AND patient_id = %s",
            (conversation_id, patient_id),
        )
        return cur.fetchone() is not None


def _create_conversation(conversation_id: str, patient_id: str) -> None:
    with connection().cursor() as cur:
        cur.execute(
            "INSERT INTO conversations (conversation_id, patient_id) VALUES (%s, %s)",
            (conversation_id, patient_id),
        )


def _insert_turn(
    conversation_id: str,
    turn_index: int,
    role: str,
    text: str,
    cited_fact_ids: list[str] | None,
) -> None:
    with connection().cursor() as cur:
        cur.execute(
            "INSERT INTO conversation_turns "
            "(turn_id, conversation_id, turn_index, role, text, cited_fact_ids) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                uuid.uuid4().hex,
                conversation_id,
                turn_index,
                role,
                text,
                Jsonb(cited_fact_ids) if cited_fact_ids is not None else None,
            ),
        )
        cur.execute(
            "UPDATE conversations SET last_turn_at = now() WHERE conversation_id = %s",
            (conversation_id,),
        )


def _render_context(brief: Brief, narrative_matches: list[NarrativeMatch], include_missing: bool) -> str:
    lines = [f"Patient summary: {brief.headline}", ""]
    for section in brief.sections:
        lines.append(f"{section.title}:")
        if section.lines:
            for line in section.lines:
                fact_ids = ",".join(line.fact_ids)
                lines.append(f"- {line.text} (fact_id={fact_ids})")
        else:
            lines.append(f"- {section.empty_note}")
        lines.append("")

    if narrative_matches:
        lines.append("Matching clinical notes:")
        for match in narrative_matches:
            lines.append(f"- [{match.section}, fact_id={match.fact_id}] {match.text}")
        lines.append("")

    # Missingness is a property of the patient record at the time the
    # thread opened, not of each follow-up -- restated every turn it would
    # crowd out the actual question being asked this turn.
    if include_missing and brief.missing:
        lines.append("Not on file (pre-op checklist gaps):")
        for item in brief.missing:
            detail = (
                f"last seen {item.last_seen.date().isoformat()}"
                if item.last_seen
                else "never recorded"
            )
            lines.append(f"- {item.label} ({detail})")

    return "\n".join(lines)


def _broaden_narrative_matches(
    result: RetrievalResult, patient_id: str, message: str
) -> RetrievalResult:
    """Attempt vector search on a narrative-shaped follow-up even when
    structured entities already resolved this turn -- retrieve()'s own
    fallback only fires when nothing resolved at all, which leaves a
    request like "why was that started" with nothing to search."""
    if not result.entities or not _looks_narrative(message) or not vector_search_enabled():
        return result

    try:
        store = VectorStore()
        hits = store.search(patient_id, message)
    except Exception:
        return result

    if not hits:
        return result

    seen = {m.fact_id for m in result.narrative_matches}
    additions = [
        NarrativeMatch(
            fact_id=h.fact_id,
            encounter_id=h.encounter_id,
            date=h.date,
            section=h.section,
            text=h.text,
            score=h.score,
        )
        for h in hits
        if h.fact_id not in seen
    ]
    if not additions:
        return result
    return result.model_copy(update={"narrative_matches": result.narrative_matches + additions})


def answer(patient_id: str, message: str, conversation_id: str | None = None) -> ChatResponse:
    start = time.perf_counter()
    patient = get_patient(patient_id)  # KeyError propagates -- main.py maps it to 404

    is_new_conversation = conversation_id is None
    if is_new_conversation:
        conversation_id = uuid.uuid4().hex
        _create_conversation(conversation_id, patient_id)
        prior_turns: list[dict] = []
    else:
        if not _conversation_belongs_to(conversation_id, patient_id):
            raise ConversationNotFoundError(
                f"no conversation {conversation_id!r} for patient {patient_id!r}"
            )
        prior_turns = _load_turns(conversation_id)

    next_index = len(prior_turns)
    _insert_turn(conversation_id, next_index, "user", message, None)

    parse_start = time.perf_counter()
    parsed = parse_query(message)
    parse_ms = (time.perf_counter() - parse_start) * 1000

    result = retrieve(parsed, patient_id)
    result = _broaden_narrative_matches(result, patient_id, message)

    brief = build_brief(result, patient)
    context = _render_context(brief, result.narrative_matches, include_missing=is_new_conversation)

    llm_messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for turn in prior_turns[-_HISTORY_TURN_WINDOW:]:
        llm_messages.append({"role": turn["role"], "content": turn["text"]})
    llm_messages.append(
        {
            "role": "user",
            "content": f"Patient record for this turn:\n{context}\n\nDoctor's question: {message}",
        }
    )

    llm_start = time.perf_counter()
    answer_text = generate_reply(llm_messages) if groq_enabled() else None
    llm_ms = (time.perf_counter() - llm_start) * 1000 if groq_enabled() else None

    if answer_text is None:
        # Same degrade-to-template contract as backend/llm_polish.py: no
        # key, a timeout, a bad response -- the request still answers.
        template_lines = [line.text for section in brief.sections for line in section.lines]
        answer_text = "\n".join(template_lines) or brief.headline

    cited_fact_ids = list(dict.fromkeys(brief.fact_ids + [m.fact_id for m in result.narrative_matches]))
    _insert_turn(conversation_id, next_index + 1, "assistant", answer_text, cited_fact_ids)

    total_ms = (time.perf_counter() - start) * 1000
    result = result.model_copy(
        update={
            "timings": result.timings.model_copy(
                update={"parse_ms": parse_ms, "llm_ms": llm_ms, "total_ms": total_ms}
            )
        }
    )

    return ChatResponse(
        conversation_id=conversation_id,
        patient=patient,
        message=message,
        answer=answer_text,
        cited_fact_ids=cited_fact_ids,
        result=result,
        turn_index=next_index + 1,
        timings=result.timings,
    )
