"""
Content hashing for deduplication and incremental re-ingestion.

Uses SHA-256 for content hashing. Normalizes text before hashing
to avoid false negatives from whitespace/encoding differences.
"""
import hashlib
import re


def normalize_text(text: str) -> str:
    """Normalize text for consistent hashing: lowercase, collapse whitespace, strip."""
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def hash_content(text: str) -> str:
    """Generate SHA-256 hash of normalized text content."""
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def hash_chunk(text: str) -> str:
    """Hash a chunk (same algorithm, distinct name for clarity)."""
    return hash_content(text)
