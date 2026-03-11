"""Tests for analysis.tasks — Pipeline orchestration."""
from unittest.mock import MagicMock

import pytest

from analysis.models import (
    AnalysisJob,
    ContradictionPair,
    DuplicateGroup,
    GapReport,
)
from analysis.tasks import (
    ANALYSIS_PHASE_ORDER,
    AUDIT_PHASE_ORDER,
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
            tenant=tenant, project=project, analysis_job=analysis_job,
        )

        _cleanup_phase(analysis_job, "duplicates")

        assert DuplicateGroup.objects.filter(analysis_job=analysis_job).count() == 0

    def test_cleanup_contradictions(self, tenant, project, connector, analysis_job):
        from analysis.models import Claim

        doc = make_document(tenant, project, connector, title="ContraDoc")
        chunk = make_chunk(tenant, doc, 0, "text")
        claim_a = Claim.objects.create(
            tenant=tenant, project=project, document=doc, chunk=chunk,
            subject="x", predicate="y", object_value="z", raw_text="a",
        )
        claim_b = Claim.objects.create(
            tenant=tenant, project=project, document=doc, chunk=chunk,
            subject="x", predicate="y", object_value="w", raw_text="b",
        )
        ContradictionPair.objects.create(
            tenant=tenant, project=project, analysis_job=analysis_job,
            claim_a=claim_a, claim_b=claim_b,
            classification="contradiction", confidence=0.9, evidence="test",
        )

        _cleanup_phase(analysis_job, "contradictions")

        assert ContradictionPair.objects.filter(analysis_job=analysis_job).count() == 0

    def test_cleanup_gaps(self, tenant, project, analysis_job):
        GapReport.objects.create(
            tenant=tenant, project=project, analysis_job=analysis_job,
            gap_type="orphan_topic", title="Test Gap",
            description="A test gap.", severity="low",
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

        cb(1, 10)   # first call
        cb(10, 10)  # last call (done >= total)

        analysis_job.refresh_from_db()
        detail = analysis_job.phase_detail
        assert detail["done"] == 10
        assert detail["total"] == 10
