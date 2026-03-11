"""
Vector storage and similarity search using sqlite-vec (vec0).

sqlite-vec provides a vec0 virtual table that stores float32 vectors
and supports fast cosine similarity search via KNN queries.

This module manages:
  - Creating the vec0 virtual table
  - Inserting/updating/deleting vectors
  - KNN similarity search with tenant isolation
  - Batch operations for bulk ingestion

Usage:
    store = VectorStore()
    store.ensure_table()
    store.upsert("chunk-uuid", tenant_id, vector, {"doc_id": "...", "doc_type": "report"})
    results = store.search(query_vector, tenant_id, k=10)
"""
import json
import logging
import sqlite3
import struct
import threading
import time
from pathlib import Path

import numpy as np
import sqlite_vec
from django.conf import settings

logger = logging.getLogger(__name__)

# Default DB path alongside Django DB
_DEFAULT_VEC_DB = None


def _get_vec_db_path() -> Path:
    global _DEFAULT_VEC_DB
    if _DEFAULT_VEC_DB is None:
        _DEFAULT_VEC_DB = settings.DATA_DIR / "vec.sqlite3"
    return _DEFAULT_VEC_DB


def _serialize_f32(vector: list[float]) -> bytes:
    """Serialize a float32 vector to bytes for sqlite-vec."""
    return struct.pack(f"{len(vector)}f", *vector)


def _deserialize_f32(data: bytes) -> list[float]:
    """Deserialize bytes back to float32 vector."""
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


class VectorStore:
    """Interface to sqlite-vec for vector storage and KNN search."""

    def __init__(self, db_path: Path | str | None = None, dimensions: int | None = None):
        self._db_path = str(db_path or _get_vec_db_path())
        self._dimensions = dimensions or settings.EMBEDDING_DIMENSIONS
        self._local = threading.local()

        # Pipeline trace collector
        self._trace = None
        # Thread-local trace override for parallel phase execution
        self._trace_local = threading.local()

    def set_trace(self, collector):
        """Set the pipeline trace collector. Use clear_trace() when done."""
        self._trace = collector

    def clear_trace(self):
        """Remove the pipeline trace collector."""
        self._trace = None

    @property
    def _active_trace(self):
        """Return thread-local trace if set, else the global trace."""
        return getattr(self._trace_local, "trace", None) or self._trace

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a per-thread database connection with sqlite-vec loaded."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            # Enable WAL for concurrent reads
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None

    def ensure_tables(self):
        """Create the vec0 virtual table and metadata table if they don't exist."""
        conn = self._get_conn()

        # Metadata table for tenant isolation and filtering
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vec_metadata (
                chunk_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                doc_type TEXT DEFAULT '',
                source_type TEXT DEFAULT '',
                extra JSON DEFAULT '{}'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vec_metadata_tenant
            ON vec_metadata(tenant_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vec_metadata_doc
            ON vec_metadata(document_id)
        """)

        # Add project_id column if it doesn't exist
        try:
            conn.execute("ALTER TABLE vec_metadata ADD COLUMN project_id TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # Column already exists
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vec_metadata_tenant_project
            ON vec_metadata(tenant_id, project_id)
        """)

        # vec0 virtual table for vector storage + KNN
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
                chunk_id TEXT PRIMARY KEY,
                embedding float[{self._dimensions}]
            )
        """)

        # Separate table for claim embeddings
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claim_metadata (
                claim_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_claim_metadata_tenant
            ON claim_metadata(tenant_id)
        """)

        # Add project_id column to claim_metadata if it doesn't exist
        try:
            conn.execute("ALTER TABLE claim_metadata ADD COLUMN project_id TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # Column already exists
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_claim_metadata_tenant_project
            ON claim_metadata(tenant_id, project_id)
        """)

        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_claims USING vec0(
                claim_id TEXT PRIMARY KEY,
                embedding float[{self._dimensions}]
            )
        """)

        conn.commit()
        logger.info("Vector store tables ensured (dimensions=%d)", self._dimensions)

    def upsert(
        self,
        chunk_id: str,
        tenant_id: str,
        vector: list[float],
        metadata: dict | None = None,
        project_id: str = "",
    ):
        """Insert or replace a chunk vector and its metadata."""
        conn = self._get_conn()
        meta = metadata or {}

        conn.execute(
            """INSERT OR REPLACE INTO vec_metadata
               (chunk_id, tenant_id, document_id, doc_type, source_type, extra, project_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                chunk_id,
                tenant_id,
                meta.get("document_id", ""),
                meta.get("doc_type", ""),
                meta.get("source_type", ""),
                json.dumps({k: v for k, v in meta.items()
                           if k not in ("document_id", "doc_type", "source_type")}),
                project_id,
            ),
        )

        conn.execute(
            "INSERT OR REPLACE INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, _serialize_f32(vector)),
        )
        conn.commit()

    def upsert_batch(
        self,
        items: list[tuple[str, str, list[float], dict]],
        project_id: str = "",
    ):
        """Batch upsert: list of (chunk_id, tenant_id, vector, metadata)."""
        t0 = time.monotonic()
        conn = self._get_conn()

        meta_rows = []
        vec_rows = []
        for chunk_id, tenant_id, vector, meta in items:
            meta = meta or {}
            meta_rows.append((
                chunk_id,
                tenant_id,
                meta.get("document_id", ""),
                meta.get("doc_type", ""),
                meta.get("source_type", ""),
                json.dumps({k: v for k, v in meta.items()
                           if k not in ("document_id", "doc_type", "source_type")}),
                project_id,
            ))
            vec_rows.append((chunk_id, _serialize_f32(vector)))

        conn.executemany(
            """INSERT OR REPLACE INTO vec_metadata
               (chunk_id, tenant_id, document_id, doc_type, source_type, extra, project_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            meta_rows,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
            vec_rows,
        )
        conn.commit()

        _trace = self._active_trace
        if _trace:
            _trace.record_event(
                "vec_upsert",
                item_count=len(items),
                duration=time.monotonic() - t0,
            )

    def delete_by_document(self, document_id: str):
        """Remove all vectors for a given document."""
        conn = self._get_conn()
        # Get chunk IDs first
        rows = conn.execute(
            "SELECT chunk_id FROM vec_metadata WHERE document_id = ?", (document_id,)
        ).fetchall()
        chunk_ids = [r[0] for r in rows]

        if chunk_ids:
            placeholders = ",".join("?" * len(chunk_ids))
            conn.execute(f"DELETE FROM vec_chunks WHERE chunk_id IN ({placeholders})", chunk_ids)
            conn.execute(f"DELETE FROM vec_metadata WHERE chunk_id IN ({placeholders})", chunk_ids)
            conn.commit()

    def delete_by_documents(self, document_ids: list[str]):
        """Remove all chunk vectors and claim vectors for a list of documents."""
        conn = self._get_conn()
        if not document_ids:
            return

        placeholders = ",".join("?" * len(document_ids))

        # Delete chunk vectors
        chunk_rows = conn.execute(
            f"SELECT chunk_id FROM vec_metadata WHERE document_id IN ({placeholders})",
            document_ids,
        ).fetchall()
        chunk_ids = [r[0] for r in chunk_rows]
        if chunk_ids:
            ph = ",".join("?" * len(chunk_ids))
            conn.execute(f"DELETE FROM vec_chunks WHERE chunk_id IN ({ph})", chunk_ids)
            conn.execute(f"DELETE FROM vec_metadata WHERE chunk_id IN ({ph})", chunk_ids)

        # Delete claim vectors
        claim_rows = conn.execute(
            f"SELECT claim_id FROM claim_metadata WHERE document_id IN ({placeholders})",
            document_ids,
        ).fetchall()
        claim_ids = [r[0] for r in claim_rows]
        if claim_ids:
            ph = ",".join("?" * len(claim_ids))
            conn.execute(f"DELETE FROM vec_claims WHERE claim_id IN ({ph})", claim_ids)
            conn.execute(f"DELETE FROM claim_metadata WHERE claim_id IN ({ph})", claim_ids)

        conn.commit()

    def search(
        self,
        query_vector: list[float],
        tenant_id: str,
        k: int = 10,
        doc_type: str | None = None,
        exclude_document_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict]:
        """
        KNN search within a tenant's vectors.

        Returns list of dicts with: chunk_id, document_id, distance, similarity, metadata
        """
        t0 = time.monotonic()
        conn = self._get_conn()

        # sqlite-vec KNN query with JOIN to avoid N+1 metadata lookups
        fetch_k = k * 5  # overfetch for tenant/type filtering

        rows = conn.execute(
            """
            SELECT vc.chunk_id, vc.distance,
                   m.tenant_id, m.document_id, m.doc_type, m.source_type, m.extra, m.project_id
            FROM vec_chunks vc
            LEFT JOIN vec_metadata m ON m.chunk_id = vc.chunk_id
            WHERE vc.embedding MATCH ?
              AND k = ?
            ORDER BY vc.distance
            """,
            (_serialize_f32(query_vector), fetch_k),
        ).fetchall()

        results = []
        for chunk_id, distance, row_tenant, doc_id, row_doc_type, source_type, extra_json, row_project in rows:
            if not row_tenant:
                continue
            if row_tenant != tenant_id:
                continue
            if project_id and row_project != project_id:
                continue
            if doc_type and row_doc_type != doc_type:
                continue
            if exclude_document_id and doc_id == exclude_document_id:
                continue

            similarity = 1.0 - (distance ** 2) / 2.0

            extra = json.loads(extra_json) if extra_json else {}
            results.append({
                "chunk_id": chunk_id,
                "document_id": doc_id,
                "doc_type": row_doc_type,
                "source_type": source_type,
                "distance": distance,
                "similarity": max(0.0, min(1.0, similarity)),
                **extra,
            })

            if len(results) >= k:
                break

        _trace = self._active_trace
        if _trace:
            _trace.record_event(
                "vec_search",
                result_count=len(results),
                duration=time.monotonic() - t0,
            )

        return results

    def search_claims(
        self,
        query_vector: list[float],
        tenant_id: str,
        k: int = 10,
        project_id: str | None = None,
    ) -> list[dict]:
        """KNN search on claim embeddings."""
        t0 = time.monotonic()
        conn = self._get_conn()
        fetch_k = k * 5

        rows = conn.execute(
            """
            SELECT vc.claim_id, vc.distance,
                   m.tenant_id, m.document_id, m.chunk_id, m.project_id
            FROM vec_claims vc
            LEFT JOIN claim_metadata m ON m.claim_id = vc.claim_id
            WHERE vc.embedding MATCH ?
              AND k = ?
            ORDER BY vc.distance
            """,
            (_serialize_f32(query_vector), fetch_k),
        ).fetchall()

        results = []
        for claim_id, distance, row_tenant, doc_id, chunk_id, row_project in rows:
            if not row_tenant or row_tenant != tenant_id:
                continue
            if project_id and row_project != project_id:
                continue

            similarity = 1.0 - (distance ** 2) / 2.0
            results.append({
                "claim_id": claim_id,
                "document_id": doc_id,
                "chunk_id": chunk_id,
                "distance": distance,
                "similarity": max(0.0, min(1.0, similarity)),
            })

            if len(results) >= k:
                break

        _trace = self._active_trace
        if _trace:
            _trace.record_event(
                "vec_search_claims",
                result_count=len(results),
                duration=time.monotonic() - t0,
            )

        return results

    def upsert_claim(self, claim_id: str, tenant_id: str, document_id: str,
                     chunk_id: str, vector: list[float], project_id: str = ""):
        """Insert or replace a claim vector."""
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO claim_metadata (claim_id, tenant_id, document_id, chunk_id, project_id) VALUES (?, ?, ?, ?, ?)",
            (claim_id, tenant_id, document_id, chunk_id, project_id),
        )
        conn.execute(
            "INSERT OR REPLACE INTO vec_claims (claim_id, embedding) VALUES (?, ?)",
            (claim_id, _serialize_f32(vector)),
        )
        conn.commit()

    def get_all_vectors_for_tenant(self, tenant_id: str, project_id: str | None = None) -> list[tuple[str, np.ndarray]]:
        """Retrieve all chunk vectors for a tenant (for clustering). Optionally filter by project."""
        conn = self._get_conn()

        if project_id:
            rows = conn.execute(
                """SELECT m.chunk_id, v.embedding
                   FROM vec_metadata m
                   JOIN vec_chunks v ON v.chunk_id = m.chunk_id
                   WHERE m.tenant_id = ? AND m.project_id = ?""",
                (tenant_id, project_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT m.chunk_id, v.embedding
                   FROM vec_metadata m
                   JOIN vec_chunks v ON v.chunk_id = m.chunk_id
                   WHERE m.tenant_id = ?""",
                (tenant_id,),
            ).fetchall()

        return [
            (chunk_id, np.array(_deserialize_f32(emb), dtype=np.float32))
            for chunk_id, emb in rows
        ]

    def get_chunk_embeddings_batch(self, chunk_ids: list[str]) -> dict[str, np.ndarray]:
        """Batch-load chunk embeddings by IDs. Returns {chunk_id: vector}."""
        if not chunk_ids:
            return {}
        conn = self._get_conn()
        results = {}
        # Process in batches of 500 to avoid SQLite variable limit
        for i in range(0, len(chunk_ids), 500):
            batch = chunk_ids[i : i + 500]
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT chunk_id, embedding FROM vec_chunks WHERE chunk_id IN ({placeholders})",
                batch,
            ).fetchall()
            for chunk_id, emb in rows:
                results[chunk_id] = np.array(_deserialize_f32(emb), dtype=np.float32)
        return results

    def get_all_claim_embeddings_for_tenant(self, tenant_id: str, project_id: str | None = None) -> dict[str, np.ndarray]:
        """Batch-load all claim embeddings for a tenant. Returns {claim_id: vector}."""
        conn = self._get_conn()
        if project_id:
            rows = conn.execute(
                """SELECT m.claim_id, v.embedding
                   FROM claim_metadata m
                   JOIN vec_claims v ON v.claim_id = m.claim_id
                   WHERE m.tenant_id = ? AND m.project_id = ?""",
                (tenant_id, project_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT m.claim_id, v.embedding
                   FROM claim_metadata m
                   JOIN vec_claims v ON v.claim_id = m.claim_id
                   WHERE m.tenant_id = ?""",
                (tenant_id,),
            ).fetchall()
        return {
            claim_id: np.array(_deserialize_f32(emb), dtype=np.float32)
            for claim_id, emb in rows
        }

    def search_batch(
        self,
        query_vectors: list[list[float]],
        tenant_id: str,
        k: int = 10,
        project_id: str | None = None,
    ) -> list[list[dict]]:
        """Batch search: in-memory cosine similarity against all tenant vectors.

        Much faster than N individual KNN queries when searching many vectors
        against the same corpus. Loads all tenant vectors once, then computes
        cosine similarity via matrix multiplication.
        """
        t0 = time.monotonic()
        if not query_vectors:
            return []

        # Load all tenant vectors once
        all_vectors = self.get_all_vectors_for_tenant(tenant_id, project_id=project_id)
        if not all_vectors:
            return [[] for _ in query_vectors]

        MAX_CORPUS_SIZE = 500_000
        if len(all_vectors) > MAX_CORPUS_SIZE:
            logger.warning(
                "search_batch: corpus size (%d vectors) exceeds recommended limit (%d). "
                "Consider using individual KNN queries or migrating to a dedicated vector database.",
                len(all_vectors), MAX_CORPUS_SIZE,
            )

        corpus_ids = [v[0] for v in all_vectors]
        corpus_matrix = np.stack([v[1] for v in all_vectors])

        # Normalize corpus
        corpus_norms = np.linalg.norm(corpus_matrix, axis=1, keepdims=True)
        corpus_norms = np.where(corpus_norms > 0, corpus_norms, 1.0)
        corpus_normed = corpus_matrix / corpus_norms

        # Normalize queries
        query_matrix = np.array(query_vectors, dtype=np.float32)
        query_norms = np.linalg.norm(query_matrix, axis=1, keepdims=True)
        query_norms = np.where(query_norms > 0, query_norms, 1.0)
        query_normed = query_matrix / query_norms

        # Cosine similarity: (Q x D) @ (C x D).T → (Q x C)
        sim_matrix = query_normed @ corpus_normed.T

        # Load metadata for matched chunks (batch)
        conn = self._get_conn()
        # Pre-fetch all metadata for this tenant/project
        if project_id:
            meta_rows = conn.execute(
                "SELECT chunk_id, document_id, doc_type, source_type, extra FROM vec_metadata WHERE tenant_id = ? AND project_id = ?",
                (tenant_id, project_id),
            ).fetchall()
        else:
            meta_rows = conn.execute(
                "SELECT chunk_id, document_id, doc_type, source_type, extra FROM vec_metadata WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
        meta_map = {row[0]: row for row in meta_rows}

        all_results = []
        for q_idx in range(len(query_vectors)):
            sims = sim_matrix[q_idx]
            actual_k = min(k, len(corpus_ids))
            top_indices = np.argpartition(sims, -actual_k)[-actual_k:]
            top_indices = top_indices[np.argsort(sims[top_indices])[::-1]]

            results = []
            for idx in top_indices:
                chunk_id = corpus_ids[idx]
                similarity = float(sims[idx])
                meta = meta_map.get(chunk_id)
                if not meta:
                    continue
                _, doc_id, doc_type, source_type, extra_json = meta
                extra = json.loads(extra_json) if extra_json else {}
                results.append({
                    "chunk_id": chunk_id,
                    "document_id": doc_id,
                    "doc_type": doc_type,
                    "source_type": source_type,
                    "distance": 0.0,
                    "similarity": max(0.0, min(1.0, similarity)),
                    **extra,
                })
            all_results.append(results)

        _trace = self._active_trace
        if _trace:
            total_results = sum(len(r) for r in all_results)
            _trace.record_event(
                "vec_search",
                item_count=len(query_vectors),
                result_count=total_results,
                duration=time.monotonic() - t0,
            )

        return all_results

    def upsert_claims_batch(self, items: list[tuple[str, str, str, str, list[float]]], project_id: str = ""):
        """Batch upsert claims: list of (claim_id, tenant_id, document_id, chunk_id, vector)."""
        if not items:
            return
        t0 = time.monotonic()
        conn = self._get_conn()
        meta_rows = [(cid, tid, did, chid, project_id) for cid, tid, did, chid, _ in items]
        vec_rows = [(cid, _serialize_f32(vec)) for cid, _, _, _, vec in items]
        conn.executemany(
            "INSERT OR REPLACE INTO claim_metadata (claim_id, tenant_id, document_id, chunk_id, project_id) VALUES (?, ?, ?, ?, ?)",
            meta_rows,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO vec_claims (claim_id, embedding) VALUES (?, ?)",
            vec_rows,
        )
        conn.commit()

        _trace = self._active_trace
        if _trace:
            _trace.record_event(
                "vec_upsert",
                item_count=len(items),
                duration=time.monotonic() - t0,
            )


# Module-level singleton (thread-safe)
import threading

_store: VectorStore | None = None
_store_lock = threading.Lock()


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = VectorStore()
                _store.ensure_tables()
    return _store
