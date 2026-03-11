"""Tests for the vector index layer."""

import numpy as np
import pytest

from nsg.index import BruteForceConceptIndex, make_concept_index


@pytest.fixture
def brute_index() -> BruteForceConceptIndex:
    idx = BruteForceConceptIndex()
    rng = np.random.default_rng(42)
    for i in range(10):
        idx.add(f"concept_{i}", rng.random(64).astype(np.float32))
    idx.build()
    return idx


class TestBruteForceConceptIndex:
    def test_length(self, brute_index: BruteForceConceptIndex) -> None:
        assert len(brute_index) == 10

    def test_search_returns_results(self, brute_index: BruteForceConceptIndex) -> None:
        query = np.random.default_rng(0).random(64).astype(np.float32)
        results = brute_index.search(query, top_k=3)
        assert len(results) == 3

    def test_search_scores_are_sorted(self, brute_index: BruteForceConceptIndex) -> None:
        query = np.random.default_rng(1).random(64).astype(np.float32)
        results = brute_index.search(query, top_k=5)
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_empty_index(self) -> None:
        idx = BruteForceConceptIndex()
        idx.build()
        assert idx.search(np.zeros(64, dtype=np.float32)) == []


class TestMakeConceptIndex:
    def test_factory_returns_an_index(self) -> None:
        idx = make_concept_index()
        assert hasattr(idx, "add")
        assert hasattr(idx, "build")
        assert hasattr(idx, "search")
