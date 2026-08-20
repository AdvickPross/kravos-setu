"""Surface-form index over the corpus vocabulary.

Loads ``lexicon.csv`` (generated from the corpus) and ``data/synonyms.csv``
(hand-authored concepts) and builds an in-memory map:

    surface form -> list of (category, code_system, code, display)

Multiple codes per surface form is normal, not an error: ``glucose`` is 4
LOINC codes, ``oxycodone`` is 4 RxNorm products.

Never touches facts.jsonl.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from retrieval.models import MatchQuality
from retrieval.normalize import (
    NormalizationRules,
    load_rules,
    normalize_display,
    normalize_query_text,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEXICON_PATH = _REPO_ROOT / "lexicon.csv"
DEFAULT_SYNONYMS_PATH = _REPO_ROOT / "data" / "synonyms.csv"

log = logging.getLogger("kravos.lexicon")


@dataclass(frozen=True)
class LexiconEntry:
    """One code, as the index knows it."""

    category: str | None
    code_system: str | None
    code: str
    display: str
    concept: str | None = None  # set when the form came from synonyms.csv

    @property
    def group(self) -> tuple[str | None, str | None]:
        return self.category, self.code_system


@dataclass(frozen=True)
class Hit:
    """An index entry plus how the surface form reached it."""

    entry: LexiconEntry
    quality: MatchQuality


class Lexicon:
    """Surface-form index. File paths are constructor arguments on purpose:
    the lexicon is regenerated whenever the corpus changes."""

    def __init__(
        self,
        lexicon_path: str | Path = DEFAULT_LEXICON_PATH,
        synonyms_path: str | Path | None = DEFAULT_SYNONYMS_PATH,
        rules_path: str | Path | None = None,
    ) -> None:
        self.lexicon_path = Path(lexicon_path)
        self.synonyms_path = Path(synonyms_path) if synonyms_path else None
        self.rules_path = rules_path
        self.reload()

    # ------------------------------------------------------------------ load

    def reload(self) -> None:
        """(Re)read both files and rebuild the index from scratch."""
        self.rules: NormalizationRules = load_rules(self.rules_path)
        self._index: dict[str, list[Hit]] = defaultdict(list)
        self._by_code: dict[tuple[str | None, str], LexiconEntry] = {}

        # Diagnostics, surfaced rather than swallowed.
        self.duplicate_displays: list[str] = []
        self.unknown_synonym_codes: list[str] = []
        self.rows = 0
        self.concepts = 0

        self._load_lexicon()
        if self.synonyms_path and self.synonyms_path.exists():
            self._load_synonyms()

    def _load_lexicon(self) -> None:
        seen_display: dict[str, set[tuple[str | None, str]]] = defaultdict(set)

        with self.lexicon_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                entry = LexiconEntry(
                    category=row["category"] or None,
                    code_system=row["code_system"] or None,
                    code=row["code"],
                    display=row["display"],
                )
                self.rows += 1
                self._by_code.setdefault((entry.code_system, entry.code), entry)

                normalized = normalize_display(entry.code_system, entry.display, self.rules)
                for form in normalized.forms:
                    self._add(form, entry, MatchQuality.EXACT)

                # Every indexed form, not just `full`. Keying this on `full`
                # alone reported zero collisions forever: two rows only share
                # a `full` display if their raw displays are identical, which
                # they never are. The collisions that matter happen on the
                # *derived* forms -- "Hemoglobin [Mass/volume] in Blood" and
                # "Hemoglobin ... in Urine by Test strip" both reduce to
                # "hemoglobin" once normalize_loinc() strips the specimen, so
                # one word reaches a blood analyte and a urine dipstick.
                # That is exactly what this diagnostic was written to catch
                # and exactly what it was silently missing.
                for form in normalized.forms:
                    seen_display[form].add((entry.code_system, entry.code))

        # "log any case where two entries normalize identically but have
        # different codes" -- in this corpus the casing variants
        # (medroxyPROGESTERone / medroxyprogesterone) all share one code, so
        # anything reported here is a genuine collision worth looking at.
        # Most of these are intended and documented: `acetaminophen` reaching
        # 10 RxNorm products is the ingredient rollup working, and multi-code
        # lab analytes are established behaviour. The subset worth a human
        # look is cross-specimen -- one word reaching a blood analyte and a
        # urine dipstick -- which this cannot tell apart on its own, so the
        # full list is recorded for inspection and only a summary is warned.
        # Per-collision warnings would put ~100 lines in the log every time
        # the lexicon loads, which is how a diagnostic gets muted for good.
        for display, codes in sorted(seen_display.items()):
            if len(codes) > 1:
                message = f"{display!r} normalizes from {len(codes)} distinct codes: {sorted(c for _, c in codes)}"
                self.duplicate_displays.append(message)
                log.debug("display collision: %s", message)
        if self.duplicate_displays:
            log.warning(
                "%d surface forms reach more than one code; see Lexicon.duplicate_displays",
                len(self.duplicate_displays),
            )

    def _load_synonyms(self) -> None:
        with self.synonyms_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                concept = (row.get("concept") or "").strip()
                if not concept or concept.startswith("#"):
                    continue
                self.concepts += 1
                category = (row.get("category") or "").strip() or None
                code_system = (row.get("code_system") or "").strip() or None
                codes = [c.strip() for c in (row.get("codes") or "").split("|") if c.strip()]
                forms = [f.strip() for f in (row.get("surface_forms") or "").split("|") if f.strip()]

                # The concept's own name is a surface form. Every author of
                # synonyms.csv listed only the *alternatives* and assumed the
                # canonical name was implied -- it wasn't, and six concepts
                # were silently broken by the omission (measured by the gold
                # eval set, not guessed): `hypertension` and `nt-probnp`
                # matched nothing at all, while `blood glucose`, `hemoglobin`
                # and `leukocytes` fell through to the raw lexicon display and
                # picked up their urine-dipstick codes. Adding it here rather
                # than in the CSV makes the whole file safe by construction,
                # so the next concept row cannot reintroduce the bug.
                # _add() dedupes on code identity, so a row that already
                # repeats its own name costs nothing.
                if concept not in forms:
                    forms.append(concept)

                for code in codes:
                    known = self._by_code.get((code_system, code))
                    if known is None:
                        self.unknown_synonym_codes.append(f"{concept}: {code_system} {code}")
                        log.warning(
                            "synonym %r points at %s %s, which is not in %s",
                            concept,
                            code_system,
                            code,
                            self.lexicon_path.name,
                        )
                        continue
                    entry = LexiconEntry(
                        category=category or known.category,
                        code_system=code_system or known.code_system,
                        code=code,
                        display=known.display,
                        concept=concept,
                    )
                    for form in forms:
                        self._add(form, entry, MatchQuality.SYNONYM)

    def _add(self, form: str, entry: LexiconEntry, quality: MatchQuality) -> None:
        key = normalize_query_text(form)
        if not key:
            return
        bucket = self._index[key]
        # Dedupe on code identity, not on the entry: Synthea emits the same
        # code under two casings ("Aspirin"/"aspirin 81 MG Oral Tablet"), and
        # a form must not yield the same code twice.
        identity = (entry.category, entry.code_system, entry.code)
        if any((h.entry.category, h.entry.code_system, h.entry.code) == identity for h in bucket):
            return
        bucket.append(Hit(entry=entry, quality=quality))

    # ----------------------------------------------------------------- query

    def lookup(self, form: str) -> list[Hit]:
        """Hits for an already-normalized surface form (empty list if none)."""
        return list(self._index.get(form, ()))

    def __contains__(self, form: str) -> bool:
        return form in self._index

    @property
    def forms(self) -> list[str]:
        return list(self._index)

    @property
    def max_form_tokens(self) -> int:
        """Longest indexed form, in tokens -- the widest span worth trying."""
        return max((len(f.split(" ")) for f in self._index), default=1)

    def entry_for(self, code_system: str | None, code: str) -> LexiconEntry | None:
        return self._by_code.get((code_system, code))

    def stats(self) -> dict[str, int]:
        return {
            "lexicon_rows": self.rows,
            "surface_forms": len(self._index),
            "codes": len(self._by_code),
            "concepts": self.concepts,
            "display_collisions": len(self.duplicate_displays),
            "unknown_synonym_codes": len(self.unknown_synonym_codes),
        }
