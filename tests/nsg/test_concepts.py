"""Tests for the concept extraction and chunking utilities."""

import pytest

from nsg.concepts import chunk_text, extract_concepts, is_language, normalize_concept
from tests.nsg.conftest import requires_english_model, requires_french_model


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


@requires_english_model
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


class TestIsLanguage:
    """Écarte les fragments qui ne sont pas du texte, relevés sur un corpus réel."""

    @pytest.mark.parametrize(
        "concept",
        [
            "conseillers rh",
            "accord télétravail",
            "données personnelles des assurés",
            "décret date de publication 1984-07-04",
            "indemnisation limite de montant 82.50",
            # Le pourcent isolé est courant en français et doit passer.
            "proratisation à temps partiel (90%)",
            "l’abondement employeur sera majoré de 20%",
        ],
    )
    def test_keeps_real_concepts(self, concept: str) -> None:
        assert is_language(concept)

    def test_rejects_failed_decoding(self) -> None:
        """U+FFFD signe un texte que l'ingestion n'a pas su décoder."""
        assert not is_language("� � � � �")

    def test_rejects_a_single_replacement_char(self) -> None:
        assert not is_language("télétravail �")

    @pytest.mark.parametrize(
        "concept",
        [
            "6a%70%6c%61%74%66%6f%72%6d%2f%6a%63%6d%73",
            "mailto:%61%6e%64%72%65%61%2e%6d%6f%72%65%69%72%61",
            "%2f%6a%63%6d%73%2f%70%6c%31%5f%31%39%37%30%36%33%30",
        ],
    )
    def test_rejects_percent_encoded_blobs(self, concept: str) -> None:
        assert not is_language(concept)

    def test_tolerates_up_to_two_escapes(self) -> None:
        """La borne est un compromis : deux séquences peuvent être fortuites."""
        assert is_language("remise de 10%ab et 20%cd")
        assert not is_language("remise de 10%ab 20%cd 30%ef")


@requires_french_model
class TestExtractConceptsFilters:
    def test_encoded_url_never_becomes_a_concept(self) -> None:
        text = (
            "Le collaborateur consulte la plateforme "
            "%2f%6a%63%6d%73%2f%70%6c%31%5f%31%39%37%30%36%33%30%2f%65%67%61%6c%69%74%65 "
            "pour la gestion administrative."
        )

        concepts = extract_concepts(text, spacy_model="fr_core_news_sm")

        assert all("%2f" not in c for c in concepts)

    def test_undecodable_run_never_becomes_a_concept(self) -> None:
        text = "La politique de télétravail � � � � est publiée."

        concepts = extract_concepts(text, spacy_model="fr_core_news_sm")

        assert all("�" not in c for c in concepts)
