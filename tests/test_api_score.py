import hashlib
import json
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from api.models import APIToken
from analysis.models import AnalysisJob
from tenants.models import Project, Tenant


@pytest.fixture
def score_setup(db):
    tenant = Tenant.objects.create(name="ScoreTenant", slug="score-tenant")
    project = Project.objects.create(tenant=tenant, name="ScoreProject", slug="score-project")
    user = User.objects.create_user(username="scoreuser", password="pass")
    raw_token = "score_score_test"
    APIToken.objects.create(
        key_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        user=user,
        tenant=tenant,
        project=project,
        name="score-token",
    )
    return {"tenant": tenant, "project": project, "token": raw_token}


class TestScoreEndpoint:
    def test_get_score(self, client, score_setup):
        response = client.get(
            "/api/v1/score/",
            HTTP_AUTHORIZATION=f"Bearer {score_setup['token']}",
        )
        assert response.status_code == 200
        data = response.json()
        assert "score" in data
        assert "grade" in data
        assert "dimensions" in data

    def test_get_score_no_auth(self, client):
        response = client.get("/api/v1/score/")
        assert response.status_code == 401


class TestAnalysisEndpoint:
    def test_trigger_analysis(self, client, score_setup):
        with patch("api.views_score.run_unified_pipeline") as mock_task:
            mock_task.delay.return_value.id = "fake-celery-id"
            response = client.post(
                "/api/v1/analysis/",
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {score_setup['token']}",
            )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "running"

    def test_get_analysis_status(self, client, score_setup):
        job = AnalysisJob.objects.create(
            tenant=score_setup["tenant"],
            project=score_setup["project"],
            status=AnalysisJob.Status.COMPLETED,
        )
        response = client.get(
            f"/api/v1/analysis/{job.id}/",
            HTTP_AUTHORIZATION=f"Bearer {score_setup['token']}",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"

    def test_get_analysis_not_found(self, client, score_setup):
        import uuid
        response = client.get(
            f"/api/v1/analysis/{uuid.uuid4()}/",
            HTTP_AUTHORIZATION=f"Bearer {score_setup['token']}",
        )
        assert response.status_code == 404
