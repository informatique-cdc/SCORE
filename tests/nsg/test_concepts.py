"""Tests for the concept extraction and chunking utilities."""

import pytest

from nsg.concepts import chunk_text, extract_concepts, normalize_concept


class TestChunkText:
    def test_short_text_single_chunk(self) -> None:
        chunks = chunk_text("Hello world.", max_chars=800)
        assert len(chunks) == 1
        assert chunks[0] == "Hello world."

    def test_respects_max_chars(self) -> None:
        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        chunks = chunk_text(text, max_chars=40)
        for chunk in chunks:
            assert len(chunk) <= 40

    def test_empty_text(self) -> None:
        assert chunk_text("") == []

    def test_oversized_sentence_is_hard_wrapped(self) -> None:
        text = "a" * 1600
        chunks = chunk_text(text, max_chars=800)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 800


class TestNormalizeConcept:
    def test_lowercase_and_strip(self) -> None:
        assert normalize_concept("  Machine Learning  ") == "machine learning"

    def test_removes_leading_article(self) -> None:
        assert normalize_concept("The United States") == "united states"
        assert normalize_concept("a test") == "test"
        assert normalize_concept("an example") == "example"

    def test_collapses_whitespace(self) -> None:
        assert normalize_concept("deep   learning") == "deep learning"

    @pytest.mark.parametrize(
        "word",
        [
            "les",
            "le",
            "la",
            "un",
            "une",
            "des",
            "du",
            "au",
            "aux",
            "de",
        ],
    )
    def test_standalone_french_article_normalizes_to_empty(self, word: str) -> None:
        assert normalize_concept(word) == ""

    def test_french_article_stripped_from_phrase(self) -> None:
        assert normalize_concept("les données") == "données"
        assert normalize_concept("Le système") == "système"
        assert normalize_concept("un exemple") == "exemple"
        assert normalize_concept("des résultats") == "résultats"
        assert normalize_concept("du projet") == "projet"


class TestExtractConcepts:
    def test_returns_nonempty_list(self) -> None:
        text = "Machine learning is a subset of artificial intelligence."
        concepts = extract_concepts(text)
        assert len(concepts) > 0

    def test_concepts_are_normalised(self) -> None:
        text = "The European Union passed new regulations."
        concepts = extract_concepts(text)
        for c in concepts:
            assert c == c.lower().strip()

    def test_deduplication(self) -> None:
        text = "Python is great. Python is versatile."
        concepts = extract_concepts(text)
        assert len(concepts) == len(set(concepts))

    def test_no_french_stopwords_leak(self) -> None:
        from nsg.stopwords import STOPWORDS_FR

        text = (
            "Le système de gestion des données permet un traitement "
            "efficace des résultats dans les bases de données."
        )
        concepts = extract_concepts(text)
        for c in concepts:
            assert c not in STOPWORDS_FR, f"Stopword {c!r} leaked through"

    def test_no_english_stopwords_leak(self) -> None:
        from nsg.stopwords import STOPWORDS_EN

        text = "The system provides a framework for the analysis of data."
        concepts = extract_concepts(text)
        for c in concepts:
            assert c not in STOPWORDS_EN, f"Stopword {c!r} leaked through"
