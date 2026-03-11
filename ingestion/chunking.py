"""
Document chunking strategies.

Two strategies:
  1. heading_aware: Split on headings first, then by token count with overlap.
     Preserves heading context in each chunk for better retrieval.
  2. token_fixed: Simple fixed-size token-based chunking with overlap.

Both use tiktoken for accurate token counting.
"""
import logging
import re
from dataclasses import dataclass

import tiktoken

from django.conf import settings

from .hashing import hash_chunk

logger = logging.getLogger(__name__)

# Use cl100k_base encoding (GPT-4, text-embedding-3)
_enc: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    global _enc
    if _enc is None:
        _enc = tiktoken.get_encoding("cl100k_base")
    return _enc


@dataclass
class Chunk:
    """A text chunk with metadata."""
    index: int
    content: str
    token_count: int
    heading_path: str
    content_hash: str


def chunk_document(
    text: str,
    headings: list[dict] | None = None,
    strategy: str | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    min_chunk_size: int | None = None,
) -> list[Chunk]:
    """
    Split document text into chunks.

    Args:
        text: Full document text
        headings: List of {"level": int, "text": str, "offset": int}
        strategy: "heading_aware" or "token_fixed"
        chunk_size: Target tokens per chunk
        chunk_overlap: Overlap tokens between chunks
        min_chunk_size: Minimum tokens to keep a chunk (discard smaller)
    """
    config = settings.CHUNKING_CONFIG
    strategy = strategy or config.get("strategy", "heading_aware")
    chunk_size = chunk_size or config.get("chunk_size", 512)
    chunk_overlap = chunk_overlap or config.get("chunk_overlap", 64)
    min_chunk_size = min_chunk_size or config.get("min_chunk_size", 50)

    if strategy == "heading_aware" and headings:
        return _chunk_heading_aware(text, headings, chunk_size, chunk_overlap, min_chunk_size)
    return _chunk_token_fixed(text, chunk_size, chunk_overlap, min_chunk_size)


def _chunk_heading_aware(
    text: str,
    headings: list[dict],
    chunk_size: int,
    chunk_overlap: int,
    min_chunk_size: int,
) -> list[Chunk]:
    """
    Split text by headings first, then subdivide large sections by token count.
    Each chunk retains its heading path for context.
    """
    enc = _get_encoder()
    chunks: list[Chunk] = []
    chunk_idx = 0

    # Build sections from headings
    sections = _split_by_headings(text, headings)

    for section_text, heading_path in sections:
        section_tokens = enc.encode(section_text)
        if len(section_tokens) <= chunk_size:
            if len(section_tokens) >= min_chunk_size:
                chunks.append(Chunk(
                    index=chunk_idx,
                    content=section_text.strip(),
                    token_count=len(section_tokens),
                    heading_path=heading_path,
                    content_hash=hash_chunk(section_text),
                ))
                chunk_idx += 1
        else:
            # Subdivide large section with overlap
            sub_chunks = _split_tokens(section_text, section_tokens, enc, chunk_size, chunk_overlap, min_chunk_size)
            for sc_text, sc_token_count in sub_chunks:
                chunks.append(Chunk(
                    index=chunk_idx,
                    content=sc_text.strip(),
                    token_count=sc_token_count,
                    heading_path=heading_path,
                    content_hash=hash_chunk(sc_text),
                ))
                chunk_idx += 1

    return chunks


def _split_by_headings(text: str, headings: list[dict]) -> list[tuple[str, str]]:
    """Split text at heading boundaries, returning (section_text, heading_path) pairs."""
    if not headings:
        return [(text, "")]

    sections = []
    heading_stack: list[str] = []
    sorted_headings = sorted(headings, key=lambda h: h["offset"])

    for i, heading in enumerate(sorted_headings):
        start = heading["offset"]
        end = sorted_headings[i + 1]["offset"] if i + 1 < len(sorted_headings) else len(text)

        # Maintain heading hierarchy
        level = heading.get("level", 1)
        heading_text = heading["text"]

        # Trim stack to current level
        heading_stack = heading_stack[: max(0, level - 1)]
        heading_stack.append(heading_text)

        heading_path = " > ".join(heading_stack)
        section_text = text[start:end].strip()
        if section_text:
            sections.append((section_text, heading_path))

    # If there's text before the first heading
    if sorted_headings and sorted_headings[0]["offset"] > 0:
        preamble = text[: sorted_headings[0]["offset"]].strip()
        if preamble:
            sections.insert(0, (preamble, ""))

    return sections if sections else [(text, "")]


def _chunk_token_fixed(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    min_chunk_size: int,
) -> list[Chunk]:
    """Simple fixed-size token chunking with overlap."""
    enc = _get_encoder()
    tokens = enc.encode(text)

    return [
        Chunk(
            index=idx,
            content=chunk_text.strip(),
            token_count=tc,
            heading_path="",
            content_hash=hash_chunk(chunk_text),
        )
        for idx, (chunk_text, tc) in enumerate(
            _split_tokens(text, tokens, enc, chunk_size, chunk_overlap, min_chunk_size)
        )
    ]


def _split_tokens(
    text: str,
    tokens: list[int],
    enc: tiktoken.Encoding,
    chunk_size: int,
    chunk_overlap: int,
    min_chunk_size: int,
) -> list[tuple[str, int]]:
    """Split token list into overlapping windows and decode back to text."""
    results = []
    step = max(1, chunk_size - chunk_overlap)

    for start in range(0, len(tokens), step):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        if len(chunk_tokens) < min_chunk_size:
            continue
        chunk_text = enc.decode(chunk_tokens)
        results.append((chunk_text, len(chunk_tokens)))

        if end >= len(tokens):
            break

    return results


def count_tokens(text: str) -> int:
    """Count tokens in a text string."""
    return len(_get_encoder().encode(text))
