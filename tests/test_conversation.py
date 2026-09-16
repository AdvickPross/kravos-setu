"""backend.conversation -- the chat orchestrator behind POST /api/chat.

Isolated from the real corpus and from Groq/Pinecone the same way the rest
of the suite is: kravos_test for storage (see tests/conftest.py), and
generate_reply / VectorStore mocked so no unit test makes a real network
call.
"""

from __future__ import annotations

import pytest

from backend import conversation
from backend.data_layer import connection
# Imported as a bare module, not as `tests.conftest`. A third-party
# package named `tests` is installed in site-packages on some machines,
# and being a real package it shadows this directory -- so the dotted
# form resolves to the wrong `tests` and finds no conftest. pytest puts
# this directory on sys.path, which is also how `abha_support` is
# imported by the ABHA tests.
from conftest import insert_facts, insert_patients

PATIENT = "patient-1"
OTHER_PATIENT = "patient-2"


def _fact(fact_id: str, **overrides) -> dict:
    base = {
        "fact_id": fact_id,
        "patient_id": PATIENT,
        "category": "MEDICATION",
        "safety_class": "CLINICAL",
        "text": "text",
        "code_system": "http://www.nlm.nih.gov/research/umls/rxnorm",
        "code": "999",
        "display": "Lisinopril 10 MG",
        "value": None,
        "unit": None,
        "date": "2025-01-01T00:00:00+05:30",
        "status": "active",
        "source_resource_type": "MedicationRequest",
        "source_resource_id": "res-1",
        "encounter_id": "enc-1",
        "also_in": [],
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _isolated_db(use_test_db):
    pass


@pytest.fixture(autouse=True)
def _seed_patients(_isolated_db):
    insert_patients(
        [
            {"patient_id": PATIENT, "name": "Jane Doe", "birth_date": "1990-01-01", "gender": "female", "_n_facts": 1},
            {"patient_id": OTHER_PATIENT, "name": "Other Patient", "birth_date": "1980-01-01", "gender": "male", "_n_facts": 0},
        ]
    )


@pytest.fixture(autouse=True)
def _no_vector_search(monkeypatch):
    monkeypatch.setattr(conversation, "vector_search_enabled", lambda: False)


@pytest.fixture
def fake_reply(monkeypatch):
    """generate_reply mocked to a fixed string, capturing every call's
    message list so tests can assert on what the LLM was actually shown."""
    calls: list[list[dict]] = []

    def _fake(messages, **kwargs):
        calls.append(messages)
        return "a mocked conversational answer"

    monkeypatch.setattr(conversation, "generate_reply", _fake)
    monkeypatch.setattr(conversation, "groq_enabled", lambda: True)
    return calls


def _turn_rows(conversation_id: str) -> list[tuple]:
    with connection().cursor() as cur:
        cur.execute(
            "SELECT turn_index, role, text FROM conversation_turns "
            "WHERE conversation_id = %s ORDER BY turn_index",
            (conversation_id,),
        )
        return cur.fetchall()


# -------------------------------------------------------------- new conversation


def test_new_conversation_creates_two_turns_and_returns_answer(fake_reply):
    insert_facts([_fact("f1")])

    response = conversation.answer(PATIENT, "what medications is she on?")

    assert response.conversation_id
    assert response.turn_index == 1
    assert response.answer == "a mocked conversational answer"
    assert "f1" in response.cited_fact_ids

    rows = _turn_rows(response.conversation_id)
    assert [r[1] for r in rows] == ["user", "assistant"]
    assert rows[0][2] == "what medications is she on?"
    assert rows[1][2] == "a mocked conversational answer"


def test_unknown_patient_raises_keyerror(fake_reply):
    with pytest.raises(KeyError):
        conversation.answer("no-such-patient", "hi")


# --------------------------------------------------------- continuing a thread


def test_continuing_conversation_carries_prior_turns_into_the_prompt(fake_reply):
    insert_facts([_fact("f1")])

    first = conversation.answer(PATIENT, "what medications is she on?")
    second = conversation.answer(
        PATIENT, "and what about her allergies?", conversation_id=first.conversation_id
    )

    assert second.turn_index == 3  # user=2, assistant=3
    second_call_messages = fake_reply[1]
    joined = " ".join(m["content"] for m in second_call_messages)
    assert "what medications is she on?" in joined
    assert "a mocked conversational answer" in joined


def test_unknown_conversation_id_raises_not_found(fake_reply):
    with pytest.raises(conversation.ConversationNotFoundError):
        conversation.answer(PATIENT, "hi", conversation_id="bogus")


def test_conversation_id_is_scoped_to_its_own_patient(fake_reply):
    first = conversation.answer(PATIENT, "hi")
    with pytest.raises(conversation.ConversationNotFoundError):
        conversation.answer(OTHER_PATIENT, "hi", conversation_id=first.conversation_id)


# ------------------------------------------------------------ missingness scope


def test_missingness_included_only_on_first_turn(fake_reply):
    first = conversation.answer(PATIENT, "full picture please")
    conversation.answer(PATIENT, "anything else?", conversation_id=first.conversation_id)

    first_context = fake_reply[0][-1]["content"]
    second_context = fake_reply[1][-1]["content"]
    assert "Not on file" in first_context
    assert "Not on file" not in second_context


# --------------------------------------------------------------- LLM degradation


def test_degrades_to_template_text_when_groq_disabled():
    """No fake_reply fixture here -- groq_enabled() is genuinely off
    (GROQ_API_KEY unset in the test environment), so this exercises the
    real degrade-to-template path, same contract as llm_polish.py."""
    insert_facts([_fact("f1")])
    response = conversation.answer(PATIENT, "what medications is she on?")
    assert response.answer  # non-empty; never crashes, never None
    assert "Lisinopril" in response.answer  # came straight from the grounded template


# ------------------------------------------------------------------ turn shaping


@pytest.mark.parametrize(
    "message,expected",
    [
        ("why was that started?", True),
        ("any notes about her history?", True),
        ("tell me more about the last visit", True),
        ("what is her blood pressure?", False),
        ("any allergies?", False),
    ],
)
def test_looks_narrative_cue_detection(message, expected):
    assert conversation._looks_narrative(message) is expected


def test_broaden_narrative_matches_skipped_when_nothing_resolved(monkeypatch):
    """No entities at all -- retrieve()'s own fallback is the one that
    should handle this, not the broadening added here."""
    from backend.models import RetrievalResult

    monkeypatch.setattr(conversation, "vector_search_enabled", lambda: True)

    def _boom(*a, **k):
        raise AssertionError("VectorStore should not be constructed with no resolved entities")

    monkeypatch.setattr(conversation, "VectorStore", _boom)

    result = RetrievalResult(patient_id=PATIENT, query="why", entities=[])
    out = conversation._broaden_narrative_matches(result, PATIENT, "why did that happen")
    assert out is result


def test_broaden_narrative_matches_merges_hits_without_duplicates(monkeypatch):
    from backend.models import EntityFacts, NarrativeMatch, RetrievalOutcome, RetrievalResult
    from retrieval.models import MatchQuality, ResolvedEntity

    monkeypatch.setattr(conversation, "vector_search_enabled", lambda: True)

    class _Hit:
        def __init__(self, fact_id):
            self.fact_id = fact_id
            self.encounter_id = "enc-1"
            self.date = None
            self.section = "Assessment and Plan"
            self.text = "some narrative text"
            self.score = 0.5

    class _FakeStore:
        def __init__(self, *a, **k):
            pass

        def search(self, patient_id, query_text, top_k=5):
            return [_Hit("new-1"), _Hit("already-there")]

    monkeypatch.setattr(conversation, "VectorStore", _FakeStore)

    entity = ResolvedEntity(
        span="lisinopril", start=0, end=10, category="MEDICATION",
        code_system="rxnorm", codes=["999"], display="lisinopril",
        quality=MatchQuality.EXACT, negated=False,
    )
    result = RetrievalResult(
        patient_id=PATIENT,
        query="why is she on lisinopril",
        entities=[EntityFacts(entity=entity, facts=[], outcome=RetrievalOutcome.FOUND_NONE)],
        narrative_matches=[
            NarrativeMatch(fact_id="already-there", section="Note", text="x", score=0.1)
        ],
    )

    out = conversation._broaden_narrative_matches(result, PATIENT, "why is she on lisinopril")

    assert {m.fact_id for m in out.narrative_matches} == {"already-there", "new-1"}
