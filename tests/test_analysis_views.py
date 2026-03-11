"""Tests for analysis views: list, detail, run, delete, retry, cancel, reports, resolve."""
from unittest.mock import patch, MagicMock

import pytest
from django.contrib.auth.models import User
from django.test import Client

from analysis.models import (
    AnalysisJob,
    Claim,
    ContradictionPair,
    DuplicateGroup,
    DuplicatePair,
    GapReport,
    TopicCluster,
)
from connectors.models import ConnectorConfig
from tenants.models import Project, ProjectMembership, Tenant, TenantMembership
from tests.conftest import make_chunk, make_document


@pytest.fixture
def setup(db):
    user = User.objects.create_user("auser", "a@example.com", "pass1234")
    tenant = Tenant.objects.create(name="AnalysisTenant", slug="analysis-tenant")
    TenantMembership.objects.create(tenant=tenant, user=user, role=TenantMembership.Role.ADMIN)
    project = Project.objects.create(tenant=tenant, name="AnalysisProject", slug="analysis-project")
    ProjectMembership.objects.create(project=project, user=user, role=TenantMembership.Role.ADMIN)
    connector = ConnectorConfig.objects.create(
        tenant=tenant, project=project, name="AC", connector_type="generic"
    )
    return user, tenant, project, connector


def _client(user, tenant, project):
    c = Client()
    c.login(username=user.username, password="pass1234")
    session = c.session
    session["tenant_id"] = str(tenant.id)
    session["project_id"] = str(project.id)
    session.save()
    return c


def _make_job(tenant, project, status="completed"):
    return AnalysisJob.objects.create(
        tenant=tenant, project=project, status=status,
    )


# ---------------------------------------------------------------------------
# Analysis list
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnalysisList:
    def test_loads(self, setup):
        user, tenant, project, _ = setup
        client = _client(user, tenant, project)
        resp = client.get("/analysis/")
        assert resp.status_code == 200

    def test_requires_login(self, setup):
        c = Client()
        resp = c.get("/analysis/")
        assert resp.status_code == 302
        assert "/auth/login/" in resp.url


# ---------------------------------------------------------------------------
# Analysis detail
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnalysisDetail:
    def test_loads_completed_job(self, setup):
        user, tenant, project, connector = setup
        make_document(tenant, project, connector, title="D", status="ready")
        job = _make_job(tenant, project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/")
        assert resp.status_code == 200

    def test_wrong_project_404(self, setup):
        user, tenant, project, _ = setup
        other_tenant = Tenant.objects.create(name="Other", slug="other-an")
        other_project = Project.objects.create(tenant=other_tenant, name="OP", slug="op")
        job = _make_job(other_tenant, other_project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Analysis run
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnalysisRun:
    @patch("analysis.views.run_unified_pipeline")
    def test_run_creates_job(self, mock_run, setup):
        user, tenant, project, connector = setup
        make_document(tenant, project, connector, title="D", status="ready")
        mock_task = MagicMock()
        mock_task.id = "fake-celery-task-id"
        mock_run.delay.return_value = mock_task
        client = _client(user, tenant, project)
        resp = client.post("/analysis/run/")
        assert resp.status_code == 302
        assert AnalysisJob.objects.filter(project=project).exists()

    def test_run_blocked_no_docs(self, setup):
        user, tenant, project, _ = setup
        client = _client(user, tenant, project)
        resp = client.post("/analysis/run/")
        # Should redirect back (cannot run without docs)
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Analysis delete
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnalysisDelete:
    def test_delete(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project)
        client = _client(user, tenant, project)
        resp = client.post(f"/analysis/{job.id}/delete/")
        assert resp.status_code == 302
        assert not AnalysisJob.objects.filter(id=job.id).exists()

    def test_get_not_allowed(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/delete/")
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# Analysis cancel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnalysisCancel:
    def test_cancel_running_job(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project, status="running")
        client = _client(user, tenant, project)
        resp = client.post(f"/analysis/{job.id}/cancel/")
        assert resp.status_code == 302
        job.refresh_from_db()
        assert job.status == AnalysisJob.Status.CANCELLED


# ---------------------------------------------------------------------------
# Duplicates report
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDuplicatesReport:
    def test_loads(self, setup):
        user, tenant, project, connector = setup
        make_document(tenant, project, connector, title="D", status="ready")
        job = _make_job(tenant, project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/duplicates/")
        assert resp.status_code == 200

    def test_shows_duplicate_groups(self, setup):
        user, tenant, project, connector = setup
        doc_a = make_document(tenant, project, connector, title="DA", status="ready")
        doc_b = make_document(tenant, project, connector, title="DB", status="ready")
        job = _make_job(tenant, project)
        group = DuplicateGroup.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            recommended_action="merge",
        )
        DuplicatePair.objects.create(
            tenant=tenant, project=project, group=group,
            doc_a=doc_a, doc_b=doc_b,
            semantic_score=0.95, lexical_score=0.80,
            metadata_score=0.70, combined_score=0.87,
        )
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/duplicates/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Contradictions report & resolve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestContradictionsReport:
    def _make_contradiction(self, tenant, project, connector, job):
        doc = make_document(tenant, project, connector, title="ConDoc", status="ready")
        chunk = make_chunk(tenant, doc, 0, "text")
        claim_a = Claim.objects.create(
            tenant=tenant, project=project, document=doc, chunk=chunk,
            subject="X", predicate="is", object_value="A", raw_text="X is A",
        )
        claim_b = Claim.objects.create(
            tenant=tenant, project=project, document=doc, chunk=chunk,
            subject="X", predicate="is", object_value="B", raw_text="X is B",
        )
        return ContradictionPair.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            claim_a=claim_a, claim_b=claim_b,
            classification="contradiction", severity="high",
            confidence=0.9, evidence="Direct conflict.",
        )

    def test_loads(self, setup):
        user, tenant, project, connector = setup
        job = _make_job(tenant, project)
        self._make_contradiction(tenant, project, connector, job)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/contradictions/")
        assert resp.status_code == 200

    def test_resolve_contradiction(self, setup):
        user, tenant, project, connector = setup
        job = _make_job(tenant, project)
        contra = self._make_contradiction(tenant, project, connector, job)
        client = _client(user, tenant, project)
        resp = client.post(
            f"/analysis/{job.id}/contradictions/{contra.id}/resolve/",
            {"resolution": "resolved"},
        )
        assert resp.status_code == 302
        contra.refresh_from_db()
        assert contra.resolution == "resolved"


# ---------------------------------------------------------------------------
# Gaps report & resolve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGapsReport:
    def test_loads(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project)
        GapReport.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            gap_type="missing_topic", title="Missing API",
            description="API docs missing.", severity="high",
        )
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/gaps/")
        assert resp.status_code == 200

    def test_resolve_gap(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project)
        gap = GapReport.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            gap_type="missing_topic", title="Gap",
            description="A gap.", severity="low",
        )
        client = _client(user, tenant, project)
        resp = client.post(
            f"/analysis/{job.id}/gaps/{gap.id}/resolve/",
            {"resolution": "resolved"},
        )
        assert resp.status_code == 302
        gap.refresh_from_db()
        assert gap.resolution == "resolved"


# ---------------------------------------------------------------------------
# Clusters view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClustersView:
    def test_loads(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/clusters/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tree view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTreeView:
    def test_loads(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/tree/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Progress partials
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProgressPartials:
    def test_progress_partial(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project, status="running")
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/_progress/")
        assert resp.status_code == 200

    def test_progress_full_partial(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project, status="running")
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/_progress_full/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# JSON APIs
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClustersJSON:
    def test_returns_json(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/api/clusters/")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/json"

    def test_includes_clusters(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project)
        TopicCluster.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            label="Security", level=0,
        )
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/api/clusters/")
        data = resp.json()
        assert "nodes" in data


@pytest.mark.django_db
class TestTreeJSON:
    def test_returns_json(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/{job.id}/api/tree/")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# can_run_analysis
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCanRunAnalysis:
    def test_no_docs_blocks(self, setup):
        _, tenant, project, _ = setup
        from analysis.views import can_run_analysis
        can_run, reason = can_run_analysis(project)
        assert can_run is False
        assert reason == "no_docs"

    def test_with_docs_allows(self, setup):
        _, tenant, project, connector = setup
        make_document(tenant, project, connector, title="D", status="ready")
        from analysis.views import can_run_analysis
        can_run, reason = can_run_analysis(project)
        assert can_run is True

    def test_running_job_blocks(self, setup):
        _, tenant, project, connector = setup
        make_document(tenant, project, connector, title="D", status="ready")
        AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.RUNNING,
        )
        from analysis.views import can_run_analysis
        can_run, reason = can_run_analysis(project)
        assert can_run is False
        assert reason == "running"

    def test_no_changes_since_last_analysis_blocks(self, setup):
        _, tenant, project, connector = setup
        make_document(tenant, project, connector, title="D", status="ready")
        AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED,
        )
        from analysis.views import can_run_analysis
        can_run, reason = can_run_analysis(project)
        assert can_run is False
        assert reason == "no_changes"


# ---------------------------------------------------------------------------
# Batch resolve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBatchResolve:
    def test_batch_resolve_contradictions(self, setup):
        user, tenant, project, connector = setup
        job = _make_job(tenant, project)
        doc = make_document(tenant, project, connector, title="BD", status="ready")
        chunk = make_chunk(tenant, doc, 0, "text")
        claim_a = Claim.objects.create(
            tenant=tenant, project=project, document=doc, chunk=chunk,
            subject="S", predicate="P", object_value="O", raw_text="s p o",
        )
        claim_b = Claim.objects.create(
            tenant=tenant, project=project, document=doc, chunk=chunk,
            subject="S", predicate="P", object_value="O2", raw_text="s p o2",
        )
        c1 = ContradictionPair.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            claim_a=claim_a, claim_b=claim_b,
            classification="contradiction", severity="low",
            confidence=0.5, evidence="Minor conflict.",
        )
        client = _client(user, tenant, project)
        resp = client.post(
            f"/analysis/{job.id}/contradictions/batch-resolve/",
            {"selected": [str(c1.id)], "resolution": "resolved"},
        )
        assert resp.status_code == 302
        c1.refresh_from_db()
        assert c1.resolution == "resolved"

    def test_batch_resolve_gaps(self, setup):
        user, tenant, project, _ = setup
        job = _make_job(tenant, project)
        g1 = GapReport.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            gap_type="orphan_topic", title="Orphan",
            description="Orphaned.", severity="low",
        )
        client = _client(user, tenant, project)
        resp = client.post(
            f"/analysis/{job.id}/gaps/batch-resolve/",
            {"selected": [str(g1.id)], "resolution": "kept"},
        )
        assert resp.status_code == 302
        g1.refresh_from_db()
        assert g1.resolution == "kept"
