"""Kravos Setu query-side retrieval package.

Query-side NER only: parses the doctor's question against the corpus
vocabulary (``lexicon.csv`` + ``data/synonyms.csv``). Never reads
``facts.jsonl`` at runtime.
"""

from retrieval.lexicon import Lexicon
from retrieval.models import MatchQuality, ParsedQuery, ResolvedEntity
from retrieval.ner import QueryNER, parse_query

__all__ = [
    "Lexicon",
    "MatchQuality",
    "ParsedQuery",
    "QueryNER",
    "ResolvedEntity",
    "parse_query",
]
