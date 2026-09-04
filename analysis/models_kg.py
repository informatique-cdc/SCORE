"""
Knowledge graph models: entities and typed relations extracted from the corpus.

Unlike the ``nsg`` semantic graph — which links concepts by weighted
co-occurrence and cannot say *how* two concepts relate — these relations carry
a predicate and a provenance trail back to the source documents.

The graph is built one ``TopicCluster`` at a time: entity standardisation is far
more reliable inside a thematically homogeneous cluster, and canonical forms are
merged across clusters afterwards. An entity found in several clusters is a
bridge between topics.
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _
from tenants.models import ProjectScopedModel


class KnowledgeGraphRun(ProjectScopedModel):
    """One construction of the knowledge graph for an analysis job."""

    class Source(models.TextChoices):
        EXTRACT = "extract", _("Extraction LLM dédiée")
        HYBRID = "hybrid", _("Extraction + affirmations")
        CLAIMS = "claims", _("Affirmations existantes")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis_job = models.ForeignKey(
        "analysis.AnalysisJob", on_delete=models.CASCADE, related_name="kg_runs"
    )

    source = models.CharField(max_length=10, choices=Source.choices, default=Source.EXTRACT)
    config_snapshot = models.JSONField(
        default=dict, blank=True, help_text="Effective config used for this build"
    )

    entity_count = models.PositiveIntegerField(default=0)
    relation_count = models.PositiveIntegerField(default=0)
    inferred_count = models.PositiveIntegerField(default=0)
    cluster_count = models.PositiveIntegerField(default=0)
    bridge_count = models.PositiveIntegerField(
        default=0, help_text="Entities appearing in more than one cluster"
    )
    duration_seconds = models.FloatField(default=0.0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"KG {self.id!s:.8} ({self.entity_count} entités, {self.relation_count} relations)"


class KGEntity(ProjectScopedModel):
    """A node: one standardised entity, with the variants it absorbed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(KnowledgeGraphRun, on_delete=models.CASCADE, related_name="entities")

    canonical = models.CharField(max_length=300, help_text="Normalised form, used for matching")
    label = models.CharField(max_length=300, help_text="Display form: most frequent variant")
    aliases = models.JSONField(
        default=list, blank=True, help_text="Absorbed variants — makes standardisation auditable"
    )
    search_key = models.CharField(
        max_length=1000,
        blank=True,
        default="",
        db_index=True,
        help_text="Label + aliases, normalised. SQLite cannot index inside a JSONField.",
    )

    frequency = models.PositiveIntegerField(default=0, help_text="Occurrences across triples")
    degree = models.PositiveIntegerField(default=0)
    centrality = models.FloatField(
        default=0.0, help_text="Normalised betweenness; drives node size"
    )
    doc_count = models.PositiveIntegerField(default=0)

    # Clusters replace Louvain communities: they are computed from embeddings and
    # carry an LLM-generated label, so the legend reads as topics, not numbers.
    main_cluster = models.ForeignKey(
        "analysis.TopicCluster",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="kg_entities",
    )
    cluster_count = models.PositiveIntegerField(
        default=0, help_text="Number of clusters this entity appears in; > 1 means bridge"
    )

    pos_x = models.FloatField(default=0.0, help_text="Layout precomputed at build time")
    pos_y = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("run", "canonical")
        indexes = [
            models.Index(fields=["run", "main_cluster"]),
            models.Index(fields=["run", "-centrality"]),
        ]
        ordering = ["-centrality"]

    def __str__(self):
        return self.label


class KGRelation(ProjectScopedModel):
    """An edge: a typed, sourced relation between two entities."""

    class InferenceKind(models.TextChoices):
        NONE = "", _("Constatée")
        TRANSITIVE = "transitive", _("Transitive")
        CROSS_CLUSTER = "cross_cluster", _("Entre clusters")
        WITHIN_CLUSTER = "within_cluster", _("Dans un cluster")
        LEXICAL = "lexical", _("Similarité lexicale")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(KnowledgeGraphRun, on_delete=models.CASCADE, related_name="relations")

    subject = models.ForeignKey(KGEntity, on_delete=models.CASCADE, related_name="relations_out")
    object = models.ForeignKey(KGEntity, on_delete=models.CASCADE, related_name="relations_in")
    predicate = models.CharField(max_length=120, help_text="Normalised, 3 words at most")

    weight = models.FloatField(default=1.0, help_text="Number of triples merged into this edge")
    confidence = models.FloatField(default=1.0)

    # SET_NULL, never CASCADE: _cleanup_phase("clustering") deletes every
    # TopicCluster of the job, which would silently wipe the graph on resume.
    cluster = models.ForeignKey(
        "analysis.TopicCluster",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="kg_relations",
    )

    inferred = models.BooleanField(default=False)
    inference_kind = models.CharField(
        max_length=15, choices=InferenceKind.choices, blank=True, default=""
    )

    documents = models.ManyToManyField(
        "ingestion.Document", blank=True, related_name="kg_relations"
    )
    evidence = models.JSONField(default=list, blank=True, help_text="Up to 3 source snippets")

    class Meta:
        unique_together = ("run", "subject", "object", "predicate")
        indexes = [
            models.Index(fields=["run", "predicate"]),
            models.Index(fields=["run", "inferred"]),
        ]

    def __str__(self):
        return f"{self.subject_id} —{self.predicate}→ {self.object_id}"
