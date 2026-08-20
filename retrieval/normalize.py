"""Per-code-system display normalization.

One normalizer per code system, each independently testable. When real
ABDM data arrives with different vocabulary conventions, this file (plus
``data/normalization.json``) should be the only thing that changes.

The public entry point is :func:`surface_forms`, which turns one lexicon
row into the set of strings the index should key on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES_PATH = _REPO_ROOT / "data" / "normalization.json"

LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
CVX = "http://hl7.org/fhir/sid/cvx"

_SYSTEM_KEYS = {LOINC: "loinc", SNOMED: "snomed", RXNORM: "rxnorm", CVX: "cvx"}

_WS = re.compile(r"\s+")
_FIRST_DIGIT_TOKEN = re.compile(r"(?:(?<=\s)|^)\d")


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemRules:
    """Normalization vocabulary for one code system."""

    stoplist: frozenset[str] = frozenset()
    min_form_length: int = 4
    specimens: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    semantic_tags: tuple[str, ...] = ()
    leading_prefix_patterns: tuple[re.Pattern[str], ...] = ()
    brand_strip_patterns: tuple[re.Pattern[str], ...] = ()
    salt_suffixes: frozenset[str] = frozenset()
    noise_tokens: frozenset[str] = frozenset()


@dataclass(frozen=True)
class NormalizationRules:
    """All per-system rules, loaded from ``data/normalization.json``."""

    systems: dict[str, SystemRules] = field(default_factory=dict)

    def for_system(self, code_system: str | None) -> SystemRules:
        key = _SYSTEM_KEYS.get(code_system or "", "default")
        return self.systems.get(key) or self.systems.get("default") or SystemRules()

    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_RULES_PATH) -> NormalizationRules:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        systems: dict[str, SystemRules] = {}
        for key, block in raw.items():
            if key.startswith("_") or not isinstance(block, dict):
                continue
            systems[key] = SystemRules(
                stoplist=frozenset(_clean(s) for s in block.get("stoplist", [])),
                min_form_length=int(block.get("min_form_length", 4)),
                # longest first so "serum or plasma" wins over "serum"
                specimens=_by_length(block.get("specimens", [])),
                methods=_by_length(block.get("methods", [])),
                semantic_tags=_by_length(block.get("semantic_tags", [])),
                leading_prefix_patterns=tuple(
                    re.compile(p, re.IGNORECASE)
                    for p in block.get("leading_prefix_patterns", [])
                ),
                brand_strip_patterns=tuple(
                    re.compile(p, re.IGNORECASE)
                    for p in block.get("brand_strip_patterns", [])
                ),
                salt_suffixes=frozenset(
                    _clean(s) for s in block.get("salt_suffixes", [])
                ),
                noise_tokens=frozenset(_clean(s) for s in block.get("noise_tokens", [])),
            )
        return cls(systems=systems)


@lru_cache(maxsize=8)
def _load_rules_cached(path: str) -> NormalizationRules:
    return NormalizationRules.from_file(path)


def load_rules(path: str | Path | None = None) -> NormalizationRules:
    """Load (and cache) normalization rules. Pass a path to override."""
    return _load_rules_cached(str(path or DEFAULT_RULES_PATH))


def _by_length(values: list[str]) -> tuple[str, ...]:
    return tuple(sorted((_clean(v) for v in values), key=len, reverse=True))


# --------------------------------------------------------------------------
# shared text helpers
# --------------------------------------------------------------------------


def _clean(text: str) -> str:
    """Lowercase, collapse whitespace, trim."""
    return _WS.sub(" ", (text or "").replace(" ", " ")).strip().lower()


def _strip_trailing_clause(text: str, keyword: str, vocab: tuple[str, ...]) -> str:
    """Remove a trailing ``<keyword> <term>`` clause when ``term`` is known.

    Vocabulary-gated on purpose: a blind "cut at the last ' in '" would
    mangle questionnaire displays such as "Recurrent opioid use in
    situations in which it is physically hazardous".
    """
    for term in vocab:
        suffix = f" {keyword} {term}"
        if text.endswith(suffix):
            head = text[: -len(suffix)].strip()
            if head:
                return head
    return text


# --------------------------------------------------------------------------
# LOINC (LAB, VITAL)
# --------------------------------------------------------------------------


def normalize_loinc(display: str, rules: SystemRules | None = None) -> str:
    """``Analyte [Property] in Specimen by Method`` -> ``analyte``.

    The bracket cut runs first: "Cholesterol in HDL [Mass/volume] in Serum
    or Plasma" has an ``in`` before the bracket that must survive, so only
    the *trailing* specimen phrase is ever removed.
    """
    rules = rules or load_rules().for_system(LOINC)
    text = display.split("[", 1)[0]
    text = text.split("--", 1)[0]
    text = _clean(text)

    # Clauses can nest ("... by Probe in Positive blood culture"), so peel
    # until stable.
    for _ in range(3):
        before = text
        text = _strip_trailing_clause(text, "by", rules.methods)
        text = _strip_trailing_clause(text, "in", rules.specimens)
        if text == before:
            break
    return text.strip(" ,-")


# --------------------------------------------------------------------------
# SNOMED (CONDITION, PROCEDURE, ALLERGY)
# --------------------------------------------------------------------------


def normalize_snomed(display: str, rules: SystemRules | None = None) -> str:
    """Strip the trailing parenthetical semantic tag."""
    rules = rules or load_rules().for_system(SNOMED)
    text = _clean(display)
    for tag in rules.semantic_tags:
        suffix = f"({tag})"
        if text.endswith(suffix):
            head = text[: -len(suffix)].strip()
            if head:
                return head
    return text


# --------------------------------------------------------------------------
# CVX (IMMUNIZATION)
# --------------------------------------------------------------------------


def normalize_cvx(display: str, rules: SystemRules | None = None) -> str:
    """No structured pattern to strip; lowercase and trim."""
    return _clean(display)


# --------------------------------------------------------------------------
# RxNorm (MEDICATION)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RxNormParts:
    """What an RxNorm display decomposes into.

    ``salt_forms`` holds the pre-strip name where it differs from the
    ingredient (``warfarin sodium`` alongside ``warfarin``): both are worth
    indexing, and for a few entries the salt carries the identity
    (``ferrous sulfate`` is the useful name, ``ferrous`` is not).
    """

    ingredients: tuple[str, ...] = ()
    salt_forms: tuple[str, ...] = ()
    brand: str | None = None


def normalize_rxnorm(display: str, rules: SystemRules | None = None) -> RxNormParts:
    """Split an RxNorm display into ingredient(s) and brand.

    ``Acetaminophen 325 MG / Oxycodone Hydrochloride 5 MG Oral Tablet
    [Percocet]`` -> ingredients ``("acetaminophen", "oxycodone")``, brand
    ``"percocet"``. That gives RxNorm's ingredient-rollup behaviour without
    downloading RxNorm.
    """
    rules = rules or load_rules().for_system(RXNORM)

    body, brand = _split_brand(display, rules)
    body = _strip_leading_prefixes(body, rules)

    ingredients: list[str] = []
    salt_forms: list[str] = []
    for fragment in body.split("/"):
        name, salted = _fragment_to_ingredient(fragment, rules)
        if name and name not in ingredients:
            ingredients.append(name)
        if salted and salted != name and salted not in salt_forms:
            salt_forms.append(salted)

    return RxNormParts(
        ingredients=tuple(ingredients),
        salt_forms=tuple(salt_forms),
        brand=brand,
    )


def _split_brand(display: str, rules: SystemRules) -> tuple[str, str | None]:
    """Pull the trailing bracketed brand name off, if present."""
    match = re.search(r"\[([^\[\]]+)\]\s*$", display)
    if not match:
        return display, None
    brand = _clean(match.group(1))
    for pattern in rules.brand_strip_patterns:  # "camila 28 day" -> "camila"
        brand = _clean(pattern.sub("", brand))
    return display[: match.start()], (brand or None)


def _strip_leading_prefixes(text: str, rules: SystemRules) -> str:
    """Drop quantity/duration/marketing prefixes: ``1 ML``, ``24 HR``,
    ``168 HR``, ``21 DAY``, ``NDA020800``, ``Abuse-Deterrent``."""
    text = text.strip()
    changed = True
    while changed:
        changed = False
        for pattern in rules.leading_prefix_patterns:
            match = pattern.match(text)
            if match and match.end() > 0:
                text = text[match.end() :].lstrip()
                changed = True
    return text


def _fragment_to_ingredient(
    fragment: str, rules: SystemRules
) -> tuple[str | None, str | None]:
    """One ``/``-delimited fragment -> ``(ingredient, salted_form)``.

    Returns ``(None, None)`` when the fragment is only strength, dose-form
    or packaging debris (``"ML Oral Solution"``).
    """
    # Pack notation: "{28 (norethindrone 0.35 MG Oral Tablet) }"
    text = re.sub(r"[{}()]", " ", fragment)
    text = text.replace(",", " ")
    text = _strip_leading_prefixes(_clean(text), rules)

    # Cut at the first token-initial digit: from the strength onward it is
    # dose information, not identity. Token-initial so that "Vitamin B12"
    # keeps its "12".
    digit = _FIRST_DIGIT_TOKEN.search(text)
    if digit:
        text = text[: digit.start()]
    text = _clean(text)
    if not text:
        return None, None

    tokens = [t for t in text.split(" ") if t and t not in rules.noise_tokens]
    if not tokens:
        return None, None
    salted = " ".join(tokens).strip(" -") or None

    # Trailing salt/ester words are not part of the ingredient identity,
    # but never strip away the whole name ("Sodium Chloride" keeps both
    # tokens because "chloride" is not a salt suffix).
    while len(tokens) > 1 and tokens[-1] in rules.salt_suffixes:
        tokens.pop()
    name = " ".join(tokens).strip(" -") or None
    return name, salted


# --------------------------------------------------------------------------
# surface forms
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedDisplay:
    """Everything one lexicon row contributes to the surface-form index."""

    full: str
    primary: str | None = None
    ingredients: tuple[str, ...] = ()
    salt_forms: tuple[str, ...] = ()
    brand: str | None = None
    suppressed: tuple[str, ...] = ()

    @property
    def forms(self) -> tuple[str, ...]:
        """Indexable surface forms, de-duplicated, order preserved."""
        out: list[str] = []
        for form in (
            self.full,
            self.primary,
            self.brand,
            *self.ingredients,
            *self.salt_forms,
        ):
            if form and form not in out:
                out.append(form)
        return tuple(out)


def is_indexable(form: str, rules: SystemRules) -> bool:
    """A derived form must not be indexed if it is generic or too short.

    ``total score`` currently collapses 4 distinct LOINC codes; indexing it
    would let a query for "score" claim all of them. The *full* display of
    such a row is still indexed by :func:`normalize_display`.
    """
    form = _clean(form)
    if not form:
        return False
    if form in rules.stoplist:
        return False
    return len(form) >= rules.min_form_length


def normalize_display(
    code_system: str | None,
    display: str,
    rules: NormalizationRules | None = None,
) -> NormalizedDisplay:
    """Normalize one lexicon row into its indexable surface forms.

    The full lowercased display is always indexed, even when every derived
    form is suppressed by the stoplist.
    """
    all_rules = rules or load_rules()
    sys_rules = all_rules.for_system(code_system)

    full = _clean(display)
    primary: str | None = None
    ingredients: list[str] = []
    salt_forms: list[str] = []
    brand: str | None = None
    suppressed: list[str] = []

    if code_system == LOINC:
        primary = normalize_loinc(display, sys_rules)
    elif code_system == SNOMED:
        primary = normalize_snomed(display, sys_rules)
    elif code_system == CVX:
        primary = normalize_cvx(display, sys_rules)
    elif code_system == RXNORM:
        parts = normalize_rxnorm(display, sys_rules)
        ingredients = list(parts.ingredients)
        salt_forms = list(parts.salt_forms)
        brand = parts.brand
    else:
        primary = full

    def keep(form: str | None) -> str | None:
        if not form or form == full:
            return None
        if is_indexable(form, sys_rules):
            return form
        suppressed.append(form)
        return None

    primary = keep(primary)
    brand = keep(brand)
    ingredients = [i for i in (keep(x) for x in ingredients) if i]
    salt_forms = [s for s in (keep(x) for x in salt_forms) if s]

    return NormalizedDisplay(
        full=full,
        primary=primary,
        ingredients=tuple(ingredients),
        salt_forms=tuple(salt_forms),
        brand=brand,
        suppressed=tuple(suppressed),
    )


def normalize_query_text(text: str) -> str:
    """Canonical matching key, for query text and for index keys alike.

    Keeps ``+`` (``k+``), ``/`` (``fev1/fvc``) and word-internal ``.``/``-``
    (``bilirubin.total``, ``ortho tri-cyclen``); everything else
    punctuation-ish becomes whitespace. Apostrophes are deleted rather than
    split on, so a query for "cows milk" still reaches "Cow's milk".
    """
    text = re.sub(r"['‘’]", "", text or "")
    text = (text or "").lower().replace(" ", " ")
    text = re.sub(r"[^\w\s+/.\-]", " ", text)
    text = re.sub(r"(?<![\w+])[.\-](?![\w])", " ", text)
    return _WS.sub(" ", text).strip()
