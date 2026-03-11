"""Tests for tenants.middleware — TenantMiddleware."""

import pytest
from django.contrib.auth.models import User
from django.test import Client, RequestFactory

from tenants.models import (
    Project,
    ProjectMembership,
    Tenant,
    TenantMembership,
)


@pytest.fixture
def mw_user(db):
    return User.objects.create_user("mwuser", "mw@example.com", "pass1234")


@pytest.fixture
def mw_tenant(db):
    return Tenant.objects.create(name="MW Tenant", slug="mw-tenant")


@pytest.fixture
def mw_membership(mw_tenant, mw_user):
    return TenantMembership.objects.create(
        tenant=mw_tenant,
        user=mw_user,
        role=TenantMembership.Role.ADMIN,
    )


@pytest.fixture
def mw_project(mw_tenant):
    return Project.objects.create(tenant=mw_tenant, name="MW Project", slug="mw-project")


@pytest.fixture
def mw_project_membership(mw_project, mw_user):
    return ProjectMembership.objects.create(
        project=mw_project,
        user=mw_user,
        role=TenantMembership.Role.ADMIN,
    )


@pytest.mark.django_db
class TestTenantMiddleware:
    def test_unauthenticated_passes_through(self):
        client = Client()
        resp = client.get("/auth/login/")
        assert resp.status_code == 200

    def test_authenticated_without_tenant_redirects_to_select(self, mw_user):
        client = Client()
        client.login(username="mwuser", password="pass1234")
        resp = client.get("/dashboard/")
        assert resp.status_code == 302
        assert "tenants/select" in resp.url or "tenant" in resp.url.lower()

    def test_authenticated_with_membership_resolves_tenant(
        self,
        mw_user,
        mw_tenant,
        mw_membership,
        mw_project,
        mw_project_membership,
    ):
        client = Client()
        client.login(username="mwuser", password="pass1234")
        resp = client.get("/dashboard/")
        # Should load successfully (200) or redirect within dashboard
        assert resp.status_code == 200

    def test_session_tenant_id_persisted(
        self,
        mw_user,
        mw_tenant,
        mw_membership,
        mw_project,
        mw_project_membership,
    ):
        client = Client()
        client.login(username="mwuser", password="pass1234")
        client.get("/dashboard/")
        assert client.session.get("tenant_id") == str(mw_tenant.id)

    def test_session_project_id_persisted(
        self,
        mw_user,
        mw_tenant,
        mw_membership,
        mw_project,
        mw_project_membership,
    ):
        client = Client()
        client.login(username="mwuser", password="pass1234")
        client.get("/dashboard/")
        assert client.session.get("project_id") == str(mw_project.id)

    def test_admin_path_skips_tenant_resolution(self, mw_user):
        """Admin paths should not trigger tenant middleware redirects."""
        from tenants.middleware import TenantMiddleware

        factory = RequestFactory()
        request = factory.get("/admin/")
        request.user = mw_user
        request.session = {}

        # Run middleware directly — should not redirect
        mw = TenantMiddleware(lambda req: req)
        result = mw(request)
        # Middleware should pass through (return the request, not a redirect)
        assert (
            result is request
            or not hasattr(result, "url")
            or "tenant" not in getattr(result, "url", "")
        )

    def test_switching_tenant_updates_session(
        self, mw_user, mw_membership, mw_project, mw_project_membership
    ):
        tenant2 = Tenant.objects.create(name="Tenant 2", slug="tenant-2")
        TenantMembership.objects.create(
            tenant=tenant2,
            user=mw_user,
            role=TenantMembership.Role.ADMIN,
        )
        project2 = Project.objects.create(tenant=tenant2, name="P2", slug="p2")
        ProjectMembership.objects.create(
            project=project2,
            user=mw_user,
            role=TenantMembership.Role.ADMIN,
        )

        client = Client()
        client.login(username="mwuser", password="pass1234")

        # Select tenant 2
        resp = client.post("/tenants/select/", {"tenant_id": str(tenant2.id)})
        assert resp.status_code == 302
        assert client.session["tenant_id"] == str(tenant2.id)
