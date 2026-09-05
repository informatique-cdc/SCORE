import hashlib
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from api.models import APIToken
from analysis.models import AnalysisJob, AuditJob, AuditAxisResult
from tenants.models import Project, Tenant


@pytest.fixture
def audit_setup(db):
    tenant = Tenant.objects.create(name="AuditTenant", slug="audit-tenant")
    project = Project.objects.create(tenant=tenant, name="AuditProject", slug="audit-project")
    user = User.objects.create_user(username="audituser", password="pass")
    raw_token = "audit_test_token"
    APIToken.objects.create(
        key_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        user=user,
        tenant=tenant,
        project=project,
        name="audit-token",
    )
    return {"tenant": tenant, "project": project, "token": raw_token}


class TestAuditDetail:
    def test_get_audit_results_with_axis_scores(self, client, audit_setup):
        audit_job = AuditJob.objects.create(
            tenant=audit_setup["tenant"],
            project=audit_setup["project"],
            status=AuditJob.Status.COMPLETED,
            overall_score=78.5,
            overall_grade="B",
        )
        AuditAxisResult.objects.create(
            tenant=audit_setup["tenant"],
            project=audit_setup["project"],
            audit_job=audit_job,
            axis="hygiene",
            score=85.0,
            metrics={"broken_links": 2},
            chart_data={"labels": ["OK", "KO"]},
            details={"items": []},
        )
        AuditAxisResult.objects.create(
            tenant=audit_setup["tenant"],
            project=audit_setup["project"],
            audit_job=audit_job,
            axis="structure",
            score=72.0,
            metrics={"avg_chunk_size": 512},
            chart_data={},
            details={},
        )

        response = client.get(
            f"/api/v1/audit/{audit_job.id}/",
            HTTP_AUTHORIZATION=f"Bearer {audit_setup['token']}",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == str(audit_job.id)
        assert data["status"] == "completed"
        assert data["overall_score"] == 78.5
        assert data["overall_grade"] == "B"
        assert len(data["axes"]) == 2
        axes_by_name = {a["axis"]: a for a in data["axes"]}
        assert axes_by_name["hygiene"]["score"] == 85.0
        assert axes_by_name["structure"]["score"] == 72.0

    def test_get_audit_not_found(self, client, audit_setup):
        response = client.get(
            f"/api/v1/audit/{uuid.uuid4()}/",
            HTTP_AUTHORIZATION=f"Bearer {audit_setup['token']}",
        )
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "NOT_FOUND"


class TestAuditTrigger:
    def test_post_trigger_audit(self, client, audit_setup):
        with patch("api.views_audit.run_unified_pipeline") as mock_task:
            mock_task.delay.return_value.id = "fake-celery-id"
            response = client.post(
                "/api/v1/audit/",
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {audit_setup['token']}",
            )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "running"
        # Verify the AnalysisJob was created with includes_audit=True
        job = AnalysisJob.objects.get(id=data["job_id"])
        assert job.includes_audit is True
        assert job.celery_task_id == "fake-celery-id"
