"""Retrieval interface + the shipped numpy implementation.

The tool/agent layer depends only on the `Retriever` protocol, never on numpy or
the on-disk format. Swapping in a real vector DB later (FAISS / pgvector / Chroma)
means writing one new class behind this interface -- no agent or tool changes.
For a few thousand chunks, exact cosine over a numpy matrix is simpler, has zero
infra, and is fast (a single matrix-vector product per query).
"""
from __future__ import annotations

import pickle
from typing import Optional, Protocol

import numpy as np

import config
from config import embed_texts, get_client


class Retriever(Protocol):
    """Semantic search returning chunks tagged with their filing source."""

    def search(self, query: str, k: int = 5, company: Optional[str] = None,
               year: Optional[int] = None) -> list[dict]:
        ...


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalization. Cosine similarity is the dot product of unit
    vectors, and embedding models don't always return unit-length vectors, so we
    normalize BOTH the stored matrix and every query -- otherwise a raw dot
    product is biased by magnitude instead of direction."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.clip(norms, 1e-8, None)


class NumpyCosineRetriever:
    """Exact cosine search over the prebuilt pickle index."""

    def __init__(self, index_path=config.INDEX_PATH):
        self._index_path = index_path
        self._vectors: Optional[np.ndarray] = None  # (n, dim), L2-normalized
        self._metas: Optional[list[dict]] = None

    def _ensure_loaded(self) -> None:
        if self._vectors is not None:
            return
        if not self._index_path.exists():
            raise FileNotFoundError(
                f"Vector index not found at {self._index_path}. "
                "Run `python -m scripts.build_index` first."
            )
        with open(self._index_path, "rb") as fh:
            blob = pickle.load(fh)
        self._vectors = _l2_normalize(np.asarray(blob["vectors"], dtype=np.float32))
        self._metas = blob["metas"]

    def search(self, query, k=5, company=None, year=None):
        self._ensure_loaded()
        q = embed_texts(get_client(), [query])[0]
        q = q / max(float(np.linalg.norm(q)), 1e-8)
        scores = self._vectors @ q  # cosine similarity for every chunk at once

        # Metadata pre-filter: only rank chunks from the requested filing(s).
        candidates = [
            i for i, m in enumerate(self._metas)
            if (not company or m["ticker"] == company.upper())
            and (not year or int(m["fiscal_year"]) == int(year))
        ]
        candidates.sort(key=lambda i: scores[i], reverse=True)

        hits = []
        for i in candidates[:k]:
            m = self._metas[i]
            hits.append({
                "text": m["text"],
                "ticker": m["ticker"],
                "fiscal_year": m["fiscal_year"],
                "section": m.get("section"),
                "ordinal": m.get("ordinal"),
                "score": float(scores[i]),
            })
        return hits


# To scale past a handful of filings, add a VectorDBRetriever(Retriever) here and
# point get_retriever() at it; nothing else in the codebase changes.
_DEFAULT: Optional[Retriever] = None


def get_retriever() -> Retriever:
    """Process-wide default retriever (lazy singleton so the index loads once)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = NumpyCosineRetriever()
    return _DEFAULT
