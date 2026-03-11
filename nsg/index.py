"""Vector search layer — FAISS primary, brute-force cosine fallback."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import numpy as np

logger = logging.getLogger(__name__)


class BaseConceptIndex(ABC):
    """Interface every concept index must implement."""

    @abstractmethod
    def add(self, concept: str, vector: np.ndarray) -> None: ...

    @abstractmethod
    def build(self) -> None: ...

    @abstractmethod
    def search(self, query_vector: np.ndarray, top_k: int = 12) -> list[tuple[str, float]]: ...

    @abstractmethod
    def __len__(self) -> int: ...


# ------------------------------------------------------------------
# FAISS-backed index
# ------------------------------------------------------------------

class FaissConceptIndex(BaseConceptIndex):
    """Wraps a FAISS ``IndexFlatIP`` (inner-product on L2-normalised vectors)."""

    def __init__(self) -> None:
        import faiss  # will raise ImportError if unavailable

        self._faiss = faiss
        self._concepts: list[str] = []
        self._vectors: list[np.ndarray] = []
        self._index: "faiss.IndexFlatIP | None" = None

    # -- mutators --------------------------------------------------

    def add(self, concept: str, vector: np.ndarray) -> None:
        self._concepts.append(concept)
        self._vectors.append(vector / (np.linalg.norm(vector) + 1e-10))
        self._index = None  # invalidate

    def build(self) -> None:
        if not self._vectors:
            return
        dim = self._vectors[0].shape[0]
        self._index = self._faiss.IndexFlatIP(dim)
        matrix = np.vstack(self._vectors).astype(np.float32)
        self._index.add(matrix)

    # -- query -----------------------------------------------------

    def search(self, query_vector: np.ndarray, top_k: int = 12) -> list[tuple[str, float]]:
        if self._index is None or self._index.ntotal == 0:
            return []
        qv = (query_vector / (np.linalg.norm(query_vector) + 1e-10)).reshape(1, -1).astype(np.float32)
        k = min(top_k, self._index.ntotal)
        scores, indices = self._index.search(qv, k)
        results: list[tuple[str, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            results.append((self._concepts[idx], float(score)))
        return results

    def __len__(self) -> int:
        return len(self._concepts)


# ------------------------------------------------------------------
# Brute-force fallback
# ------------------------------------------------------------------

class BruteForceConceptIndex(BaseConceptIndex):
    """Pure-NumPy cosine-similarity scan — no external deps beyond numpy."""

    def __init__(self) -> None:
        self._concepts: list[str] = []
        self._vectors: list[np.ndarray] = []
        self._matrix: np.ndarray | None = None

    def add(self, concept: str, vector: np.ndarray) -> None:
        self._concepts.append(concept)
        self._vectors.append(vector / (np.linalg.norm(vector) + 1e-10))
        self._matrix = None

    def build(self) -> None:
        if self._vectors:
            self._matrix = np.vstack(self._vectors).astype(np.float32)

    def search(self, query_vector: np.ndarray, top_k: int = 12) -> list[tuple[str, float]]:
        if self._matrix is None or len(self._matrix) == 0:
            return []
        qv = (query_vector / (np.linalg.norm(query_vector) + 1e-10)).astype(np.float32)
        scores = self._matrix @ qv
        k = min(top_k, len(scores))
        top_idx = np.argpartition(scores, -k)[-k:]
        top_idx = top_idx[np.argsort(scores[top_idx])[::-1]]
        return [(self._concepts[i], float(scores[i])) for i in top_idx]

    def __len__(self) -> int:
        return len(self._concepts)


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------

def make_concept_index() -> BaseConceptIndex:
    """Return FAISS index if available, otherwise fall back to brute force."""
    try:
        return FaissConceptIndex()
    except ImportError:
        logger.info("faiss-cpu not installed — using brute-force cosine fallback")
        return BruteForceConceptIndex()
