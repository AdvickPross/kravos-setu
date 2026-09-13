"""ABHA-to-patient tagging. NHA M1 TAGGING_UNIQUEPATIENTID_UNIQUEABHANUMBER.

The mandatory item with no ABDM API behind it. What is being protected is a
clinical failure, not a data-hygiene preference: two patient records against
one ABHA means one person with two partial histories, and a query against
either returns an answer that is wrong by omission.
"""

from __future__ import annotations

import json

import pytest

from backend.abha_link import AbhaLinkError, AbhaLinkStore, normalize_abha_number

ABHA = "91-1234-5678-9012"
ABHA_DIGITS = "911234567890 12".replace(" ", "")


@pytest.fixture
def store(tmp_path) -> AbhaLinkStore:
    return AbhaLinkStore(tmp_path / "links.json")


# --------------------------------------------------------------------------
# the uniqueness rule
# --------------------------------------------------------------------------


def test_one_abha_cannot_be_tagged_to_two_patients(store: AbhaLinkStore):
    """The case the checklist names, and the one that splits a history."""
    store.link(abha_number=ABHA, patient_id="patient-1")
    with pytest.raises(AbhaLinkError) as exc:
        store.link(abha_number=ABHA, patient_id="patient-2")
    assert "already tagged" in str(exc.value)
    assert store.patient_for(ABHA) == "patient-1", "the first link must survive"


def test_one_patient_cannot_be_tagged_to_two_abhas(store: AbhaLinkStore):
    """The mirror case. The checklist does not name it, but it produces the
    same ambiguity from the other end -- a person has one ABHA number."""
    store.link(abha_number=ABHA, patient_id="patient-1")
    with pytest.raises(AbhaLinkError):
        store.link(abha_number="91-9999-8888-7777", patient_id="patient-1")


def test_relinking_the_same_pair_succeeds(store: AbhaLinkStore):
    """A desk verifying the same patient twice in a day is not an error."""
    first = store.link(abha_number=ABHA, patient_id="patient-1")
    again = store.link(abha_number=ABHA, patient_id="patient-1")
    assert again.patient_id == "patient-1"
    assert again.linked_at == first.linked_at, "original tagging time is provenance"


def test_formatting_differences_are_the_same_abha(store: AbhaLinkStore):
    """Without normalisation the uniqueness check passes trivially: the same
    ABHA typed with and without hyphens looks like two identifiers, and the
    duplicate-patient case sails straight through."""
    store.link(abha_number=ABHA, patient_id="patient-1")
    with pytest.raises(AbhaLinkError):
        store.link(abha_number=ABHA.replace("-", ""), patient_id="patient-2")


def test_normalisation_strips_hyphens_and_spaces():
    assert normalize_abha_number("91-1234-5678-9012") == "91123456789012"
    assert normalize_abha_number(" 91 1234 5678 9012 ") == "91123456789012"


@pytest.mark.parametrize("bad", ["91-****-****-9012", "12345", "", "abcdefghijklmn"])
def test_a_masked_or_malformed_number_is_refused(store: AbhaLinkStore, bad: str):
    """ABDM masks ABHA numbers in most responses. A masked value is not an
    identifier, and tagging one would create a link that matches nothing."""
    with pytest.raises(AbhaLinkError):
        store.link(abha_number=bad, patient_id="patient-1")


def test_patient_id_is_required(store: AbhaLinkStore):
    with pytest.raises(AbhaLinkError):
        store.link(abha_number=ABHA, patient_id="")


# --------------------------------------------------------------------------
# lookups
# --------------------------------------------------------------------------


def test_lookup_before_creating_a_patient_record(store: AbhaLinkStore):
    """The checklist's actual ask: check before creating a new patient id."""
    assert store.patient_for(ABHA) is None
    store.link(abha_number=ABHA, patient_id="patient-1")
    assert store.patient_for(ABHA) == "patient-1"


def test_reverse_lookup_carries_the_address(store: AbhaLinkStore):
    store.link(abha_number=ABHA, patient_id="patient-1", abha_address="p1@sbx")
    link = store.abha_for("patient-1")
    assert link is not None
    assert link.abha_number == "91123456789012"
    assert link.abha_address == "p1@sbx"


def test_unknown_patient_has_no_link(store: AbhaLinkStore):
    assert store.abha_for("nobody") is None


def test_unlink_frees_both_sides(store: AbhaLinkStore):
    store.link(abha_number=ABHA, patient_id="patient-1")
    assert store.unlink(ABHA) is True
    assert store.patient_for(ABHA) is None
    store.link(abha_number=ABHA, patient_id="patient-2")


def test_unlinking_something_absent_is_false_not_an_error(store: AbhaLinkStore):
    assert store.unlink(ABHA) is False


# --------------------------------------------------------------------------
# durability
# --------------------------------------------------------------------------


def test_links_survive_a_new_store_over_the_same_file(tmp_path):
    path = tmp_path / "links.json"
    AbhaLinkStore(path).link(abha_number=ABHA, patient_id="patient-1")
    assert AbhaLinkStore(path).patient_for(ABHA) == "patient-1"


def test_a_corrupt_store_refuses_rather_than_reading_as_empty(tmp_path):
    """An empty map silently drops every uniqueness guarantee at exactly the
    moment it matters. Failing loudly is the safer answer."""
    path = tmp_path / "links.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(AbhaLinkError):
        AbhaLinkStore(path).patient_for(ABHA)


def test_error_messages_never_carry_a_full_abha_number(store: AbhaLinkStore):
    """These reach logs."""
    store.link(abha_number=ABHA, patient_id="patient-1")
    with pytest.raises(AbhaLinkError) as exc:
        store.link(abha_number=ABHA, patient_id="patient-2")
    assert "91123456789012" not in str(exc.value)
    assert "9012" in str(exc.value), "the last four are kept so it is identifiable"


def test_stored_file_is_valid_json(store: AbhaLinkStore):
    store.link(abha_number=ABHA, patient_id="patient-1")
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data["91123456789012"]["patient_id"] == "patient-1"


def test_the_link_map_is_gitignored():
    """ABHA numbers are health identifiers; committing the map would publish
    exactly the linkage this module exists to protect."""
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["git", "check-ignore", "data/abha_links.json"],
        cwd=repo,
        capture_output=True,
        text=True,
        # pytest replaces stdin with a closed object; without this, Windows
        # fails to duplicate the handle and the test errors for a reason that
        # has nothing to do with gitignore.
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, "data/abha_links.json is not gitignored"
