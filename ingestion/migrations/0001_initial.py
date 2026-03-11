import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("connectors", "0001_initial"),
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Document",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "source_id",
                    models.CharField(
                        help_text="Unique ID in the source system (URL, file path, page ID)",
                        max_length=1000,
                    ),
                ),
                ("title", models.CharField(max_length=1000)),
                ("source_url", models.URLField(blank=True, default="", max_length=2000)),
                ("author", models.CharField(blank=True, default="", max_length=500)),
                (
                    "doc_type",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="File type or document category",
                        max_length=50,
                    ),
                ),
                (
                    "path",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Path/hierarchy in the source system",
                        max_length=2000,
                    ),
                ),
                (
                    "content_hash",
                    models.CharField(
                        help_text="SHA-256 hash of extracted text content", max_length=64
                    ),
                ),
                (
                    "source_version",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Version ID from source (etag, revision, modified timestamp)",
                        max_length=200,
                    ),
                ),
                ("version_number", models.PositiveIntegerField(default=1)),
                ("source_created_at", models.DateTimeField(blank=True, null=True)),
                ("source_modified_at", models.DateTimeField(blank=True, null=True)),
                ("word_count", models.PositiveIntegerField(default=0)),
                ("chunk_count", models.PositiveIntegerField(default=0)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "En attente"),
                            ("ingested", "Ingéré"),
                            ("embedding", "Vectorisation"),
                            ("ready", "Prêt"),
                            ("error", "Erreur"),
                            ("deleted", "Supprimé"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "connector",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="documents",
                        to="connectors.connectorconfig",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(class)s_set",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["-source_modified_at"],
                "unique_together": {("tenant", "connector", "source_id")},
                "indexes": [
                    models.Index(
                        fields=["tenant", "status"], name="ingestion_d_tenant__6b1cdf_idx"
                    ),
                    models.Index(fields=["content_hash"], name="ingestion_d_content_2972b7_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="DocumentChunk",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "chunk_index",
                    models.PositiveIntegerField(help_text="Position of chunk within document"),
                ),
                ("content", models.TextField(help_text="Chunk text content")),
                ("token_count", models.PositiveIntegerField(default=0)),
                (
                    "heading_path",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Hierarchical heading path, e.g. 'Chapter 1 > Section 2 > Subsection A'",
                        max_length=1000,
                    ),
                ),
                ("content_hash", models.CharField(max_length=64)),
                ("has_embedding", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="ingestion.document",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(class)s_set",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["document", "chunk_index"],
                "unique_together": {("document", "chunk_index")},
                "indexes": [
                    models.Index(
                        fields=["tenant", "has_embedding"], name="ingestion_d_tenant__306729_idx"
                    ),
                    models.Index(fields=["content_hash"], name="ingestion_d_content_c34532_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="IngestionJob",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "En attente"),
                            ("running", "En cours"),
                            ("completed", "Terminé"),
                            ("failed", "Échoué"),
                        ],
                        default="queued",
                        max_length=20,
                    ),
                ),
                ("total_documents", models.PositiveIntegerField(default=0)),
                ("processed_documents", models.PositiveIntegerField(default=0)),
                ("new_documents", models.PositiveIntegerField(default=0)),
                ("updated_documents", models.PositiveIntegerField(default=0)),
                ("deleted_documents", models.PositiveIntegerField(default=0)),
                ("error_count", models.PositiveIntegerField(default=0)),
                ("celery_task_id", models.CharField(blank=True, default="", max_length=255)),
                ("error_message", models.TextField(blank=True, default="")),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "connector",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ingestion_jobs",
                        to="connectors.connectorconfig",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(class)s_set",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
