"""Tests for chunking module."""
import pytest

from ingestion.chunking import chunk_document, count_tokens, Chunk


@pytest.fixture(autouse=True)
def mock_settings(settings):
    settings.CHUNKING_CONFIG = {
        "strategy": "heading_aware",
        "chunk_size": 100,
        "chunk_overlap": 20,
        "min_chunk_size": 10,
    }


class TestCountTokens:
    def test_basic(self):
        count = count_tokens("hello world")
        assert count == 2

    def test_longer_text(self):
        count = count_tokens("The quick brown fox jumps over the lazy dog")
        assert count > 0

    def test_empty(self):
        assert count_tokens("") == 0


class TestChunkDocumentTokenFixed:
    def test_short_document_single_chunk(self):
        text = "This is a short document."
        chunks = chunk_document(text, strategy="token_fixed", min_chunk_size=1)
        assert len(chunks) == 1
        assert chunks[0].index == 0
        assert "short document" in chunks[0].content

    def test_chunks_have_required_fields(self):
        text = " ".join(["word"] * 200)
        chunks = chunk_document(text, strategy="token_fixed")
        for c in chunks:
            assert isinstance(c, Chunk)
            assert isinstance(c.index, int)
            assert isinstance(c.content, str)
            assert isinstance(c.token_count, int)
            assert isinstance(c.content_hash, str)
            assert len(c.content_hash) == 64

    def test_overlapping_chunks(self):
        text = " ".join([f"word{i}" for i in range(300)])
        chunks = chunk_document(text, strategy="token_fixed", chunk_size=50, chunk_overlap=10)
        assert len(chunks) > 1
        # Chunks should have sequential indices
        for i, c in enumerate(chunks):
            assert c.index == i

    def test_min_chunk_size_filtering(self):
        # Very short text that gets filtered
        text = "Hi"
        chunks = chunk_document(text, strategy="token_fixed", min_chunk_size=10)
        assert len(chunks) == 0


class TestChunkDocumentHeadingAware:
    def test_splits_on_headings(self):
        text = "# Introduction\nSome intro text here with enough words to pass the minimum.\n\n# Methods\nSome methods text here with enough words to pass the minimum."
        headings = [
            {"level": 1, "text": "Introduction", "offset": 0},
            {"level": 1, "text": "Methods", "offset": text.index("# Methods")},
        ]
        chunks = chunk_document(text, headings=headings, strategy="heading_aware")
        assert len(chunks) >= 1

    def test_heading_path_preserved(self):
        text = "# Chapter\n## Section\nContent here with enough words to be meaningful and pass minimum size.\n\n## Another Section\nMore content here with enough words as well."
        headings = [
            {"level": 1, "text": "Chapter", "offset": 0},
            {"level": 2, "text": "Section", "offset": text.index("## Section")},
            {"level": 2, "text": "Another Section", "offset": text.index("## Another")},
        ]
        chunks = chunk_document(text, headings=headings, strategy="heading_aware")
        # At least one chunk should have a heading path
        paths = [c.heading_path for c in chunks if c.heading_path]
        assert len(paths) > 0

    def test_falls_back_to_token_fixed_without_headings(self):
        text = " ".join(["word"] * 200)
        chunks = chunk_document(text, headings=None, strategy="heading_aware")
        assert len(chunks) >= 1
        # All chunks should have empty heading_path (no headings)
        for c in chunks:
            assert c.heading_path == ""

    def test_chunk_hashes_are_deterministic(self):
        text = "Some content for hashing test with enough words."
        c1 = chunk_document(text, strategy="token_fixed", min_chunk_size=1)
        c2 = chunk_document(text, strategy="token_fixed", min_chunk_size=1)
        assert c1[0].content_hash == c2[0].content_hash
