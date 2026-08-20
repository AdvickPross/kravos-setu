"""backend.vector_store -- chunking (pure function, checked against a real
REPORT fact) and the store's env-var-gated construction. The actual
Pinecone SDK is never called here -- see kravos_hackathon_progress.md for
why a live call wasn't made this session; VectorStore.search/upsert_chunks
are exercised via a fake object standing in for the SDK client.
"""

from __future__ import annotations

import pytest

from backend.vector_store import (
    ReportChunk,
    VectorStore,
    chunk_report_text,
    iter_report_chunks,
    vector_search_enabled,
)

# A real REPORT fact's text, verbatim from facts.jsonl (patient
# bc2bb0d1-a0e6-c9f0-f250-32ff7934b2dd) -- not reconstructed by hand, so the
# chunker is checked against what the corpus actually contains, including
# its quirks (blank lines inside a section, a nested "## Plan" sub-header).
REAL_REPORT_TEXT = (
    "2016-03-05\n\n# Chief Complaint\nNo complaints.\n\n# History of Present "
    "Illness\nAbby752 is a 1 year-old nonhispanic white female.\n\n# Social "
    "History\n Patient has never smoked.\n\n\nPatient comes from a middle "
    "socioeconomic background.\n\nPatient currently has UnitedHealthcare.\n\n"
    "# Allergies\nNo Known Allergies.\n\n# Medications\nNo Active "
    "Medications.\n\n# Assessment and Plan\nPatient is presenting with "
    "medication review due (situation). \n\n\n## Plan"
)


@pytest.fixture(autouse=True)
def _no_pinecone_key(monkeypatch):
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)


# ------------------------------------------------------------------ chunking


def test_chunks_real_report_text_by_section():
    chunks = chunk_report_text(REAL_REPORT_TEXT)
    sections = [section for section, _ in chunks]
    assert sections == [
        "Chief Complaint",
        "History of Present Illness",
        "Social History",
        "Allergies",
        "Medications",
        "Assessment and Plan",
    ]


def test_date_line_before_first_header_is_dropped():
    chunks = chunk_report_text(REAL_REPORT_TEXT)
    all_text = " ".join(body for _, body in chunks)
    assert "2016-03-05" not in all_text


def test_nested_subheader_rolls_up_into_parent_section():
    """## Plan is a sub-header under Assessment and Plan -- the chunker only
    splits on single-# headers, so it stays inside that chunk's body."""
    chunks = dict(chunk_report_text(REAL_REPORT_TEXT))
    assert "## Plan" in chunks["Assessment and Plan"]


def test_blank_lines_within_a_section_are_preserved_in_body():
    chunks = dict(chunk_report_text(REAL_REPORT_TEXT))
    assert "UnitedHealthcare" in chunks["Social History"]


def test_text_with_no_headers_becomes_one_note_chunk():
    chunks = chunk_report_text("Just some free text, no headers at all.")
    assert chunks == [("Note", "Just some free text, no headers at all.")]


def test_empty_text_produces_no_chunks():
    assert chunk_report_text("") == []
    assert chunk_report_text("   \n  ") == []


def test_iter_report_chunks_builds_ids_and_carries_provenance():
    fact = {
        "fact_id": "abc123",
        "patient_id": "patient-1",
        "encounter_id": "enc-1",
        "date": "2016-03-05T00:34:26+05:30",
        "text": REAL_REPORT_TEXT,
    }
    chunks = list(iter_report_chunks(fact))
    assert len(chunks) == 6
    assert all(isinstance(c, ReportChunk) for c in chunks)
    assert [c.chunk_id for c in chunks] == [f"abc123-{i}" for i in range(6)]
    assert all(c.fact_id == "abc123" for c in chunks)
    assert all(c.patient_id == "patient-1" for c in chunks)
    assert all(c.encounter_id == "enc-1" for c in chunks)


# --------------------------------------------------------------- enable flag


def test_vector_search_disabled_when_key_unset():
    assert vector_search_enabled() is False


def test_vector_search_enabled_when_key_set(monkeypatch):
    monkeypatch.setenv("PINECONE_API_KEY", "test-key")
    assert vector_search_enabled() is True


def test_vector_search_disabled_for_blank_key(monkeypatch):
    monkeypatch.setenv("PINECONE_API_KEY", "   ")
    assert vector_search_enabled() is False


# -------------------------------------------------------------- VectorStore


def test_construction_without_api_key_raises():
    with pytest.raises(RuntimeError):
        VectorStore()


def test_construction_with_explicit_api_key_succeeds():
    store = VectorStore(api_key="test-key")
    assert store._api_key == "test-key"


def test_search_rejects_empty_patient_id():
    store = VectorStore(api_key="test-key")
    with pytest.raises(ValueError):
        store.search("", "any query")


class _FakeHit:
    def __init__(self, fields: dict, score: float):
        self.fields = fields
        self.score = score


class _FakeSearchResult:
    def __init__(self, hits):
        self.hits = hits


class _FakeSearchResponse:
    def __init__(self, hits):
        self.result = _FakeSearchResult(hits)


class _FakeIndex:
    def __init__(self):
        self.upsert_calls = []
        self.search_calls = []

    def upsert_records(self, *, records, namespace):
        self.upsert_calls.append({"records": records, "namespace": namespace})

    def search(self, *, namespace, top_k, inputs, filter, fields):
        self.search_calls.append(
            {"namespace": namespace, "top_k": top_k, "inputs": inputs, "filter": filter, "fields": fields}
        )
        return _FakeSearchResponse(
            [_FakeHit({"fact_id": "f1", "chunk_text": "snippet", "section": "Allergies"}, 0.9)]
        )


def test_upsert_chunks_groups_by_patient_into_separate_namespaces():
    store = VectorStore(api_key="test-key")
    fake_index = _FakeIndex()
    store._index = fake_index

    chunks = [
        ReportChunk(chunk_id="a-0", patient_id="patient-a", fact_id="a", section="X", text="t1"),
        ReportChunk(chunk_id="b-0", patient_id="patient-b", fact_id="b", section="X", text="t2"),
    ]
    store.upsert_chunks(chunks)

    namespaces = {call["namespace"] for call in fake_index.upsert_calls}
    assert namespaces == {"patient-a", "patient-b"}
    for call in fake_index.upsert_calls:
        assert len(call["records"]) == 1


def test_upsert_records_include_patient_id_for_defense_in_depth_filter():
    store = VectorStore(api_key="test-key")
    fake_index = _FakeIndex()
    store._index = fake_index

    store.upsert_chunks(
        [ReportChunk(chunk_id="a-0", patient_id="patient-a", fact_id="a", section="X", text="t1")]
    )
    record = fake_index.upsert_calls[0]["records"][0]
    assert record["patient_id"] == "patient-a"
    assert record["_id"] == "a-0"
    assert record["chunk_text"] == "t1"


def test_search_always_scopes_by_namespace_and_filter():
    store = VectorStore(api_key="test-key")
    fake_index = _FakeIndex()
    store._index = fake_index

    matches = store.search("patient-a", "any allergies?", top_k=3)

    call = fake_index.search_calls[0]
    assert call["namespace"] == "patient-a"
    assert call["filter"] == {"patient_id": "patient-a"}
    assert call["top_k"] == 3
    assert len(matches) == 1
    assert matches[0].fact_id == "f1"
    assert matches[0].score == 0.9
