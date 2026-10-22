"""Filing-search tool: a thin, agent-facing wrapper over the Retriever protocol.

All vector math lives behind app.retriever, so this module and the agent that
calls it never touch numpy or the index format.
"""
from __future__ import annotations

from typing import Optional

from app.retriever import get_retriever


def search_filings(query: str, company: Optional[str] = None,
                   year: Optional[int] = None, k: int = 5,
                   min_score: Optional[float] = None) -> list[dict]:
    """Return up to `k` filing chunks most similar to `query`, each with its
    source (ticker, fiscal year, section, chunk ordinal, score). `min_score` is
    an optional cosine cutoff applied here so the Retriever stays minimal."""
    hits = get_retriever().search(query, k=k, company=company, year=year)
    if min_score is not None:
        hits = [h for h in hits if h["score"] >= min_score]
    return hits
