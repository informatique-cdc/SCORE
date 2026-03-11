"""
Document and chunk models.

Documents represent ingested files/pages. Each document is split into chunks
for embedding and analysis. Content hashing enables incremental re-ingestion.
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _
from tenants.models import ProjectScopedModel, TenantScopedModel


class Document(ProjectScopedModel):
    """A single ingested document (file, wiki page, etc.)."""

    class Status(models.TextChoices):
        PENDING = "pending", _("En attente")
        INGESTED = "ingested", _("Ingéré")
        EMBEDDING = "embedding", _("Vectorisation")
        READY = "ready", _("Prêt")
        ERROR = "error", _("Erreur")
        DELETED = "deleted", _("Supprimé")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connector = models.ForeignKey(
        "connectors.ConnectorConfig", on_delete=models.CASCADE, related_name="documents"
    )

    # Identity & versioning
    source_id = models.CharField(
        max_length=1000,
        help_text="Unique ID in the source system (URL, file path, page ID)",
    )
    title = models.CharField(max_length=1000)
    source_url = models.URLField(max_length=2000, blank=True, default="")
    author = models.CharField(max_length=500, blank=True, default="")
    doc_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="File type or document category",
    )
    path = models.CharField(
        max_length=2000,
        blank=True,
        default="",
        help_text="Path/hierarchy in the source system",
    )

    # Versioning for incremental re-ingestion
    content_hash = models.CharField(
        max_length=64,
        help_text="SHA-256 hash of extracted text content",
    )
    source_version = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Version ID from source (etag, revision, modified timestamp)",
    )
    version_number = models.PositiveIntegerField(default=1)

    # Metadata
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_modified_at = models.DateTimeField(null=True, blank=True)
    word_count = models.PositiveIntegerField(default=0)
    chunk_count = models.PositiveIntegerField(default=0)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("tenant", "project", "connector", "source_id")
        ordering = ["-source_modified_at"]
        indexes = [
            models.Index(fields=["tenant", "project", "status"]),
            models.Index(fields=["content_hash"]),
        ]

    def __str__(self):
        return self.title


class DocumentChunk(TenantScopedModel):
    """A chunk of a document, used for embedding and analysis."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")

    chunk_index = models.PositiveIntegerField(help_text="Position of chunk within document")
    content = models.TextField(help_text="Chunk text content")
    token_count = models.PositiveIntegerField(default=0)

    # Heading context for heading-aware chunking
    heading_path = models.CharField(
        max_length=1000,
        blank=True,
        default="",
        help_text="Hierarchical heading path, e.g. 'Chapter 1 > Section 2 > Subsection A'",
    )

    # Hash for dedup at chunk level
    content_hash = models.CharField(max_length=64)

    # Embedding status
    has_embedding = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("document", "chunk_index")
        ordering = ["document", "chunk_index"]
        indexes = [
            models.Index(fields=["tenant", "has_embedding"]),
            models.Index(fields=["content_hash"]),
        ]

    def __str__(self):
        return f"{self.document.title} [chunk {self.chunk_index}]"


class IngestionJob(ProjectScopedModel):
    """Tracks an ingestion run for a connector."""

    class Status(models.TextChoices):
        QUEUED = "queued", _("En attente")
        RUNNING = "running", _("En cours")
        COMPLETED = "completed", _("Terminé")
        FAILED = "failed", _("Échoué")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connector = models.ForeignKey(
        "connectors.ConnectorConfig", on_delete=models.CASCADE, related_name="ingestion_jobs"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)

    # Progress tracking
    total_documents = models.PositiveIntegerField(default=0)
    processed_documents = models.PositiveIntegerField(default=0)
    new_documents = models.PositiveIntegerField(default=0)
    updated_documents = models.PositiveIntegerField(default=0)
    deleted_documents = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)

    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Ingestion {self.connector.name} @ {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def progress_pct(self):
        if self.total_documents == 0:
            return 0
        return int(100 * self.processed_documents / self.total_documents)
