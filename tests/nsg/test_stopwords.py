"""Tests for the shared stopwords module."""

from nsg.stopwords import (
    STOPWORDS_ALL,
    STOPWORDS_EN,
    STOPWORDS_FR,
    get_stopwords_for_sklearn,
)


class TestStopwordSets:
    def test_french_set_is_frozenset(self) -> None:
        assert isinstance(STOPWORDS_FR, frozenset)

    def test_english_set_is_frozenset(self) -> None:
        assert isinstance(STOPWORDS_EN, frozenset)

    def test_all_is_frozenset(self) -> None:
        assert isinstance(STOPWORDS_ALL, frozenset)

    def test_all_is_union(self) -> None:
        assert STOPWORDS_ALL == STOPWORDS_FR | STOPWORDS_EN

    def test_french_articles_present(self) -> None:
        for w in ("le", "la", "les", "un", "une", "des", "du", "au", "aux"):
            assert w in STOPWORDS_FR, f"{w!r} missing from STOPWORDS_FR"

    def test_french_prepositions_present(self) -> None:
        for w in ("de", "dans", "pour", "sur", "avec", "par", "entre", "vers"):
            assert w in STOPWORDS_FR, f"{w!r} missing from STOPWORDS_FR"

    def test_english_articles_present(self) -> None:
        for w in ("the", "a", "an"):
            assert w in STOPWORDS_EN, f"{w!r} missing from STOPWORDS_EN"

    def test_sets_are_nonempty(self) -> None:
        assert len(STOPWORDS_FR) > 20
        assert len(STOPWORDS_EN) > 20
        assert len(STOPWORDS_ALL) > len(STOPWORDS_FR)


class TestGetStopwordsForSklearn:
    def test_returns_sorted_list(self) -> None:
        result = get_stopwords_for_sklearn()
        assert isinstance(result, list)
        assert result == sorted(result)

    def test_contains_all_stopwords(self) -> None:
        result = set(get_stopwords_for_sklearn())
        assert result == set(STOPWORDS_ALL)
