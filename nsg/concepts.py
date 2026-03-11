"""Concept extraction and text chunking utilities."""

from __future__ import annotations

import re
import textwrap
from typing import TYPE_CHECKING

from nsg.stopwords import STOPWORDS_ALL

if TYPE_CHECKING:
    import spacy

_NLP_CACHE: "dict[str, spacy.Language]" = {}


def _get_nlp(model_name: str = "en_core_web_sm") -> "spacy.Language":
    """Lazy-load the spaCy model with a clear error when it's missing."""
    if model_name in _NLP_CACHE:
        return _NLP_CACHE[model_name]
    try:
        import spacy as _spacy
    except ImportError as exc:
        raise ImportError("spaCy is required. Install it with: pip install spacy") from exc
    try:
        nlp = _spacy.load(model_name)
    except OSError as exc:
        raise OSError(
            f"spaCy model '{model_name}' not found. "
            f"Download it with: python -m spacy download {model_name}"
        ) from exc
    _NLP_CACHE[model_name] = nlp
    return nlp


# ------------------------------------------------------------------
# Text chunking
# ------------------------------------------------------------------


def chunk_text(text: str, max_chars: int = 800) -> list[str]:
    """Split *text* into chunks that respect sentence boundaries.

    Strategy: split on sentence-ending punctuation, then greedily pack
    sentences into chunks up to *max_chars*.  If a single sentence exceeds
    the budget it is hard-wrapped with :func:`textwrap.wrap`.
    """
    # Rough sentence split – handles ". ", "! ", "? " and newlines.
    raw_sentences = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    sentences = [s.strip() for s in raw_sentences if s.strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sent in sentences:
        if len(sent) > max_chars:
            # Flush anything accumulated so far.
            if current:
                chunks.append(" ".join(current))
                current, current_len = [], 0
            # Hard-wrap the oversized sentence.
            chunks.extend(textwrap.wrap(sent, width=max_chars))
            continue

        if current_len + len(sent) + 1 > max_chars:
            chunks.append(" ".join(current))
            current, current_len = [], 0

        current.append(sent)
        current_len += len(sent) + 1  # +1 for the joining space

    if current:
        chunks.append(" ".join(current))

    return chunks


# ------------------------------------------------------------------
# Concept extraction
# ------------------------------------------------------------------

_STOP_CONCEPTS = STOPWORDS_ALL | frozenset(
    {
        # Extra pronouns / demonstratives not in the shared list
        "ceci",
        "cela",
        "ça",
        "celui",
        "celle",
        "ceux",
        "celles",
        "quoi",
    }
)


def normalize_concept(concept: str) -> str:
    """Lowercase, collapse whitespace, strip leading determiners (EN + FR)."""
    c = concept.lower().strip()
    c = re.sub(r"\s+", " ", c)
    # Strip leading determiners / articles (English + French).
    c = re.sub(
        r"^(the|a|an"  # EN
        r"|le|la|les|l'"  # FR definite
        r"|un|une|des"  # FR indefinite
        r"|du|de la|de l'|de|d'|au|aux"  # FR contracted
        r")(?:\s+|$)",
        "",
        c,
    )
    # Handle elided French articles glued to the word (l'exemple → exemple)
    c = re.sub(r"^[ldnm]'", "", c)
    return c


_FUNCTION_POS = frozenset(
    {
        "DET",
        "ADP",
        "PRON",
        "AUX",
        "PUNCT",
        "CCONJ",
        "SCONJ",
        "PART",
    }
)


def extract_concepts(text: str, spacy_model: str = "en_core_web_sm") -> list[str]:
    """Return deduplicated, normalised concepts from *text*.

    Concepts come from spaCy named entities **and** noun chunks.
    """
    nlp = _get_nlp(spacy_model)
    doc = nlp(text)

    seen: set[str] = set()
    concepts: list[str] = []

    for span in list(doc.ents) + list(doc.noun_chunks):
        # Skip spans made entirely of function words (determiners, etc.)
        if all(tok.pos_ in _FUNCTION_POS for tok in span):
            continue
        norm = normalize_concept(span.text)
        if not norm or norm in _STOP_CONCEPTS or len(norm) < 2:
            continue
        if norm not in seen:
            seen.add(norm)
            concepts.append(norm)

    return concepts
