"""Tests for tenants/context_processors.py — tenant_context and onboarding."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from analysis.models import AnalysisJob
from tenants.context_processors import _get_onboarding_steps, tenant_context
from tenants.models import ProjectMembership, TenantMembership
from tests.conftest import make_document


@pytest.fixture
def factory():
    return RequestFactory()


@pytest.mark.django_db
class TestTenantContext:
    def test_anonymous_user(self, factory):
        request = factory.get("/")
        request.user = AnonymousUser()
        ctx = tenant_context(request)
        assert ctx["current_tenant"] is None
        assert ctx["user_projects"] == []
        assert ctx["user_tenants"] == []

    def test_authenticated_user_without_tenant(self, factory, user):
        request = factory.get("/")
        request.user = user
        ctx = tenant_context(request)
        assert ctx["current_tenant"] is None
        assert ctx["user_tenants"] == []

    def test_authenticated_user_with_tenant(self, factory, tenant, user):
        TenantMembership.objects.create(tenant=tenant, user=user, role="admin")
        request = factory.get("/")
        request.user = user
        request.tenant = tenant
        request.membership = TenantMembership.objects.get(tenant=tenant, user=user)
        request.project = None
        request.project_membership = None
        ctx = tenant_context(request)
        assert ctx["current_tenant"] == tenant
        assert len(ctx["user_tenants"]) == 1

    def test_with_project_lists_projects(self, factory, tenant, project, user):
        TenantMembership.objects.create(tenant=tenant, user=user, role="admin")
        pm = ProjectMembership.objects.create(project=project, user=user, role="admin")
        request = factory.get("/")
        request.user = user
        request.tenant = tenant
        request.membership = TenantMembership.objects.get(tenant=tenant, user=user)
        request.project = project
        request.project_membership = pm
        ctx = tenant_context(request)
        assert len(ctx["user_projects"]) == 1
        assert ctx["user_projects"][0].project == project


@pytest.mark.django_db
class TestOnboardingSteps:
    def test_no_project(self, tenant):
        steps = _get_onboarding_steps(tenant, project=None)
        assert steps is not None
        assert steps[1]["done"] is False  # "Créer un projet"

    def test_with_project_no_connector(self, tenant, project):
        steps = _get_onboarding_steps(tenant, project)
        assert steps[1]["done"] is True  # project exists
        assert steps[2]["done"] is False  # connector missing

    def test_with_connector_no_documents(self, tenant, project, connector):
        steps = _get_onboarding_steps(tenant, project)
        assert steps[2]["done"] is True  # connector exists
        assert steps[3]["done"] is False  # no ready docs

    def test_with_documents_no_analysis(self, tenant, project, connector):
        make_document(tenant, project, connector, title="D", status="ready")
        steps = _get_onboarding_steps(tenant, project)
        assert steps[3]["done"] is True  # ready docs exist
        assert steps[4]["done"] is False  # no analysis

    def test_all_done_returns_none(self, tenant, project, connector):
        make_document(tenant, project, connector, title="D", status="ready")
        AnalysisJob.objects.create(
            tenant=tenant,
            project=project,
            status=AnalysisJob.Status.COMPLETED,
        )
        result = _get_onboarding_steps(tenant, project)
        assert result is None  # checklist hidden when all done
