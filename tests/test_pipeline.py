"""Tests for analysis.tasks — Pipeline orchestration."""

from unittest.mock import MagicMock

import pytest

from analysis.models import (
    AnalysisJob,
    ContradictionPair,
    DuplicateGroup,
    GapReport,
    KGEntity,
    KGRelation,
    KnowledgeGraphRun,
    TopicCluster,
)
from analysis.tasks import (
    ANALYSIS_PHASE_ORDER,
    AUDIT_PHASE_ORDER,
    UNIFIED_PROGRESS,
    _build_effective_config,
    _cleanup_phase,
    _make_progress_cb,
)
from tests.conftest import make_chunk, make_document


# ---------------------------------------------------------------------------
# _build_effective_config
# ---------------------------------------------------------------------------


class TestBuildEffectiveConfig:
    def test_base_config_returned_when_no_overrides(self, settings):
        settings.ANALYSIS_CONFIG = {"duplicate": {"semantic_weight": 0.55}}
        job = MagicMock()
        job.config_overrides = {}

        result = _build_effective_config(job)

        assert result["duplicate"]["semantic_weight"] == 0.55

    def test_dict_override_merged(self, settings):
        settings.ANALYSIS_CONFIG = {
            "duplicate": {"semantic_weight": 0.55, "lexical_weight": 0.25},
        }
        job = MagicMock()
        job.config_overrides = {"duplicate": {"semantic_weight": 0.8}}

        result = _build_effective_config(job)

        assert result["duplicate"]["semantic_weight"] == 0.8
        assert result["duplicate"]["lexical_weight"] == 0.25

    def test_non_dict_override_replaced(self, settings):
        settings.ANALYSIS_CONFIG = {"some_key": "old_value"}
        job = MagicMock()
        job.config_overrides = {"some_key": "new_value", "new_key": 42}

        result = _build_effective_config(job)

        assert result["some_key"] == "new_value"
        assert result["new_key"] == 42

    def test_base_config_not_mutated(self, settings):
        original = {"duplicate": {"semantic_weight": 0.55}}
        settings.ANALYSIS_CONFIG = original
        job = MagicMock()
        job.config_overrides = {"duplicate": {"semantic_weight": 0.8}}

        _build_effective_config(job)

        # Original should be unchanged (deep copy)
        assert original["duplicate"]["semantic_weight"] == 0.55


# ---------------------------------------------------------------------------
# _cleanup_phase
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCleanupPhase:
    def test_cleanup_duplicates(self, tenant, project, connector, analysis_job):
        make_document(tenant, project, connector, title="A")
        make_document(tenant, project, connector, title="B")
        DuplicateGroup.objects.create(
            tenant=tenant,
            project=project,
            analysis_job=analysis_job,
        )

        _cleanup_phase(analysis_job, "duplicates")

        assert DuplicateGroup.objects.filter(analysis_job=analysis_job).count() == 0

    def test_cleanup_contradictions(self, tenant, project, connector, analysis_job):
        from analysis.models import Claim

        doc = make_document(tenant, project, connector, title="ContraDoc")
        chunk = make_chunk(tenant, doc, 0, "text")
        claim_a = Claim.objects.create(
            tenant=tenant,
            project=project,
            document=doc,
            chunk=chunk,
            subject="x",
            predicate="y",
            object_value="z",
            raw_text="a",
        )
        claim_b = Claim.objects.create(
            tenant=tenant,
            project=project,
            document=doc,
            chunk=chunk,
            subject="x",
            predicate="y",
            object_value="w",
            raw_text="b",
        )
        ContradictionPair.objects.create(
            tenant=tenant,
            project=project,
            analysis_job=analysis_job,
            claim_a=claim_a,
            claim_b=claim_b,
            classification="contradiction",
            confidence=0.9,
            evidence="test",
        )

        _cleanup_phase(analysis_job, "contradictions")

        assert ContradictionPair.objects.filter(analysis_job=analysis_job).count() == 0

    def test_cleanup_gaps(self, tenant, project, analysis_job):
        GapReport.objects.create(
            tenant=tenant,
            project=project,
            analysis_job=analysis_job,
            gap_type="orphan_topic",
            title="Test Gap",
            description="A test gap.",
            severity="low",
        )

        _cleanup_phase(analysis_job, "gaps")

        assert GapReport.objects.filter(analysis_job=analysis_job).count() == 0


# ---------------------------------------------------------------------------
# Resume logic
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResumeLogic:
    def test_fresh_job_runs_all_phases(self, tenant, project, analysis_job):
        """A fresh job (current_phase=duplicates) should start from index 0."""
        analysis_job.current_phase = AnalysisJob.Phase.DUPLICATES
        resume_idx = ANALYSIS_PHASE_ORDER.index("duplicates")
        assert resume_idx == 0

    def test_resume_from_clustering_skips_early(self, tenant, project, analysis_job):
        """Resuming from clustering should skip duplicates, claims, semantic_graph."""
        resume_from = "clustering"
        resume_idx = ANALYSIS_PHASE_ORDER.index(resume_from)
        phases_to_skip = ANALYSIS_PHASE_ORDER[:resume_idx]

        assert "duplicates" in phases_to_skip
        assert "claims" in phases_to_skip
        assert "semantic_graph" in phases_to_skip
        assert "clustering" not in phases_to_skip

    def test_audit_resume_skips_analysis(self, tenant, project, analysis_job):
        """If current_phase is an audit phase, analysis is completely skipped."""
        checkpoint = "audit_coverage"
        is_audit_resume = checkpoint in AUDIT_PHASE_ORDER
        assert is_audit_resume is True

        # All analysis phases should be skipped
        is_analysis_resume = checkpoint in ANALYSIS_PHASE_ORDER
        assert is_analysis_resume is False


# ---------------------------------------------------------------------------
# Knowledge graph phase
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestKnowledgeGraphPhase:
    def test_runs_after_clustering_and_before_gaps(self):
        """It consumes TopicClusters, so it cannot run before they exist."""
        order = ANALYSIS_PHASE_ORDER
        assert order.index("clustering") < order.index("knowledge_graph")
        assert order.index("knowledge_graph") < order.index("gaps")

    def test_progress_slot_leaves_neighbours_untouched(self):
        """The new step fits in the existing 22-32 gap, so no other value moves."""
        assert UNIFIED_PROGRESS["clustering"] == 22
        assert UNIFIED_PROGRESS["gaps"] == 32
        assert 22 < UNIFIED_PROGRESS["knowledge_graph"] < 32

    def test_insertion_does_not_break_existing_resume(self):
        """Phase order is recomputed at runtime; only the string is persisted."""
        resume_idx = ANALYSIS_PHASE_ORDER.index("gaps")
        skipped = ANALYSIS_PHASE_ORDER[:resume_idx]

        assert "clustering" in skipped
        assert "knowledge_graph" in skipped
        assert "tree" not in skipped

    def test_disabled_by_default_in_shipped_config(self, settings):
        """Shipped configuration must leave the existing pipeline untouched."""
        assert settings.ANALYSIS_CONFIG["knowledge_graph"]["enabled"] is False

    def test_cleanup_removes_run_and_cascades(self, tenant, project, analysis_job):
        run = KnowledgeGraphRun.objects.create(
            tenant=tenant, project=project, analysis_job=analysis_job
        )
        entity = KGEntity.objects.create(
            tenant=tenant, project=project, run=run, canonical="rgpd", label="RGPD"
        )
        other = KGEntity.objects.create(
            tenant=tenant, project=project, run=run, canonical="cnil", label="CNIL"
        )
        KGRelation.objects.create(
            tenant=tenant,
            project=project,
            run=run,
            subject=entity,
            object=other,
            predicate="contrôlé par",
        )

        _cleanup_phase(analysis_job, "knowledge_graph")

        assert KnowledgeGraphRun.objects.filter(analysis_job=analysis_job).count() == 0
        assert KGEntity.objects.filter(run=run).count() == 0
        assert KGRelation.objects.filter(run=run).count() == 0

    def test_cluster_deletion_keeps_the_graph(self, tenant, project, analysis_job):
        """SET_NULL, not CASCADE: resuming from clustering must not wipe the graph."""
        cluster = TopicCluster.objects.create(
            tenant=tenant, project=project, analysis_job=analysis_job, label="Santé"
        )
        run = KnowledgeGraphRun.objects.create(
            tenant=tenant, project=project, analysis_job=analysis_job
        )
        entity = KGEntity.objects.create(
            tenant=tenant,
            project=project,
            run=run,
            canonical="ipsec",
            label="IPSEC",
            main_cluster=cluster,
        )
        other = KGEntity.objects.create(
            tenant=tenant, project=project, run=run, canonical="garantie", label="garantie"
        )
        relation = KGRelation.objects.create(
            tenant=tenant,
            project=project,
            run=run,
            subject=entity,
            object=other,
            predicate="propose",
            cluster=cluster,
        )

        _cleanup_phase(analysis_job, "clustering")

        entity.refresh_from_db()
        relation.refresh_from_db()
        assert entity.main_cluster is None
        assert relation.cluster is None


# ---------------------------------------------------------------------------
# _make_progress_cb
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProgressTracking:
    def test_make_progress_cb_writes_detail(self, tenant, project, analysis_job):
        cb = _make_progress_cb(analysis_job.pk, "Test step")

        # First call always writes
        cb(5, 100)

        analysis_job.refresh_from_db()
        detail = analysis_job.phase_detail
        assert detail["step"] == "Test step"
        assert detail["done"] == 5
        assert detail["total"] == 100

    def test_make_progress_cb_last_call_always_writes(self, tenant, project, analysis_job):
        cb = _make_progress_cb(analysis_job.pk, "Final step")

        cb(1, 10)  # first call
        cb(10, 10)  # last call (done >= total)

        analysis_job.refresh_from_db()
        detail = analysis_job.phase_detail
        assert detail["done"] == 10
        assert detail["total"] == 10
