"""Tests for dashboard views: home, stats partials, feedback."""

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from dashboard.models import Feedback
from dashboard.views import _spark_points, _spark_trend
from tenants.models import Project, ProjectMembership, Tenant, TenantMembership


@pytest.fixture
def dash_setup(db):
    user = User.objects.create_user("dashuser", "dash@example.com", "pass1234")
    tenant = Tenant.objects.create(name="DashTenant", slug="dash-tenant")
    TenantMembership.objects.create(tenant=tenant, user=user, role=TenantMembership.Role.ADMIN)
    project = Project.objects.create(tenant=tenant, name="DashProject", slug="dash-project")
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


class TestSparkPoints:
    def test_spans_the_full_band(self):
        assert _spark_points([0, 5, 10]) == "0.0,24.0 60.0,13.0 120.0,2.0"

    def test_flat_series_sits_mid_height(self):
        assert _spark_points([4, 4, 4]) == "0.0,13.0 60.0,13.0 120.0,13.0"

    def test_needs_two_points(self):
        assert _spark_points([7]) == ""
        assert _spark_points([]) == ""


class TestSparkTrend:
    def test_rise_is_flagged_orange(self):
        assert _spark_trend([3, 8]) == {"label": "+5", "tone": "d"}

    def test_drop_is_flagged_green(self):
        assert _spark_trend([8, 3]) == {"label": "−5", "tone": "a"}

    def test_no_change_is_stable(self):
        assert _spark_trend([3, 3])["tone"] == "none"

    def test_needs_two_points(self):
        assert _spark_trend([3]) is None


@pytest.mark.django_db
class TestDashboardHome:
    def test_loads(self, dash_setup):
        user, tenant, project = dash_setup
        client = _client(user, tenant, project)
        resp = client.get("/dashboard/")
        assert resp.status_code == 200

    def test_requires_login(self, dash_setup):
        c = Client()
        resp = c.get("/dashboard/")
        assert resp.status_code == 302
        assert "/auth/login/" in resp.url


@pytest.mark.django_db
class TestStatsPartial:
    def test_loads(self, dash_setup):
        user, tenant, project = dash_setup
        client = _client(user, tenant, project)
        resp = client.get("/dashboard/_stats/")
        assert resp.status_code == 200

    def test_requires_login(self, dash_setup):
        c = Client()
        resp = c.get("/dashboard/_stats/")
        assert resp.status_code == 302


@pytest.mark.django_db
class TestLatestAnalysisPartial:
    def test_loads(self, dash_setup):
        user, tenant, project = dash_setup
        client = _client(user, tenant, project)
        resp = client.get("/dashboard/_latest-analysis/")
        assert resp.status_code == 200


@pytest.mark.django_db
class TestRecentJobsPartial:
    def test_loads(self, dash_setup):
        user, tenant, project = dash_setup
        client = _client(user, tenant, project)
        resp = client.get("/dashboard/_recent-jobs/")
        assert resp.status_code == 200


@pytest.mark.django_db
class TestSCOREDetailJson:
    def test_returns_json(self, dash_setup):
        user, tenant, project = dash_setup
        client = _client(user, tenant, project)
        resp = client.get("/dashboard/_score-detail/")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/json"


@pytest.mark.django_db
class TestSubmitFeedback:
    def test_creates_feedback(self, dash_setup):
        user, tenant, project = dash_setup
        client = _client(user, tenant, project)
        resp = client.post(
            "/dashboard/feedback/",
            json.dumps(
                {
                    "type": "feedback",
                    "area": "analysis",
                    "subject": "Great tool",
                    "description": "Very useful.",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["ok"]
        assert Feedback.objects.filter(user=user).count() == 1

    def test_get_not_allowed(self, dash_setup):
        user, tenant, project = dash_setup
        client = _client(user, tenant, project)
        resp = client.get("/dashboard/feedback/")
        assert resp.status_code == 405
