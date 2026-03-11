"""Cross-tenant data isolation tests and end-to-end integration tests."""
import json
from unittest.mock import patch, MagicMock

import pytest
from django.contrib.auth.models import User
from django.test import Client

from analysis.models import AnalysisJob, Claim, ContradictionPair, DuplicateGroup, GapReport
from chat.models import Conversation, Message
from connectors.models import ConnectorConfig
from ingestion.models import Document, DocumentChunk
from reports.models import Report
from tenants.models import Project, ProjectMembership, Tenant, TenantMembership
from tests.conftest import make_chunk, make_document


@pytest.fixture
def tenant_a(db):
    user = User.objects.create_user("user_a", "a@example.com", "pass1234")
    tenant = Tenant.objects.create(name="TenantA", slug="tenant-a")
    TenantMembership.objects.create(tenant=tenant, user=user, role="admin")
    project = Project.objects.create(tenant=tenant, name="ProjectA", slug="project-a")
    ProjectMembership.objects.create(project=project, user=user, role="admin")
    connector = ConnectorConfig.objects.create(
        tenant=tenant, project=project, name="ConnA", connector_type="generic"
    )
    return user, tenant, project, connector


@pytest.fixture
def tenant_b(db):
    user = User.objects.create_user("user_b", "b@example.com", "pass1234")
    tenant = Tenant.objects.create(name="TenantB", slug="tenant-b")
    TenantMembership.objects.create(tenant=tenant, user=user, role="admin")
    project = Project.objects.create(tenant=tenant, name="ProjectB", slug="project-b")
    ProjectMembership.objects.create(project=project, user=user, role="admin")
    connector = ConnectorConfig.objects.create(
        tenant=tenant, project=project, name="ConnB", connector_type="generic"
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


# ---------------------------------------------------------------------------
# Cross-tenant document isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentIsolation:
    def test_documents_scoped_to_tenant(self, tenant_a, tenant_b):
        _, t_a, p_a, c_a = tenant_a
        _, t_b, p_b, c_b = tenant_b

        doc_a = make_document(t_a, p_a, c_a, title="DocA")
        doc_b = make_document(t_b, p_b, c_b, title="DocB")

        docs_a = Document.objects.for_tenant(t_a)
        docs_b = Document.objects.for_tenant(t_b)

        assert doc_a in docs_a
        assert doc_b not in docs_a
        assert doc_b in docs_b
        assert doc_a not in docs_b

    def test_documents_scoped_to_project(self, tenant_a, tenant_b):
        _, t_a, p_a, c_a = tenant_a
        _, t_b, p_b, c_b = tenant_b

        doc_a = make_document(t_a, p_a, c_a, title="DocA")
        doc_b = make_document(t_b, p_b, c_b, title="DocB")

        docs_a = Document.objects.for_project(p_a)
        assert doc_a in docs_a
        assert doc_b not in docs_a


# ---------------------------------------------------------------------------
# Cross-tenant view isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestViewIsolation:
    def test_cannot_view_other_tenant_analysis(self, tenant_a, tenant_b):
        user_a, t_a, p_a, _ = tenant_a
        _, t_b, p_b, _ = tenant_b

        job_b = AnalysisJob.objects.create(
            tenant=t_b, project=p_b, status="completed"
        )

        client = _client(user_a, t_a, p_a)
        resp = client.get(f"/analysis/{job_b.id}/")
        assert resp.status_code == 404

    def test_cannot_delete_other_tenant_analysis(self, tenant_a, tenant_b):
        user_a, t_a, p_a, _ = tenant_a
        _, t_b, p_b, _ = tenant_b

        job_b = AnalysisJob.objects.create(
            tenant=t_b, project=p_b, status="completed"
        )

        client = _client(user_a, t_a, p_a)
        resp = client.post(f"/analysis/{job_b.id}/delete/")
        assert resp.status_code == 404
        assert AnalysisJob.objects.filter(id=job_b.id).exists()

    def test_cannot_view_other_tenant_connector(self, tenant_a, tenant_b):
        user_a, t_a, p_a, _ = tenant_a
        _, t_b, p_b, c_b = tenant_b

        client = _client(user_a, t_a, p_a)
        resp = client.get(f"/connectors/{c_b.id}/")
        assert resp.status_code == 404

    def test_cannot_view_other_tenant_chat(self, tenant_a, tenant_b):
        user_a, t_a, p_a, _ = tenant_a
        user_b, t_b, p_b, _ = tenant_b

        conv = Conversation.objects.create(
            tenant=t_b, project=p_b, user=user_b, title="Secret"
        )

        client = _client(user_a, t_a, p_a)
        resp = client.get(f"/chat/conversations/{conv.id}/messages/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cross-tenant analysis data isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnalysisDataIsolation:
    def test_analysis_jobs_scoped_to_project(self, tenant_a, tenant_b):
        _, t_a, p_a, _ = tenant_a
        _, t_b, p_b, _ = tenant_b

        job_a = AnalysisJob.objects.create(tenant=t_a, project=p_a, status="completed")
        job_b = AnalysisJob.objects.create(tenant=t_b, project=p_b, status="completed")

        assert AnalysisJob.objects.for_project(p_a).count() == 1
        assert AnalysisJob.objects.for_project(p_a).first() == job_a

    def test_duplicate_groups_scoped(self, tenant_a, tenant_b):
        _, t_a, p_a, _ = tenant_a
        _, t_b, p_b, _ = tenant_b

        job_a = AnalysisJob.objects.create(tenant=t_a, project=p_a, status="completed")
        job_b = AnalysisJob.objects.create(tenant=t_b, project=p_b, status="completed")

        DuplicateGroup.objects.create(
            tenant=t_a, project=p_a, analysis_job=job_a,
        )
        DuplicateGroup.objects.create(
            tenant=t_b, project=p_b, analysis_job=job_b,
        )

        assert DuplicateGroup.objects.for_tenant(t_a).count() == 1
        assert DuplicateGroup.objects.for_tenant(t_b).count() == 1


# ---------------------------------------------------------------------------
# Chat isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestChatIsolation:
    def test_conversation_belongs_to_user(self, tenant_a, tenant_b):
        user_a, t_a, p_a, _ = tenant_a
        user_b, t_b, p_b, _ = tenant_b

        conv_a = Conversation.objects.create(
            tenant=t_a, project=p_a, user=user_a, title="A's chat"
        )
        conv_b = Conversation.objects.create(
            tenant=t_b, project=p_b, user=user_b, title="B's chat"
        )

        user_a_convs = Conversation.objects.filter(user=user_a)
        assert conv_a in user_a_convs
        assert conv_b not in user_a_convs


# ---------------------------------------------------------------------------
# End-to-end flow
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEndToEndFlow:
    """Integration test: authenticated user navigates through the main pages."""

    def test_dashboard_to_analysis_flow(self, tenant_a):
        user, tenant, project, connector = tenant_a

        # Create some documents
        for i in range(3):
            make_document(tenant, project, connector, title=f"FlowDoc{i}", status="ready")

        client = _client(user, tenant, project)

        # 1. Dashboard loads
        resp = client.get("/dashboard/")
        assert resp.status_code == 200

        # 2. Analysis list loads
        resp = client.get("/analysis/")
        assert resp.status_code == 200

        # 3. Connector list loads
        resp = client.get("/connectors/")
        assert resp.status_code == 200

        # 4. Connector detail loads
        resp = client.get(f"/connectors/{connector.id}/")
        assert resp.status_code == 200

        # 5. Chat loads
        resp = client.get("/chat/")
        assert resp.status_code == 200

        # 6. Reports list loads
        resp = client.get("/reports/")
        assert resp.status_code == 200

    def test_analysis_results_flow(self, tenant_a):
        """Create analysis job manually, then verify all result views load."""
        user, tenant, project, connector = tenant_a

        for i in range(3):
            make_document(tenant, project, connector, title=f"ResDoc{i}", status="ready")

        job = AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED,
        )
        client = _client(user, tenant, project)

        # Detail page
        resp = client.get(f"/analysis/{job.id}/")
        assert resp.status_code == 200

        # Duplicates report
        resp = client.get(f"/analysis/{job.id}/duplicates/")
        assert resp.status_code == 200

        # Contradictions report
        resp = client.get(f"/analysis/{job.id}/contradictions/")
        assert resp.status_code == 200

        # Gaps report
        resp = client.get(f"/analysis/{job.id}/gaps/")
        assert resp.status_code == 200

        # Clusters view
        resp = client.get(f"/analysis/{job.id}/clusters/")
        assert resp.status_code == 200

        # Tree view
        resp = client.get(f"/analysis/{job.id}/tree/")
        assert resp.status_code == 200

    def test_dashboard_partials(self, tenant_a):
        """HTMX partials return 200."""
        user, tenant, project, _ = tenant_a
        client = _client(user, tenant, project)

        resp = client.get("/dashboard/_stats/")
        assert resp.status_code == 200

        resp = client.get("/dashboard/_latest-analysis/")
        assert resp.status_code == 200

        resp = client.get("/dashboard/_recent-jobs/")
        assert resp.status_code == 200

    def test_docuscore_detail_json(self, tenant_a):
        user, tenant, project, connector = tenant_a
        make_document(tenant, project, connector, title="D", status="ready")
        client = _client(user, tenant, project)
        resp = client.get("/dashboard/_docuscore-detail/")
        assert resp.status_code == 200
        data = resp.json()
        assert "grade" in data
        assert "score" in data

    def test_submit_feedback(self, tenant_a):
        user, tenant, project, _ = tenant_a
        client = _client(user, tenant, project)
        resp = client.post(
            "/dashboard/feedback/",
            json.dumps({
                "type": "feedback",
                "area": "analysis",
                "subject": "Test feedback",
                "description": "This is a test.",
            }),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_csv_exports_load(self, tenant_a):
        user, tenant, project, connector = tenant_a
        make_document(tenant, project, connector, title="ExpDoc", status="ready")
        job = AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED,
        )
        client = _client(user, tenant, project)

        resp = client.get(f"/reports/{job.id}/duplicates.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp["Content-Type"]

        resp = client.get(f"/reports/{job.id}/contradictions.csv")
        assert resp.status_code == 200

        resp = client.get(f"/reports/{job.id}/gaps.csv")
        assert resp.status_code == 200

        resp = client.get(f"/reports/{job.id}/report.json")
        assert resp.status_code == 200
        assert "application/json" in resp["Content-Type"]
