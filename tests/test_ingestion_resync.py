"""Tests for ingestion.pipeline — documents that did not reach READY are re-ingested."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import APITimeoutError

from connectors.base import BaseConnector, RawDocument
from ingestion.chunking import Chunk
from ingestion.hashing import hash_chunk
from ingestion.models import Document, IngestionJob
from ingestion.pipeline import IngestionPipeline
from vectorstore.store import VectorStore

DIMS = 8


class FakeConnector(BaseConnector):
    """In-memory source: ``docs`` maps source_id -> (source_version, text)."""

    def __init__(self, docs=None):
        super().__init__(config={})
        self.docs = dict(docs or {})

    def test_connection(self):
        return True

    def list_documents(self):
        return [
            {"source_id": sid, "title": sid, "source_version": version}
            for sid, (version, _text) in self.docs.items()
        ]

    def fetch_document(self, source_id):
        version, text = self.docs[source_id]
        return RawDocument(
            source_id=source_id,
            title=source_id,
            content=text,
            content_type="text/plain",
            source_version=version,
        )


def _one_chunk(text, headings=None):
    return [
        Chunk(
            index=0,
            content=text,
            token_count=len(text.split()),
            heading_path="",
            content_hash=hash_chunk(text),
        )
    ]


@pytest.fixture
def vec_store(tmp_path):
    store = VectorStore(db_path=tmp_path / "vec.sqlite3", dimensions=DIMS)
    store.ensure_tables()
    yield store
    store.close()


@pytest.fixture
def llm():
    client = MagicMock()
    client.embed.side_effect = lambda texts: [[1.0] + [0.0] * (DIMS - 1) for _ in texts]
    return client


def _sync(tenant, project, connector, source, llm, vec_store):
    job = IngestionJob.objects.create(tenant=tenant, project=project, connector=connector)
    with (
        patch("ingestion.pipeline.get_connector", return_value=source),
        patch("ingestion.pipeline.get_llm_client", return_value=llm),
        patch("ingestion.pipeline.get_vector_store", return_value=vec_store),
        patch("ingestion.pipeline.chunk_document", side_effect=_one_chunk),
    ):
        IngestionPipeline(job).run()
    job.refresh_from_db()
    return job


@pytest.mark.django_db
class TestResyncOfDocumentsNotReady:
    def test_document_restored_at_source_is_reindexed(
        self, tenant, project, connector, llm, vec_store
    ):
        source = FakeConnector({"note.txt": ("v1", "Procédure de remboursement des frais.")})
        _sync(tenant, project, connector, source, llm, vec_store)

        # Removed from the source (moved out, recycle bin, listing glitch) ...
        removed = dict(source.docs)
        source.docs.clear()
        _sync(tenant, project, connector, source, llm, vec_store)
        assert Document.objects.get(source_id="note.txt").status == Document.Status.DELETED

        # ... then restored unchanged.
        source.docs.update(removed)
        _sync(tenant, project, connector, source, llm, vec_store)

        doc = Document.objects.get(source_id="note.txt")
        assert doc.status == Document.Status.READY
        assert len(vec_store.get_all_vectors_for_tenant(str(tenant.id))) == 1

    def test_document_whose_embedding_failed_is_retried(
        self, tenant, project, connector, llm, vec_store
    ):
        source = FakeConnector({"note.txt": ("v1", "Procédure de remboursement des frais.")})
        working_embed = llm.embed.side_effect
        llm.embed.side_effect = APITimeoutError(
            request=httpx.Request("POST", "https://api.openai.com/v1/embeddings")
        )
        first = _sync(tenant, project, connector, source, llm, vec_store)
        assert first.error_count == 1
        assert vec_store.get_all_vectors_for_tenant(str(tenant.id)) == []

        # The embedding API is back; the source document did not change.
        llm.embed.side_effect = working_embed
        _sync(tenant, project, connector, source, llm, vec_store)

        doc = Document.objects.get(source_id="note.txt")
        assert doc.status == Document.Status.READY
        assert doc.chunks.filter(has_embedding=True).count() == 1
        assert len(vec_store.get_all_vectors_for_tenant(str(tenant.id))) == 1
