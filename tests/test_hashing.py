"""Tests for content hashing module."""

from ingestion.hashing import hash_content, hash_chunk, normalize_text


class TestNormalizeText:
    def test_lowercases(self):
        assert normalize_text("Hello World") == "hello world"

    def test_collapses_whitespace(self):
        assert normalize_text("hello   world\n\tfoo") == "hello world foo"

    def test_strips(self):
        assert normalize_text("  hello  ") == "hello"

    def test_empty(self):
        assert normalize_text("") == ""


class TestHashContent:
    def test_deterministic(self):
        h1 = hash_content("hello world")
        h2 = hash_content("hello world")
        assert h1 == h2

    def test_different_content_different_hash(self):
        h1 = hash_content("hello world")
        h2 = hash_content("goodbye world")
        assert h1 != h2

    def test_whitespace_normalization(self):
        h1 = hash_content("hello   world")
        h2 = hash_content("hello world")
        assert h1 == h2

    def test_case_insensitive(self):
        h1 = hash_content("Hello World")
        h2 = hash_content("hello world")
        assert h1 == h2

    def test_sha256_length(self):
        h = hash_content("test")
        assert len(h) == 64  # SHA-256 hex digest

    def test_hash_chunk_alias(self):
        assert hash_chunk("test") == hash_content("test")
