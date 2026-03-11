"""
Analysis result models: duplicates, contradictions, claims, clusters, gaps, analysis jobs.
"""
import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _
from tenants.models import ProjectScopedModel


class AnalysisJob(ProjectScopedModel):
    """Tracks a full analysis run (duplicates + contradictions + clustering + gaps)."""

    class Status(models.TextChoices):
        QUEUED = "queued", _("En attente")
        RUNNING = "running", _("En cours")
        COMPLETED = "completed", _("Terminé")
        FAILED = "failed", _("Échoué")
        CANCELLED = "cancelled", _("Annulé")

    class Phase(models.TextChoices):
        DUPLICATES = "duplicates", _("Détection des doublons")
        CLAIMS = "claims", _("Extraction des affirmations")
        SEMANTIC_GRAPH = "semantic_graph", _("Graphe sémantique")
        CONTRADICTIONS = "contradictions", _("Détection des contradictions")
        CLUSTERING = "clustering", _("Clustering thématique")
        GAPS = "gaps", _("Détection des lacunes")
        TREE = "tree", _("Index arborescent")
        HALLUCINATION = "hallucination", _("Détection des risques d'hallucination")
        AUDIT_HYGIENE = "audit_hygiene", _("Audit : Hygiène")
        AUDIT_STRUCTURE = "audit_structure", _("Audit : Structure")
        AUDIT_COVERAGE = "audit_coverage", _("Audit : Couverture")
        AUDIT_COHERENCE = "audit_coherence", _("Audit : Cohérence")
        AUDIT_RETRIEVABILITY = "audit_retrievability", _("Audit : Retrievability")
        AUDIT_GOVERNANCE = "audit_governance", _("Audit : Gouvernance")
        DONE = "done", _("Terminé")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    current_phase = models.CharField(max_length=30, choices=Phase.choices, default=Phase.DUPLICATES)
    includes_audit = models.BooleanField(default=True)
    progress_pct = models.PositiveIntegerField(default=0)
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    phase_detail = models.JSONField(default=dict, blank=True)
    config_overrides = models.JSONField(
        default=dict, blank=True,
        help_text="Per-job analysis config overrides (merged on top of config.yaml defaults)",
    )

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Analysis {self.id!s:.8} ({self.status})"


class DuplicateGroup(ProjectScopedModel):
    """A group of documents detected as duplicates or near-duplicates."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis_job = models.ForeignKey(AnalysisJob, on_delete=models.CASCADE, related_name="duplicate_groups")

    # Recommended action
    class Action(models.TextChoices):
        MERGE = "merge", _("Fusionner")
        DELETE_OLDER = "delete_older", _("Supprimer l'ancien")
        REVIEW = "review", _("Vérification manuelle")
        KEEP = "keep", _("Conserver les deux (liés, pas doublons)")

    recommended_action = models.CharField(max_length=20, choices=Action.choices, default=Action.REVIEW)
    rationale = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class DuplicatePair(ProjectScopedModel):
    """A pair of documents within a duplicate group, with scoring details."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(DuplicateGroup, on_delete=models.CASCADE, related_name="pairs")
    doc_a = models.ForeignKey("ingestion.Document", on_delete=models.CASCADE, related_name="dup_pairs_a")
    doc_b = models.ForeignKey("ingestion.Document", on_delete=models.CASCADE, related_name="dup_pairs_b")

    # Individual similarity scores
    semantic_score = models.FloatField(help_text="Cosine similarity of document embeddings")
    lexical_score = models.FloatField(help_text="MinHash Jaccard similarity")
    metadata_score = models.FloatField(help_text="Title/path/author similarity")
    combined_score = models.FloatField(help_text="Weighted combination")

    # Cross-encoder / LLM verification
    verified = models.BooleanField(default=False)
    verification_result = models.CharField(
        max_length=20, blank=True, default="",
        help_text="duplicate / related / unrelated",
    )
    verification_confidence = models.FloatField(null=True, blank=True)
    verification_evidence = models.TextField(
        blank=True, default="",
        help_text="LLM explanation of why this is/isn't a duplicate",
    )

    # Evidence: which passages triggered the match
    evidence_chunks_a = models.JSONField(
        default=list, help_text="Chunk IDs and snippets from doc A"
    )
    evidence_chunks_b = models.JSONField(
        default=list, help_text="Chunk IDs and snippets from doc B"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("group", "doc_a", "doc_b")
        ordering = ["-combined_score"]


class Claim(ProjectScopedModel):
    """An atomic claim extracted from a document chunk."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey("ingestion.Document", on_delete=models.CASCADE, related_name="claims")
    chunk = models.ForeignKey("ingestion.DocumentChunk", on_delete=models.CASCADE, related_name="claims")

    # Structured claim
    subject = models.CharField(max_length=500)
    predicate = models.CharField(max_length=500)
    object_value = models.CharField(max_length=1000)
    qualifiers = models.JSONField(default=dict, help_text="Additional qualifiers (date, version, scope)")
    claim_date = models.DateField(null=True, blank=True, help_text="Date the claim pertains to")
    raw_text = models.TextField(help_text="Original passage text")

    # For vector search on claims
    has_embedding = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "project", "subject"]),
        ]

    def __str__(self):
        return f"{self.subject} {self.predicate} {self.object_value}"

    @property
    def as_text(self):
        return f"{self.subject} {self.predicate} {self.object_value}"


class ContradictionPair(ProjectScopedModel):
    """Two claims that contradict each other or where one is outdated."""

    class Classification(models.TextChoices):
        CONTRADICTION = "contradiction", _("Contradiction")
        OUTDATED = "outdated", _("Obsolète")
        ENTAILMENT = "entailment", _("Implication")
        UNRELATED = "unrelated", _("Sans rapport")

    class Severity(models.TextChoices):
        HIGH = "high", _("Élevée")
        MEDIUM = "medium", _("Moyenne")
        LOW = "low", _("Faible")

    class Resolution(models.TextChoices):
        UNRESOLVED = "", _("Non traité")
        RESOLVED = "resolved", _("Traité")
        KEPT = "kept", _("Conservé")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis_job = models.ForeignKey(AnalysisJob, on_delete=models.CASCADE, related_name="contradictions")
    claim_a = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="contradiction_pairs_a")
    claim_b = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="contradiction_pairs_b")

    classification = models.CharField(max_length=20, choices=Classification.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MEDIUM)
    confidence = models.FloatField(help_text="LLM confidence in this classification")
    evidence = models.TextField(help_text="LLM explanation with citations")

    resolution = models.CharField(
        max_length=10, choices=Resolution.choices, default="", blank=True,
    )

    # For outdated: which is the newer authoritative claim
    authoritative_claim = models.ForeignKey(
        Claim, null=True, blank=True, on_delete=models.SET_NULL, related_name="authoritative_for"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-confidence"]


class TopicCluster(ProjectScopedModel):
    """A cluster of semantically related document chunks."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis_job = models.ForeignKey(AnalysisJob, on_delete=models.CASCADE, related_name="clusters")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )

    label = models.CharField(max_length=500, help_text="LLM-generated cluster label")
    summary = models.TextField(blank=True, default="", help_text="LLM-generated summary")
    key_concepts = models.JSONField(default=list, help_text="LLM-extracted concept list")
    content_purpose = models.CharField(
        max_length=500, blank=True, default="",
        help_text="One-line purpose of this cluster's content",
    )
    level = models.PositiveIntegerField(default=0, help_text="Hierarchy level (0 = top)")
    doc_count = models.PositiveIntegerField(default=0)
    chunk_count = models.PositiveIntegerField(default=0)

    # Centroid for positioning in visualizations
    centroid_x = models.FloatField(null=True, blank=True)
    centroid_y = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["level", "label"]

    def __str__(self):
        return self.label


class ClusterMembership(ProjectScopedModel):
    """Maps chunks and documents to topic clusters."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cluster = models.ForeignKey(TopicCluster, on_delete=models.CASCADE, related_name="memberships")
    chunk = models.ForeignKey("ingestion.DocumentChunk", on_delete=models.CASCADE, related_name="cluster_memberships")
    document = models.ForeignKey("ingestion.Document", on_delete=models.CASCADE, related_name="cluster_memberships")
    similarity_to_centroid = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("cluster", "chunk")


class GapReport(ProjectScopedModel):
    """Detected gap in documentation coverage."""

    class GapType(models.TextChoices):
        MISSING_TOPIC = "missing_topic", _("Sujet manquant")
        LOW_COVERAGE = "low_coverage", _("Faible couverture")
        STALE_AREA = "stale_area", _("Zone obsolète")
        ORPHAN_TOPIC = "orphan_topic", _("Sujet orphelin")
        WEAK_BRIDGE = "weak_bridge", _("Pont fragile")
        CONCEPT_ISLAND = "concept_island", _("Îlot conceptuel")

    class Resolution(models.TextChoices):
        UNRESOLVED = "", _("Non traité")
        RESOLVED = "resolved", _("Traité")
        KEPT = "kept", _("Conservé")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis_job = models.ForeignKey(AnalysisJob, on_delete=models.CASCADE, related_name="gaps")

    gap_type = models.CharField(max_length=20, choices=GapType.choices)
    title = models.CharField(max_length=500, help_text="Suggested topic/document title")
    description = models.TextField(help_text="Why this gap was detected and what should be documented")
    severity = models.CharField(max_length=10, choices=ContradictionPair.Severity.choices, default="medium")
    resolution = models.CharField(
        max_length=10, choices=Resolution.choices, default="", blank=True,
    )

    # Evidence
    related_cluster = models.ForeignKey(
        TopicCluster, null=True, blank=True, on_delete=models.SET_NULL
    )
    coverage_score = models.FloatField(
        null=True, blank=True,
        help_text="0-1 score of how well this topic is covered (lower = bigger gap)",
    )
    evidence = models.JSONField(
        default=dict,
        help_text="Questions that couldn't be answered, adjacent clusters, etc.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["coverage_score", "-severity"]

    def __str__(self):
        return f"Gap: {self.title}"


class AuditJob(ProjectScopedModel):
    """Tracks a RAG quality audit run (6 axes, no LLM)."""

    class Status(models.TextChoices):
        QUEUED = "queued", _("En attente")
        RUNNING = "running", _("En cours")
        COMPLETED = "completed", _("Terminé")
        FAILED = "failed", _("Échoué")

    class Axis(models.TextChoices):
        HYGIENE = "hygiene", _("Hygiène du corpus")
        STRUCTURE = "structure", _("Structure RAG")
        COVERAGE = "coverage", _("Couverture sémantique")
        COHERENCE = "coherence", _("Cohérence interne")
        RETRIEVABILITY = "retrievability", _("Retrievability")
        GOVERNANCE = "governance", _("Gouvernance & metadata")
        DONE = "done", _("Terminé")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis_job = models.ForeignKey(
        AnalysisJob, null=True, blank=True, on_delete=models.CASCADE, related_name="audit_jobs"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    current_axis = models.CharField(max_length=20, choices=Axis.choices, default=Axis.HYGIENE)
    progress_pct = models.PositiveIntegerField(default=0)
    overall_score = models.FloatField(null=True, blank=True, help_text="0-100 weighted score")
    overall_grade = models.CharField(max_length=1, blank=True, default="", help_text="A-E grade")

    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Audit {self.id!s:.8} ({self.status})"


class AuditAxisResult(ProjectScopedModel):
    """Result for a single axis within an audit job."""

    AXIS_CHOICES = AuditJob.Axis.choices[:6]  # Exclude DONE

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    audit_job = models.ForeignKey(AuditJob, on_delete=models.CASCADE, related_name="axis_results")
    axis = models.CharField(max_length=20, choices=AXIS_CHOICES)
    score = models.FloatField(help_text="0-100 axis score")
    metrics = models.JSONField(default=dict, help_text="Key metrics for this axis")
    chart_data = models.JSONField(default=dict, help_text="Pre-computed data for D3.js charts")
    details = models.JSONField(default=dict, help_text="Detailed findings and item-level data")
    duration_seconds = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("audit_job", "axis")
        ordering = ["axis"]

    def __str__(self):
        return f"{self.get_axis_display()} — {self.score:.0f}/100"


class TreeNode(ProjectScopedModel):
    """Node in the hierarchical document taxonomy/outline tree."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis_job = models.ForeignKey(AnalysisJob, on_delete=models.CASCADE, related_name="tree_nodes")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")

    label = models.CharField(max_length=500)
    node_type = models.CharField(
        max_length=20,
        choices=[("category", _("Catégorie")), ("cluster", _("Cluster")), ("subcluster", _("Sous-cluster")), ("document", _("Document")), ("section", _("Section"))],
    )
    document = models.ForeignKey(
        "ingestion.Document", null=True, blank=True, on_delete=models.SET_NULL
    )
    cluster = models.ForeignKey(
        TopicCluster, null=True, blank=True, on_delete=models.SET_NULL
    )
    level = models.PositiveIntegerField(default=0)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["level", "sort_order"]


class HallucinationReport(ProjectScopedModel):
    """Detected hallucination risk factor in the document corpus.

    Identifies elements that can mislead a RAG system into generating
    hallucinated responses: undefined acronyms, ambiguous terminology,
    jargon without context, hedging language, and implicit knowledge gaps.
    """

    class RiskType(models.TextChoices):
        UNDEFINED_ACRONYM = "undefined_acronym", _("Acronyme non défini")
        AMBIGUOUS_ACRONYM = "ambiguous_acronym", _("Acronyme ambigu")
        CONFLICTING_ACRONYM = "conflicting_acronym", _("Acronyme contradictoire")
        JARGON_NO_CONTEXT = "jargon_no_context", _("Jargon sans contexte")
        HEDGING_LANGUAGE = "hedging_language", _("Langage incertain")
        IMPLICIT_KNOWLEDGE = "implicit_knowledge", _("Connaissance implicite")

    class Severity(models.TextChoices):
        HIGH = "high", _("Élevée")
        MEDIUM = "medium", _("Moyenne")
        LOW = "low", _("Faible")

    class Resolution(models.TextChoices):
        UNRESOLVED = "", _("Non traité")
        RESOLVED = "resolved", _("Traité")
        KEPT = "kept", _("Conservé")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis_job = models.ForeignKey(
        AnalysisJob, on_delete=models.CASCADE, related_name="hallucination_reports"
    )

    risk_type = models.CharField(max_length=25, choices=RiskType.choices)
    title = models.CharField(max_length=500)
    description = models.TextField()
    severity = models.CharField(
        max_length=10, choices=Severity.choices, default=Severity.MEDIUM
    )
    resolution = models.CharField(
        max_length=10, choices=Resolution.choices, default="", blank=True,
    )

    # The term/acronym itself
    term = models.CharField(max_length=200, blank=True, default="")

    # For acronyms: known expansions and where they appear
    expansions = models.JSONField(
        default=list,
        help_text="List of known expansions with document references",
    )

    # Which documents contain this risk
    document = models.ForeignKey(
        "ingestion.Document", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="hallucination_reports",
    )
    doc_count = models.PositiveIntegerField(
        default=0, help_text="Number of documents affected",
    )

    # Risk scoring
    risk_score = models.FloatField(
        default=0.0, help_text="0-1 hallucination risk score (higher = more risky)",
    )

    # Evidence and context
    evidence = models.JSONField(
        default=dict,
        help_text="Contextual evidence: passages, locations, conflicting definitions",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-risk_score", "severity"]

    def __str__(self):
        return f"Hallucination risk: {self.title}"


class PipelineTrace(ProjectScopedModel):
    """Aggregated trace for a full pipeline run."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis_job = models.OneToOneField(
        AnalysisJob, on_delete=models.CASCADE, related_name="trace"
    )

    total_llm_calls = models.PositiveIntegerField(default=0)
    total_embed_calls = models.PositiveIntegerField(default=0)
    total_search_calls = models.PositiveIntegerField(default=0)
    total_prompt_tokens = models.PositiveIntegerField(default=0)
    total_completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    total_duration_seconds = models.FloatField(default=0.0)

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Trace for {self.analysis_job_id!s:.8}"


class PhaseTrace(ProjectScopedModel):
    """Trace for a single pipeline phase."""

    class Status(models.TextChoices):
        RUNNING = "running", _("En cours")
        COMPLETED = "completed", _("Terminé")
        FAILED = "failed", _("Échoué")
        SKIPPED = "skipped", _("Ignoré")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pipeline_trace = models.ForeignKey(
        PipelineTrace, on_delete=models.CASCADE, related_name="phases"
    )
    phase_key = models.CharField(max_length=30)
    phase_label = models.CharField(max_length=100)

    llm_calls = models.PositiveIntegerField(default=0)
    embed_calls = models.PositiveIntegerField(default=0)
    search_calls = models.PositiveIntegerField(default=0)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    items_in = models.PositiveIntegerField(default=0)
    items_out = models.PositiveIntegerField(default=0)

    duration_seconds = models.FloatField(default=0.0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.RUNNING
    )
    error_message = models.TextField(blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]
        unique_together = ("pipeline_trace", "phase_key")

    def __str__(self):
        return f"{self.phase_label} ({self.status})"


class TraceEvent(models.Model):
    """Individual traced event (LLM call, embedding, vector search)."""

    class EventType(models.TextChoices):
        LLM_CHAT = "llm_chat", "LLM Chat"
        LLM_CHAT_CONCURRENT = "llm_chat_concurrent", "LLM Chat Concurrent"
        LLM_CHAT_BATCH = "llm_chat_batch", "LLM Chat Batch"
        LLM_EMBED = "llm_embed", "Embedding"
        VEC_SEARCH = "vec_search", "Vector Search"
        VEC_SEARCH_CLAIMS = "vec_search_claims", "Claims Search"
        VEC_UPSERT = "vec_upsert", "Vector Upsert"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phase_trace = models.ForeignKey(
        PhaseTrace, on_delete=models.CASCADE, related_name="events"
    )
    event_type = models.CharField(max_length=25, choices=EventType.choices)

    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    item_count = models.PositiveIntegerField(default=0)
    result_count = models.PositiveIntegerField(default=0)
    duration_seconds = models.FloatField(default=0.0)
    model_name = models.CharField(max_length=100, blank=True, default="")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp"]
        indexes = [
            models.Index(fields=["phase_trace", "event_type"]),
        ]
