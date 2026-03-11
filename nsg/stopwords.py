"""Shared French + English stopword lists for concept extraction and TF-IDF."""

from __future__ import annotations

STOPWORDS_FR: frozenset[str] = frozenset(
    "le la les un une des de du d l au aux en et est que qui ne pas ce se"
    " son sa ses sur pour dans par avec tout toute tous toutes cette ces"
    " mais ou où plus leur leurs il elle on nous vous ils elles je tu me"
    " te lui y a été être avoir fait faire comme aussi bien si non dont"
    " quand très même sans sous entre vers chez après avant"
    # contracted / elided forms
    " c n m s j qu".split()
)

STOPWORDS_EN: frozenset[str] = frozenset(
    "the a an is are was were be been being have has had do does did"
    " will would shall should can could may might must and but or nor"
    " not no for to from by at in on of with as it its this that these"
    " those he she they we you i me him her us them my your his its"
    " our their what which who whom how when where why if then so".split()
)

STOPWORDS_ALL: frozenset[str] = STOPWORDS_FR | STOPWORDS_EN


def get_stopwords_for_sklearn() -> list[str]:
    """Return a sorted list suitable for ``TfidfVectorizer(stop_words=...)``."""
    return sorted(STOPWORDS_ALL)
