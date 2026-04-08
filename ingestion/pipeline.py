"""
Ingestion pipeline: orchestrates fetching, extraction, chunking, embedding, and storage.

The pipeline handles:
  1. Incremental sync: detect new/changed/deleted documents via content hash + version
  2. Text extraction from various formats
  3. Chunking with heading awareness
  4. Embedding generation (batched)
  5. Vector storage in sqlite-vec
"""

import logging
from datetime import datetime

from django.utils import timezone as django_tz

from connectors.base import BaseConnector, RawDocument, get_connector
from connectors.models import ConnectorConfig
from ingestion.chunking import chunk_document
from ingestion.extraction import extract_text
from ingestion.hashing import hash_content
from ingestion.models import Document, DocumentChunk, IngestionJob
from llm.client import get_llm_client
from vectorstore.store import get_vector_store

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Orchestrates the full document ingestion workflow."""

    def __init__(self, job: IngestionJob):
        self.job = job
        self.connector_config: ConnectorConfig = job.connector
        self.tenant = job.tenant
        self.project = job.project
        self.connector: BaseConnector = get_connector(
            self.connector_config.connector_type,
            self.connector_config.config,
            self.connector_config.get_secret(),
        )
        self.llm = get_llm_client()
        self.vec_store = get_vector_store()
        self._stats = {
            "new": 0,
            "updated": 0,
            "deleted": 0,
            "errors": 0,
            "unchanged": 0,
        }

    def run(self):
        """Execute the full ingestion pipeline."""
        logger.info(
            "Starting ingestion for connector=%s tenant=%s",
            self.connector_config.name,
            self.tenant.slug,
        )

        self.job.status = IngestionJob.Status.RUNNING
        self.job.started_at = django_tz.now()
        self.job.save()

        try:
            # Step 1: Get known document versions
            known_docs = Document.objects.filter(
                project=self.project,
                connector=self.connector_config,
            ).exclude(status=Document.Status.DELETED)

            known_versions = {doc.source_id: doc.source_version for doc in known_docs}

            # Step 2: Detect changes
            new_or_changed, deleted_ids = self.connector.list_changed_documents(known_versions)
            total = len(new_or_changed) + len(deleted_ids)
            self.job.total_documents = total
            self.job.save()

            # Step 3: Process deletions
            for source_id in deleted_ids:
                self._handle_deletion(source_id)

            # Step 4: Process new/changed documents
            for i, doc_info in enumerate(new_or_changed):
                try:
                    self._process_document(doc_info)
                except Exception as e:
                    logger.error("Error processing document %s: %s", doc_info.get("source_id"), e)
                    self._stats["errors"] += 1

                # Update progress
                self.job.processed_documents = i + 1 + len(deleted_ids)
                self.job.save()

            # Step 5: Update job status
            self.job.status = IngestionJob.Status.COMPLETED
            self.job.new_documents = self._stats["new"]
            self.job.updated_documents = self._stats["updated"]
            self.job.deleted_documents = self._stats["deleted"]
            self.job.error_count = self._stats["errors"]
            self.job.completed_at = django_tz.now()
            self.job.save()

            # Update connector last sync
            self.connector_config.last_sync_at = django_tz.now()
            self.connector_config.last_sync_status = "success"
            self.connector_config.save()

            logger.info(
                "Ingestion completed: new=%d updated=%d deleted=%d errors=%d",
                self._stats["new"],
                self._stats["updated"],
                self._stats["deleted"],
                self._stats["errors"],
            )

        except Exception as e:
            logger.exception("Ingestion pipeline failed: %s", e)
            self.job.status = IngestionJob.Status.FAILED
            self.job.error_message = str(e)
            self.job.completed_at = django_tz.now()
            self.job.save()

            self.connector_config.last_sync_status = "failed"
            self.connector_config.save()
            raise

    def _process_document(self, doc_info: dict):
        """Fetch, extract, chunk, embed, and store a single document."""
        source_id = doc_info["source_id"]

        # Fetch content
        raw_doc: RawDocument = self.connector.fetch_document(source_id)

        # Extract text
        extracted = extract_text(raw_doc.content, raw_doc.content_type)
        if not extracted.text:
            logger.warning("Empty text extraction for source_id=%s", source_id)
            return

        # Hash for change detection
        content_hash = hash_content(extracted.text)

        # Check if document exists and is unchanged
        existing = Document.objects.filter(
            project=self.project,
            connector=self.connector_config,
            source_id=source_id,
        ).first()

        if existing and existing.content_hash == content_hash:
            self._stats["unchanged"] += 1
            return

        # Parse dates
        source_modified_at = None
        if raw_doc.source_modified_at:
            source_modified_at = raw_doc.source_modified_at
        elif doc_info.get("source_modified_at"):
            try:
                source_modified_at = datetime.fromisoformat(
                    str(doc_info["source_modified_at"]).replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        is_update = existing is not None
        if is_update:
            doc = existing
            doc.title = raw_doc.title or doc_info.get("title", "")
            doc.content_hash = content_hash
            doc.source_version = raw_doc.source_version
            doc.version_number += 1
            doc.word_count = extracted.word_count
            doc.source_modified_at = source_modified_at
            doc.author = raw_doc.author
            doc.path = raw_doc.path
            doc.doc_type = raw_doc.doc_type or doc_info.get("content_type", "")
            doc.source_url = raw_doc.source_url
            doc.status = Document.Status.INGESTED
            doc.save()

            # Delete old chunks and vectors
            self.vec_store.delete_by_document(str(doc.id))
            doc.chunks.all().delete()

            self._stats["updated"] += 1
        else:
            doc = Document.objects.create(
                tenant=self.tenant,
                project=self.project,
                connector=self.connector_config,
                source_id=source_id,
                title=raw_doc.title or doc_info.get("title", ""),
                content_hash=content_hash,
                source_version=raw_doc.source_version,
                source_url=raw_doc.source_url,
                author=raw_doc.author,
                doc_type=raw_doc.doc_type or doc_info.get("content_type", ""),
                path=raw_doc.path,
                word_count=extracted.word_count,
                source_created_at=raw_doc.source_created_at,
                source_modified_at=source_modified_at,
                status=Document.Status.INGESTED,
            )
            self._stats["new"] += 1

        # Chunk the document
        chunks = chunk_document(extracted.text, extracted.headings)
        doc.chunk_count = len(chunks)
        doc.save()

        # Create chunk records
        chunk_objects = []
        for chunk in chunks:
            chunk_obj = DocumentChunk(
                tenant=self.tenant,
                document=doc,
                chunk_index=chunk.index,
                content=chunk.content,
                token_count=chunk.token_count,
                heading_path=chunk.heading_path,
                content_hash=chunk.content_hash,
            )
            chunk_objects.append(chunk_obj)

        DocumentChunk.objects.bulk_create(chunk_objects)

        # Generate embeddings and store in vector DB
        self._embed_and_store_chunks(doc, chunk_objects)

        doc.status = Document.Status.READY
        doc.save()

    def _embed_and_store_chunks(self, doc: Document, chunks: list[DocumentChunk]):
        """Generate embeddings for chunks and store in sqlite-vec."""
        if not chunks:
            return

        texts = [c.content for c in chunks]
        embeddings = self.llm.embed(texts)

        vec_items = []
        for chunk, embedding in zip(chunks, embeddings):
            vec_items.append(
                (
                    str(chunk.id),
                    str(self.tenant.id),
                    embedding,
                    {
                        "document_id": str(doc.id),
                        "doc_type": doc.doc_type,
                        "source_type": self.connector_config.connector_type,
                    },
                )
            )

        self.vec_store.upsert_batch(vec_items, project_id=str(self.project.id))

        # Mark chunks as having embeddings
        DocumentChunk.objects.filter(id__in=[c.id for c in chunks]).update(has_embedding=True)

        doc.status = Document.Status.READY
        doc.save()

    def _handle_deletion(self, source_id: str):
        """Mark a document as deleted and remove its vectors."""
        doc = Document.objects.filter(
            project=self.project,
            connector=self.connector_config,
            source_id=source_id,
        ).first()

        if doc:
            self.vec_store.delete_by_document(str(doc.id))
            doc.status = Document.Status.DELETED
            doc.save()
            self._stats["deleted"] += 1
