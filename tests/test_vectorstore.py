"""Integration tests for sqlite-vec vector store."""
import tempfile
from pathlib import Path

import numpy as np
import pytest

from vectorstore.store import VectorStore


@pytest.fixture
def vec_store(tmp_path):
    """Create a temporary vector store for testing."""
    db_path = tmp_path / "test_vec.sqlite3"
    store = VectorStore(db_path=db_path, dimensions=8)
    store.ensure_tables()
    yield store
    store.close()


def _random_vector(dims=8, seed=None):
    """Generate a random normalized vector."""
    rng = np.random.RandomState(seed)
    v = rng.randn(dims).astype(np.float32)
    v = v / np.linalg.norm(v)
    return v.tolist()


class TestVectorStoreBasic:
    def test_upsert_and_search(self, vec_store):
        vec = _random_vector(8, seed=42)
        vec_store.upsert(
            chunk_id="chunk-1",
            tenant_id="tenant-1",
            vector=vec,
            metadata={"document_id": "doc-1", "doc_type": "report"},
        )

        results = vec_store.search(
            query_vector=vec,
            tenant_id="tenant-1",
            k=5,
        )
        assert len(results) == 1
        assert results[0]["chunk_id"] == "chunk-1"
        assert results[0]["document_id"] == "doc-1"
        assert results[0]["similarity"] > 0.99  # Same vector

    def test_tenant_isolation(self, vec_store):
        vec = _random_vector(8, seed=42)
        vec_store.upsert("chunk-1", "tenant-1", vec, {"document_id": "doc-1"})
        vec_store.upsert("chunk-2", "tenant-2", vec, {"document_id": "doc-2"})

        results = vec_store.search(vec, "tenant-1", k=10)
        assert len(results) == 1
        assert results[0]["chunk_id"] == "chunk-1"

    def test_batch_upsert(self, vec_store):
        items = []
        for i in range(10):
            items.append((
                f"chunk-{i}",
                "tenant-1",
                _random_vector(8, seed=i),
                {"document_id": f"doc-{i // 3}"},
            ))
        vec_store.upsert_batch(items)

        results = vec_store.search(_random_vector(8, seed=0), "tenant-1", k=10)
        assert len(results) == 10

    def test_delete_by_document(self, vec_store):
        vec_store.upsert("c1", "t1", _random_vector(8, seed=1), {"document_id": "doc-1"})
        vec_store.upsert("c2", "t1", _random_vector(8, seed=2), {"document_id": "doc-1"})
        vec_store.upsert("c3", "t1", _random_vector(8, seed=3), {"document_id": "doc-2"})

        vec_store.delete_by_document("doc-1")

        results = vec_store.search(_random_vector(8, seed=1), "t1", k=10)
        assert len(results) == 1
        assert results[0]["chunk_id"] == "c3"

    def test_knn_returns_nearest(self, vec_store):
        # Insert a known vector and a distant one
        target = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        norm = np.linalg.norm(target)
        target = [x / norm for x in target]

        near = [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        norm = np.linalg.norm(near)
        near = [x / norm for x in near]

        far = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        norm = np.linalg.norm(far)
        far = [x / norm for x in far]

        vec_store.upsert("near", "t1", near, {"document_id": "d1"})
        vec_store.upsert("far", "t1", far, {"document_id": "d2"})

        results = vec_store.search(target, "t1", k=2)
        assert results[0]["chunk_id"] == "near"
        assert results[0]["similarity"] > results[1]["similarity"]

    def test_exclude_document(self, vec_store):
        vec = _random_vector(8, seed=42)
        vec_store.upsert("c1", "t1", vec, {"document_id": "doc-1"})
        vec_store.upsert("c2", "t1", _random_vector(8, seed=43), {"document_id": "doc-2"})

        results = vec_store.search(vec, "t1", k=10, exclude_document_id="doc-1")
        assert len(results) == 1
        assert results[0]["document_id"] == "doc-2"


class TestClaimVectors:
    def test_upsert_and_search_claims(self, vec_store):
        vec = _random_vector(8, seed=42)
        vec_store.upsert_claim("claim-1", "t1", "doc-1", "chunk-1", vec)

        results = vec_store.search_claims(vec, "t1", k=5)
        assert len(results) == 1
        assert results[0]["claim_id"] == "claim-1"
        assert results[0]["similarity"] > 0.99

    def test_claim_tenant_isolation(self, vec_store):
        vec = _random_vector(8, seed=42)
        vec_store.upsert_claim("claim-1", "t1", "d1", "c1", vec)
        vec_store.upsert_claim("claim-2", "t2", "d2", "c2", vec)

        results = vec_store.search_claims(vec, "t1", k=10)
        assert len(results) == 1
        assert results[0]["claim_id"] == "claim-1"


class TestGetAllVectors:
    def test_returns_all_tenant_vectors(self, vec_store):
        for i in range(5):
            vec_store.upsert(f"c{i}", "t1", _random_vector(8, seed=i), {"document_id": "d1"})
        vec_store.upsert("cx", "t2", _random_vector(8, seed=99), {"document_id": "d2"})

        results = vec_store.get_all_vectors_for_tenant("t1")
        assert len(results) == 5
        for chunk_id, vector in results:
            assert isinstance(vector, np.ndarray)
            assert vector.shape == (8,)
