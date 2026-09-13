"""One ABHA number, one patient. NHA M1 `TAGGING_UNIQUEPATIENTID_UNIQUEABHANUMBER`.

The only **mandatory** M1 item with no ABDM API behind it, because it is not
ABDM's job: the checklist requires the *integrator's* system to tag one ABHA
number to one unique patient ID, and to check an ABHA number is not already
tagged before creating a second patient record for it.

The failure it prevents is worth naming, because it is silent and it
compounds. A patient verifies their ABHA at reception on Monday and a record
is created. They verify again on Thursday, the desk does not recognise them,
and a second record is created against the same ABHA. Now the same human has
two clinical histories, each incomplete, and the next query against either
one returns an answer that is confidently wrong by omission -- which is
precisely the failure mode this whole project exists to surface. An
uniqueness constraint is cheap; reconciling two half-histories afterwards is
not.

So `link()` refuses rather than overwrites, in both directions:

* the ABHA number is already tagged to a different patient -- the case the
  checklist names;
* the patient is already tagged to a different ABHA number -- the mirror
  case, which the checklist does not name but which produces the same
  ambiguity from the other end. A person has exactly one ABHA number (up to
  six ABHA *addresses* hang off it, which is why addresses are normalised
  away from the key here).

Re-linking an identical pair is a no-op that succeeds, so a desk that
verifies the same patient twice in a day is not punished for it.

Storage is a JSON file, matching `backend/data_layer.py`'s file-backed
approach and its documented intent to become a one-file Postgres swap. Two
consequences are handled here rather than deferred: writes are atomic via a
temp file and replace, so a crash mid-write cannot truncate the map; and the
file is **gitignored**, because ABHA numbers are health identifiers and
committing a map of them would publish exactly the linkage this module
exists to protect.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STORE = REPO_ROOT / "data" / "abha_links.json"

# 14 digits, usually displayed as 91-1234-5678-9012.
_ABHA_DIGITS = re.compile(r"^\d{14}$")


class AbhaLinkError(RuntimeError):
    """A link was refused because it would break the one-to-one rule."""


@dataclass(frozen=True)
class AbhaLink:
    abha_number: str
    patient_id: str
    abha_address: str | None
    linked_at: str

    def as_dict(self) -> dict[str, str | None]:
        return {
            "abha_number": self.abha_number,
            "patient_id": self.patient_id,
            "abha_address": self.abha_address,
            "linked_at": self.linked_at,
        }


def normalize_abha_number(value: str) -> str:
    """Strip display formatting so `91-1234-5678-9012` and `91123456789012`
    are the same key.

    Without this the uniqueness check passes trivially: the same ABHA typed
    with and without hyphens looks like two different identifiers, and the
    duplicate-patient case this module exists to prevent sails straight
    through.
    """
    digits = re.sub(r"[\s\-]", "", value or "")
    if not _ABHA_DIGITS.match(digits):
        raise AbhaLinkError(
            f"not a 14-digit ABHA number: {value!r}. A masked value "
            f"(91-****-****-9012) cannot be linked -- it is not an identifier."
        )
    return digits


class AbhaLinkStore:
    def __init__(self, path: Path = DEFAULT_STORE) -> None:
        self.path = path
        self._lock = threading.Lock()

    # -- reads -------------------------------------------------------------

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            # A corrupt store must not silently become an empty one -- that
            # would drop every uniqueness guarantee at exactly the moment
            # they matter.
            raise AbhaLinkError(
                f"{self.path} is unreadable or not valid JSON; refusing to "
                f"continue with an empty link map"
            ) from None
        return data if isinstance(data, dict) else {}

    def patient_for(self, abha_number: str) -> str | None:
        record = self._load().get(normalize_abha_number(abha_number))
        return record.get("patient_id") if record else None

    def abha_for(self, patient_id: str) -> AbhaLink | None:
        for number, record in self._load().items():
            if record.get("patient_id") == patient_id:
                return AbhaLink(
                    abha_number=number,
                    patient_id=patient_id,
                    abha_address=record.get("abha_address"),
                    linked_at=record.get("linked_at", ""),
                )
        return None

    def all_links(self) -> list[AbhaLink]:
        return [
            AbhaLink(
                abha_number=number,
                patient_id=record.get("patient_id", ""),
                abha_address=record.get("abha_address"),
                linked_at=record.get("linked_at", ""),
            )
            for number, record in sorted(self._load().items())
        ]

    # -- writes ------------------------------------------------------------

    def link(
        self, *, abha_number: str, patient_id: str, abha_address: str | None = None
    ) -> AbhaLink:
        """Tag one ABHA number to one patient. Refuses to break a 1:1 pairing."""
        if not patient_id:
            raise AbhaLinkError("patient_id is required")
        key = normalize_abha_number(abha_number)

        with self._lock:
            links = self._load()

            existing = links.get(key)
            if existing and existing.get("patient_id") != patient_id:
                raise AbhaLinkError(
                    f"ABHA number {_mask(key)} is already tagged to patient "
                    f"{existing['patient_id']}. Creating a second patient "
                    f"record for the same ABHA splits one person's history in "
                    f"two; resolve the duplicate instead of re-tagging."
                )

            for other_key, record in links.items():
                if record.get("patient_id") == patient_id and other_key != key:
                    raise AbhaLinkError(
                        f"patient {patient_id} is already tagged to ABHA number "
                        f"{_mask(other_key)}. A person has exactly one ABHA "
                        f"number; if this is a correction, unlink first."
                    )

            if existing:
                # Same pair again -- a desk verifying the same patient twice.
                # Succeed without rewriting linked_at, so the original tagging
                # time survives as provenance.
                return AbhaLink(
                    abha_number=key,
                    patient_id=patient_id,
                    abha_address=existing.get("abha_address") or abha_address,
                    linked_at=existing.get("linked_at", ""),
                )

            link = AbhaLink(
                abha_number=key,
                patient_id=patient_id,
                abha_address=abha_address,
                linked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            links[key] = {
                "patient_id": link.patient_id,
                "abha_address": link.abha_address,
                "linked_at": link.linked_at,
            }
            self._write(links)
            return link

    def unlink(self, abha_number: str) -> bool:
        key = normalize_abha_number(abha_number)
        with self._lock:
            links = self._load()
            if key not in links:
                return False
            del links[key]
            self._write(links)
            return True

    def _write(self, links: dict[str, dict]) -> None:
        """Atomic: write a temp file in the same directory, then replace.

        A partial write here would corrupt the map, and `_load` deliberately
        refuses to continue past a corrupt map -- so a torn write would take
        the tagging feature down rather than degrade it.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=".abha_links.",
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                json.dump(links, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise


def _mask(abha_number: str) -> str:
    """Never put a full ABHA number in an error message -- these reach logs."""
    return f"{'*' * 10}{abha_number[-4:]}" if len(abha_number) >= 4 else "****"


_default_store = AbhaLinkStore()


def default_store() -> AbhaLinkStore:
    return _default_store
