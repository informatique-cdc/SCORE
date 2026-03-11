"""Tests for audit views: detail, axis, retry, delete, progress, API."""

import pytest
from django.contrib.auth.models import User
from django.test import Client

from analysis.models import AnalysisJob, AuditAxisResult, AuditJob
from tenants.models import Project, ProjectMembership, Tenant, TenantMembership


@pytest.fixture
def audit_setup(db):
    user = User.objects.create_user("audituser", "audit@example.com", "pass1234")
    tenant = Tenant.objects.create(name="AuditTenant", slug="audit-tenant")
    TenantMembership.objects.create(tenant=tenant, user=user, role=TenantMembership.Role.ADMIN)
    project = Project.objects.create(tenant=tenant, name="AuditProject", slug="audit-project")
    ProjectMembership.objects.create(project=project, user=user, role=TenantMembership.Role.ADMIN)
    return user, tenant, project


def _client(user, tenant, project):
    c = Client()
    c.login(username=user.username, password="pass1234")
    session = c.session
    session["tenant_id"] = str(tenant.id)
    session["project_id"] = str(project.id)
    session.save()
    return c


def _make_audit(tenant, project, status=AuditJob.Status.COMPLETED):
    analysis = AnalysisJob.objects.create(tenant=tenant, project=project, status="completed")
    job = AuditJob.objects.create(
        tenant=tenant,
        project=project,
        analysis_job=analysis,
        status=status,
        overall_score=75.0,
        overall_grade="B",
    )
    return job


@pytest.mark.django_db
class TestAuditDetail:
    def test_loads(self, audit_setup):
        user, tenant, project = audit_setup
        job = _make_audit(tenant, project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/audit/{job.id}/")
        assert resp.status_code == 200

    def test_wrong_project_404(self, audit_setup):
        user, tenant, project = audit_setup
        other_tenant = Tenant.objects.create(name="Other", slug="other-aud")
        other_project = Project.objects.create(
            tenant=other_tenant, name="OtherP", slug="other-aud-p"
        )
        job = _make_audit(other_tenant, other_project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/audit/{job.id}/")
        assert resp.status_code == 404

    def test_requires_login(self, audit_setup):
        _, tenant, project = audit_setup
        job = _make_audit(tenant, project)
        c = Client()
        resp = c.get(f"/analysis/audit/{job.id}/")
        assert resp.status_code == 302
        assert "/auth/login/" in resp.url


@pytest.mark.django_db
class TestAuditAxisView:
    def test_axis_hygiene(self, audit_setup):
        user, tenant, project = audit_setup
        job = _make_audit(tenant, project)
        AuditAxisResult.objects.create(
            tenant=tenant,
            project=project,
            audit_job=job,
            axis="hygiene",
            score=80.0,
            metrics={},
            chart_data={},
            details={},
        )
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/audit/{job.id}/hygiene/")
        assert resp.status_code == 200

    def test_missing_axis_404(self, audit_setup):
        user, tenant, project = audit_setup
        job = _make_audit(tenant, project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/audit/{job.id}/hygiene/")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestAuditDelete:
    def test_deletes_job(self, audit_setup):
        user, tenant, project = audit_setup
        job = _make_audit(tenant, project)
        client = _client(user, tenant, project)
        resp = client.post(f"/analysis/audit/{job.id}/delete/")
        assert resp.status_code == 302
        assert not AuditJob.objects.filter(id=job.id).exists()

    def test_get_not_allowed(self, audit_setup):
        user, tenant, project = audit_setup
        job = _make_audit(tenant, project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/audit/{job.id}/delete/")
        assert resp.status_code == 405


@pytest.mark.django_db
class TestAuditProgressPartial:
    def test_loads(self, audit_setup):
        user, tenant, project = audit_setup
        job = _make_audit(tenant, project, status=AuditJob.Status.RUNNING)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/audit/{job.id}/_progress/")
        assert resp.status_code == 200


@pytest.mark.django_db
class TestApiAuditAxis:
    def test_returns_json(self, audit_setup):
        user, tenant, project = audit_setup
        job = _make_audit(tenant, project)
        AuditAxisResult.objects.create(
            tenant=tenant,
            project=project,
            audit_job=job,
            axis="structure",
            score=65.0,
            metrics={"avg_chunk_size": 500},
            chart_data={"bins": [1, 2, 3]},
            details={},
        )
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/audit/{job.id}/api/structure/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["axis"] == "structure"
        assert data["score"] == 65.0

    def test_missing_axis_404(self, audit_setup):
        user, tenant, project = audit_setup
        job = _make_audit(tenant, project)
        client = _client(user, tenant, project)
        resp = client.get(f"/analysis/audit/{job.id}/api/structure/")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestAuditListRedirect:
    def test_redirects_to_analysis_list(self, audit_setup):
        user, tenant, project = audit_setup
        client = _client(user, tenant, project)
        resp = client.get("/analysis/audit/")
        assert resp.status_code == 302
        assert "analysis" in resp.url
