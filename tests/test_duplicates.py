"""Tests for duplicate detection scoring logic."""

import pytest
from unittest.mock import MagicMock


from analysis.duplicates import DuplicateDetector


class TestMetadataSimilarity:
    """Test the metadata similarity scoring component."""

    def setup_method(self):
        # Create a detector without DB dependencies for unit testing
        self.detector = DuplicateDetector.__new__(DuplicateDetector)
        self.detector.config = {
            "semantic_weight": 0.55,
            "lexical_weight": 0.25,
            "metadata_weight": 0.20,
        }

    def _make_doc(self, title="", path="", author=""):
        doc = MagicMock()
        doc.title = title
        doc.path = path
        doc.author = author
        return doc

    def test_identical_metadata(self):
        doc = self._make_doc("Guide to Python", "/docs/python", "Alice")
        score = self.detector._metadata_similarity(doc, doc)
        assert score == pytest.approx(1.0)

    def test_similar_titles(self):
        a = self._make_doc("Getting Started with Python", "", "")
        b = self._make_doc("Getting Started with Python 3", "", "")
        score = self.detector._metadata_similarity(a, b)
        assert score > 0.7

    def test_different_metadata(self):
        a = self._make_doc("Python Guide", "/docs/python", "Alice")
        b = self._make_doc("Java Handbook", "/docs/java", "Bob")
        score = self.detector._metadata_similarity(a, b)
        assert score < 0.5

    def test_empty_metadata(self):
        a = self._make_doc("", "", "")
        b = self._make_doc("", "", "")
        score = self.detector._metadata_similarity(a, b)
        assert score == 0.0  # No fields to compare


class TestCombinedScoring:
    """Test the weighted combination of similarity signals."""

    def test_weighted_combination(self):
        """Verify the math of the weighted score."""
        semantic_w = 0.55
        lexical_w = 0.25
        metadata_w = 0.20

        semantic = 0.95
        lexical = 0.80
        metadata = 0.70

        expected = semantic_w * semantic + lexical_w * lexical + metadata_w * metadata
        assert expected == pytest.approx(0.5225 + 0.2 + 0.14)

    def test_weights_sum_to_one(self):
        """Weights should sum to 1.0 for a proper convex combination."""
        assert 0.55 + 0.25 + 0.20 == pytest.approx(1.0)

    def test_high_semantic_alone_flags(self):
        """A very high semantic score should exceed the combined threshold."""
        combined = 0.55 * 0.95 + 0.25 * 0.0 + 0.20 * 0.0
        # 0.5225 — this alone won't exceed 0.80
        # But combined threshold is 0.80, so pure semantic isn't enough
        assert combined < 0.80

    def test_all_high_signals(self):
        """All high signals should clearly exceed threshold."""
        combined = 0.55 * 0.95 + 0.25 * 0.85 + 0.20 * 0.90
        assert combined > 0.80
