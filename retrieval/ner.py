"""The query-side resolver.

Turns a doctor's question into a :class:`ParsedQuery`: resolved entities
carrying code sets, match quality and a negation flag, plus a date window.

Rules and dictionary lookup only -- no model, no embeddings. The corpus
vocabulary is ~1,170 codes and fits in a dict.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from retrieval.lexicon import Hit, Lexicon
from retrieval.models import MatchQuality, ParsedQuery, ResolvedEntity
from retrieval.normalize import normalize_query_text

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CUES_PATH = _REPO_ROOT / "data" / "query_cues.json"

# A doctor does not type a 51-token questionnaire title; capping the span
# width keeps matching linear-ish without losing anything real.
MAX_SPAN_TOKENS = 8

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+/.'’-]*")


@dataclass(frozen=True)
class Token:
    raw: str
    norm: str
    start: int
    end: int


@lru_cache(maxsize=4)
def _load_cues(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class QueryNER:
    """Resolves query text against the lexicon index.

    Fuzzy matching is deliberately last-resort: the downstream router
    branches on match quality, so an honest UNRESOLVED beats a
    plausible-looking wrong code.
    """

    def __init__(
        self,
        lexicon: Lexicon | None = None,
        cues_path: str | Path = DEFAULT_CUES_PATH,
        max_span_tokens: int = MAX_SPAN_TOKENS,
        fuzzy_cutoff: float = 0.86,
        fuzzy_min_length: int = 6,
    ) -> None:
        self.lexicon = lexicon or Lexicon()
        self.cues = _load_cues(str(cues_path))
        self.max_span_tokens = max_span_tokens
        self.fuzzy_cutoff = fuzzy_cutoff
        self.fuzzy_min_length = fuzzy_min_length

        self.stopwords = frozenset(self.cues.get("stopwords", []))
        negation = self.cues.get("negation", {})
        self.negation_cues = tuple(negation.get("cues", ()))
        self.negation_window = int(negation.get("window_tokens", 4))
        self.negation_blockers = frozenset(negation.get("blockers", ()))
        self.self_negating = tuple(negation.get("self_negating", ()))
        self._dates = self.cues.get("dates", {})

        # Fuzzy candidates: single-token forms only. Matching "amoxicilin"
        # against a 40-token questionnaire title helps nobody.
        self._fuzzy_candidates = [f for f in self.lexicon.forms if " " not in f and len(f) >= 4]

    # ------------------------------------------------------------------ API

    def parse(self, query: str, today: date | None = None) -> ParsedQuery:
        today = today or date.today()
        tokens = self._tokenize(query)
        parsed = ParsedQuery(raw=query)
        if not tokens:
            return parsed

        joined, offsets = _join(tokens)
        date_from, date_to, date_tokens = self._parse_dates(joined, offsets, today)
        parsed.date_from = date_from
        parsed.date_to = date_to

        consumed: set[int] = set(date_tokens)
        entities: list[ResolvedEntity] = []

        index = 0
        while index < len(tokens):
            if index in consumed:
                index += 1
                continue
            width = self._match_at(tokens, index, consumed, query, entities)
            if width:
                consumed.update(range(index, index + width))
                index += width
            else:
                index += 1

        entities.extend(self._leftovers(tokens, consumed, query))
        entities.sort(key=lambda e: (e.start, e.end))
        parsed.entities = entities
        return parsed

    # ------------------------------------------------------------ matching

    def _match_at(
        self,
        tokens: list[Token],
        index: int,
        consumed: set[int],
        raw: str,
        out: list[ResolvedEntity],
    ) -> int:
        """Longest span first, so `blood pressure` wins over `pressure` and
        `cholesterol in hdl` wins over `cholesterol`."""
        widest = min(self.max_span_tokens, len(tokens) - index)
        for width in range(widest, 0, -1):
            span_indices = range(index, index + width)
            if any(i in consumed for i in span_indices):
                continue
            key = " ".join(tokens[i].norm for i in span_indices)
            hits = self.lexicon.lookup(key)
            if hits:
                out.extend(self._entities(tokens, index, index + width, hits, raw))
                return width
        return 0

    def _entities(
        self,
        tokens: list[Token],
        start_token: int,
        end_token: int,
        hits: list[Hit],
        raw: str,
    ) -> list[ResolvedEntity]:
        """One entity per (category, code_system) group.

        `aspirin` is both an ALLERGY substance and four MEDICATION products;
        collapsing those into one entity would force a lie into either
        `category` or `codes`.
        """
        start = tokens[start_token].start
        end = tokens[end_token - 1].end
        span_text = " ".join(t.norm for t in tokens[start_token:end_token])
        negated = span_text in self.self_negating or self._is_negated(tokens, start_token)

        groups: dict[tuple[str | None, str | None], list[Hit]] = {}
        for hit in hits:
            groups.setdefault(hit.entry.group, []).append(hit)

        entities: list[ResolvedEntity] = []
        for (category, code_system), group in groups.items():
            codes: list[str] = []
            for hit in group:
                if hit.entry.code not in codes:
                    codes.append(hit.entry.code)
            quality = (
                MatchQuality.EXACT
                if any(h.quality is MatchQuality.EXACT for h in group)
                else MatchQuality.SYNONYM
            )
            concepts = {h.entry.concept for h in group if h.entry.concept}
            if len(codes) == 1:
                display = group[0].entry.display
            elif len(concepts) == 1:
                display = next(iter(concepts))
            else:
                display = raw[start:end].lower()
            entities.append(
                ResolvedEntity(
                    span=raw[start:end],
                    start=start,
                    end=end,
                    category=category,
                    code_system=code_system,
                    codes=codes,
                    display=display,
                    quality=quality,
                    negated=negated,
                )
            )
        return entities

    # ------------------------------------------------------------ negation

    def _is_negated(self, tokens: list[Token], start_token: int) -> bool:
        """Scan a few tokens before the entity for a negation cue."""
        window_start = max(0, start_token - self.negation_window)
        window = [t.norm for t in tokens[window_start:start_token]]
        if not window:
            return False
        # "on warfarin but no aspirin": the blocker resets the window
        for position in range(len(window) - 1, -1, -1):
            if window[position] in self.negation_blockers:
                window = window[position + 1 :]
                break
        text = " ".join(window)
        for cue in self.negation_cues:
            if re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", text):
                return True
        return False

    # ---------------------------------------------------------------- dates

    def _parse_dates(
        self, joined: str, offsets: list[int], today: date
    ) -> tuple[date | None, date | None, set[int]]:
        date_from: date | None = None
        date_to: date | None = None
        touched: set[int] = set()

        def mark(match: re.Match[str]) -> None:
            for index, offset in enumerate(offsets):
                if match.start() <= offset < match.end():
                    touched.add(index)

        numbers = self._dates.get("number_words", {})
        units = self._dates.get("units", {})
        months = self._dates.get("months", {})
        recent_months = int(self._dates.get("recent_months", 6))

        number_alt = "|".join(sorted(numbers, key=len, reverse=True))
        unit_alt = "|".join(sorted(units, key=len, reverse=True))
        month_alt = "|".join(sorted(months, key=len, reverse=True))

        # "last 3 months", "over the past year", "in the last couple of weeks"
        relative = re.compile(
            rf"\b(?:last|past|previous|prior)\s+(?:(\d+|{number_alt})\s+)?(?:of\s+)?({unit_alt})\b"
        )
        for match in relative.finditer(joined):
            count_text, unit = match.group(1), match.group(2)
            count = 1
            if count_text:
                count = int(count_text) if count_text.isdigit() else int(numbers[count_text])
            days = int(units[unit]) * count
            candidate = today - timedelta(days=days)
            date_from = min(date_from, candidate) if date_from else candidate
            date_to = date_to or today
            mark(match)

        # "since 2019", "since March 2020"
        since = re.compile(rf"\bsince\s+(?:({month_alt})\s+)?((?:19|20)\d{{2}})\b")
        for match in since.finditer(joined):
            month = months.get(match.group(1) or "", 1)
            candidate = date(int(match.group(2)), int(month), 1)
            date_from = min(date_from, candidate) if date_from else candidate
            date_to = date_to or today
            mark(match)

        # "in 2021", "during 2021"
        year_only = re.compile(r"\b(?:in|during|for)\s+((?:19|20)\d{2})\b")
        for match in year_only.finditer(joined):
            year = int(match.group(1))
            candidate_from, candidate_to = date(year, 1, 1), date(year, 12, 31)
            date_from = min(date_from, candidate_from) if date_from else candidate_from
            date_to = max(date_to, candidate_to) if date_to else candidate_to
            mark(match)

        # "this year", "this month"
        if match := re.search(r"\bthis\s+year\b", joined):
            candidate = date(today.year, 1, 1)
            date_from = min(date_from, candidate) if date_from else candidate
            date_to = date_to or today
            mark(match)
        if match := re.search(r"\bthis\s+month\b", joined):
            candidate = date(today.year, today.month, 1)
            date_from = min(date_from, candidate) if date_from else candidate
            date_to = date_to or today
            mark(match)

        # bare "recent" / "recently"
        if date_from is None and (match := re.search(r"\brecent(?:ly)?\b", joined)):
            candidate = today - timedelta(days=30 * recent_months)
            date_from = candidate
            date_to = today
            mark(match)

        # "before surgery" / "pre-op": an upper bound, not a window
        for cue in self._dates.get("upper_bound_cues", ()):
            for match in re.finditer(rf"(?<!\w){re.escape(cue)}(?!\w)", joined):
                date_to = date_to or today
                mark(match)

        return date_from, date_to, touched

    # ----------------------------------------------------- fuzzy / leftover

    def _leftovers(
        self, tokens: list[Token], consumed: set[int], raw: str
    ) -> list[ResolvedEntity]:
        """Unmatched clinical-looking tokens: fuzzy match, else UNRESOLVED.

        Adjacent leftovers merge into one span ("cardiac stress echo" is one
        thing the router failed to resolve, not three)."""
        entities: list[ResolvedEntity] = []
        run: list[int] = []

        def flush() -> None:
            if not run:
                return
            first, last = run[0], run[-1]
            start, end = tokens[first].start, tokens[last].end
            if len(run) == 1:
                form, fuzzy = self._fuzzy(tokens[first].norm)
                if fuzzy:
                    resolved = self._entities(tokens, first, last + 1, fuzzy, raw)
                    for entity in resolved:
                        entity.quality = MatchQuality.FUZZY
                        if len(entity.codes) > 1:
                            # report what we matched, not what was typed
                            entity.display = form
                    entities.extend(resolved)
                    run.clear()
                    return
            entities.append(
                ResolvedEntity(
                    span=raw[start:end],
                    start=start,
                    end=end,
                    quality=MatchQuality.UNRESOLVED,
                    negated=self._is_negated(tokens, first),
                )
            )
            run.clear()

        for index, token in enumerate(tokens):
            if index in consumed or not self._looks_clinical(token):
                flush()
                continue
            if run and run[-1] != index - 1:
                flush()
            run.append(index)
        flush()
        return entities

    def _looks_clinical(self, token: Token) -> bool:
        norm = token.norm
        if len(norm) < 3 or norm in self.stopwords:
            return False
        return not norm.replace(".", "").isdigit()

    def _fuzzy(self, norm: str) -> tuple[str | None, list[Hit]]:
        """Conservative edit-distance fallback: one clear winner or nothing."""
        if len(norm) < self.fuzzy_min_length:
            return None, []
        candidates = difflib.get_close_matches(
            norm, self._fuzzy_candidates, n=3, cutoff=self.fuzzy_cutoff
        )
        if not candidates:
            return None, []
        best = candidates[0]
        best_ratio = difflib.SequenceMatcher(None, norm, best).ratio()
        if len(candidates) > 1:
            runner_up = difflib.SequenceMatcher(None, norm, candidates[1]).ratio()
            if best_ratio - runner_up < 0.03:  # ambiguous -> stay honest
                return None, []
        return best, self.lexicon.lookup(best)

    # ------------------------------------------------------------ tokenize

    def _tokenize(self, query: str) -> list[Token]:
        tokens: list[Token] = []
        for match in _TOKEN_RE.finditer(query or ""):
            norm = normalize_query_text(match.group(0))
            if not norm:
                continue
            # normalize_query_text can split a raw token ("bp/hr"); keep the
            # whole raw span but index on the normalized text
            tokens.append(
                Token(raw=match.group(0), norm=norm, start=match.start(), end=match.end())
            )
        return tokens


def _join(tokens: list[Token]) -> tuple[str, list[int]]:
    """Normalized token text as one string, plus each token's offset in it."""
    offsets: list[int] = []
    position = 0
    for token in tokens:
        offsets.append(position)
        position += len(token.norm) + 1
    return " ".join(t.norm for t in tokens), offsets


@lru_cache(maxsize=1)
def _default_ner() -> QueryNER:
    return QueryNER()


def parse_query(query: str, today: date | None = None) -> ParsedQuery:
    """Convenience entry point using the default lexicon and cue files."""
    return _default_ner().parse(query, today=today)
