"""Tests for reports views: list, CSV export, JSON export."""

import pytest
from django.contrib.auth.models import User
from django.test import Client

from analysis.models import AnalysisJob
from tenants.models import Project, ProjectMembership, Tenant, TenantMembership


@pytest.fixture
def report_setup(db):
    user = User.objects.create_user("reportuser", "report@example.com", "pass1234")
    tenant = Tenant.objects.create(name="ReportTenant", slug="report-tenant")
    TenantMembership.objects.create(tenant=tenant, user=user, role=TenantMembership.Role.ADMIN)
    project = Project.objects.create(tenant=tenant, name="ReportProject", slug="report-project")
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


@pytest.mark.django_db
class TestReportList:
    def test_loads(self, report_setup):
        user, tenant, project = report_setup
        client = _client(user, tenant, project)
        resp = client.get("/reports/")
        assert resp.status_code == 200

    def test_requires_login(self, report_setup):
        c = Client()
        resp = c.get("/reports/")
        assert resp.status_code == 302
        assert "/auth/login/" in resp.url


@pytest.mark.django_db
class TestExportDuplicatesCsv:
    def test_returns_csv(self, report_setup):
        user, tenant, project = report_setup
        job = AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED
        )
        client = _client(user, tenant, project)
        resp = client.get(f"/reports/{job.id}/duplicates.csv")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "text/csv"
        assert "attachment" in resp["Content-Disposition"]

    def test_wrong_project_404(self, report_setup):
        user, tenant, project = report_setup
        other_tenant = Tenant.objects.create(name="Other", slug="other")
        other_project = Project.objects.create(tenant=other_tenant, name="OtherP", slug="other-p")
        job = AnalysisJob.objects.create(
            tenant=other_tenant, project=other_project, status=AnalysisJob.Status.COMPLETED
        )
        client = _client(user, tenant, project)
        resp = client.get(f"/reports/{job.id}/duplicates.csv")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestExportContradictionsCsv:
    def test_returns_csv(self, report_setup):
        user, tenant, project = report_setup
        job = AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED
        )
        client = _client(user, tenant, project)
        resp = client.get(f"/reports/{job.id}/contradictions.csv")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "text/csv"


@pytest.mark.django_db
class TestExportReportJson:
    def test_returns_json(self, report_setup):
        user, tenant, project = report_setup
        job = AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED
        )
        client = _client(user, tenant, project)
        resp = client.get(f"/reports/{job.id}/report.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["analysis_job"] == str(job.id)
        assert "duplicates" in data
        assert "contradictions" in data
        assert "gaps" in data

    def test_nonexistent_job_404(self, report_setup):
        import uuid

        user, tenant, project = report_setup
        client = _client(user, tenant, project)
        resp = client.get(f"/reports/{uuid.uuid4()}/report.json")
        assert resp.status_code == 404
