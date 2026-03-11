"""Tests for tenants.views — tenant/project CRUD, user management."""

import pytest
from django.contrib.auth.models import User
from django.test import Client

from tenants.models import (
    Project,
    ProjectMembership,
    Tenant,
    TenantMembership,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_setup(db):
    """Full setup: user + tenant + membership + project + project_membership."""
    user = User.objects.create_user("admin", "admin@example.com", "pass1234")
    tenant = Tenant.objects.create(name="Acme Corp", slug="acme-corp")
    membership = TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=TenantMembership.Role.ADMIN,
    )
    project = Project.objects.create(tenant=tenant, name="Main", slug="main")
    ProjectMembership.objects.create(
        project=project,
        user=user,
        role=TenantMembership.Role.ADMIN,
    )
    return user, tenant, membership, project


def _logged_in_client(user, tenant=None, project=None):
    """Return a Client logged in with session keys set."""
    client = Client()
    client.login(username=user.username, password="pass1234")
    if tenant:
        session = client.session
        session["tenant_id"] = str(tenant.id)
        if project:
            session["project_id"] = str(project.id)
        session.save()
    return client


# ---------------------------------------------------------------------------
# Tenant select
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTenantSelect:
    def test_get_shows_tenants(self, admin_setup):
        user, tenant, _, _ = admin_setup
        client = _logged_in_client(user, tenant)
        resp = client.get("/tenants/select/")
        assert resp.status_code == 200

    def test_post_selects_tenant(self, admin_setup):
        user, tenant, _, _ = admin_setup
        client = _logged_in_client(user)
        resp = client.post("/tenants/select/", {"tenant_id": str(tenant.id)})
        assert resp.status_code == 302
        assert client.session["tenant_id"] == str(tenant.id)

    def test_requires_login(self):
        client = Client()
        resp = client.get("/tenants/select/")
        assert resp.status_code == 302
        assert "/auth/login/" in resp.url


# ---------------------------------------------------------------------------
# Tenant create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTenantCreate:
    def test_creates_tenant_and_admin_membership(self, admin_setup):
        user, tenant, _, _ = admin_setup
        client = _logged_in_client(user, tenant)
        resp = client.post("/tenants/create/", {"name": "New Space"})
        assert resp.status_code == 302

        new_tenant = Tenant.objects.get(name="New Space")
        assert new_tenant.slug == "new-space"
        assert TenantMembership.objects.filter(
            tenant=new_tenant,
            user=user,
            role=TenantMembership.Role.ADMIN,
        ).exists()
        assert client.session["tenant_id"] == str(new_tenant.id)

    def test_empty_name_rejected(self, admin_setup):
        user, tenant, _, _ = admin_setup
        client = _logged_in_client(user, tenant)
        before_count = Tenant.objects.count()
        resp = client.post("/tenants/create/", {"name": ""})
        assert resp.status_code == 302
        assert Tenant.objects.count() == before_count

    def test_duplicate_name_rejected(self, admin_setup):
        user, tenant, _, _ = admin_setup
        client = _logged_in_client(user, tenant)
        resp = client.post("/tenants/create/", {"name": "Acme Corp"})
        assert resp.status_code == 302
        assert Tenant.objects.filter(name="Acme Corp").count() == 1

    def test_slug_collision_generates_unique(self, admin_setup):
        user, tenant, _, _ = admin_setup
        Tenant.objects.create(name="Other", slug="other")
        client = _logged_in_client(user, tenant)
        # "Other" slug is taken, so a new one should get "other-1"
        resp = client.post("/tenants/create/", {"name": "Other!"})
        assert resp.status_code == 302
        # The new tenant should exist with a variant slug
        assert Tenant.objects.filter(slug__startswith="other").count() >= 2


# ---------------------------------------------------------------------------
# Project list and create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProjectList:
    def test_shows_projects(self, admin_setup):
        user, tenant, _, project = admin_setup
        client = _logged_in_client(user, tenant, project)
        resp = client.get("/tenants/projects/")
        assert resp.status_code == 200

    def test_post_selects_project(self, admin_setup):
        user, tenant, _, project = admin_setup
        client = _logged_in_client(user, tenant, project)
        resp = client.post("/tenants/projects/", {"project_id": str(project.id)})
        assert resp.status_code == 302
        assert client.session["project_id"] == str(project.id)


@pytest.mark.django_db
class TestProjectCreate:
    def test_creates_project(self, admin_setup):
        user, tenant, _, _ = admin_setup
        client = _logged_in_client(user, tenant)
        resp = client.post(
            "/tenants/projects/create/",
            {
                "name": "New Project",
                "description": "A new project.",
            },
        )
        assert resp.status_code == 302
        assert Project.objects.filter(tenant=tenant, name="New Project").exists()
        new_project = Project.objects.get(tenant=tenant, name="New Project")
        # Creator should have admin membership
        assert ProjectMembership.objects.filter(
            project=new_project,
            user=user,
            role=TenantMembership.Role.ADMIN,
        ).exists()

    def test_non_admin_cannot_create(self, admin_setup):
        _, tenant, _, project = admin_setup
        viewer = User.objects.create_user("viewer", "v@example.com", "pass1234")
        TenantMembership.objects.create(
            tenant=tenant,
            user=viewer,
            role=TenantMembership.Role.VIEWER,
        )
        ProjectMembership.objects.create(
            project=project,
            user=viewer,
            role=TenantMembership.Role.VIEWER,
        )
        client = _logged_in_client(viewer, tenant, project)
        resp = client.post("/tenants/projects/create/", {"name": "Nope"})
        # Should redirect without creating
        assert resp.status_code == 302
        assert not Project.objects.filter(name="Nope").exists()


# ---------------------------------------------------------------------------
# User invite
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserInvite:
    def test_invite_new_email_creates_user_and_membership(self, admin_setup):
        user, tenant, _, project = admin_setup
        client = _logged_in_client(user, tenant, project)
        resp = client.post(
            "/tenants/users/invite/",
            {
                "email": "newguy@example.com",
                "role": "editor",
            },
        )
        assert resp.status_code == 302

        invited_user = User.objects.get(email="newguy@example.com")
        assert TenantMembership.objects.filter(
            tenant=tenant,
            user=invited_user,
            role=TenantMembership.Role.EDITOR,
        ).exists()
        # Should also get project memberships
        assert ProjectMembership.objects.filter(
            project=project,
            user=invited_user,
        ).exists()

    def test_invite_existing_user(self, admin_setup):
        user, tenant, _, project = admin_setup
        existing = User.objects.create_user("existing", "existing@example.com", "pass1234")
        client = _logged_in_client(user, tenant, project)
        resp = client.post(
            "/tenants/users/invite/",
            {
                "email": "existing@example.com",
                "role": "viewer",
            },
        )
        assert resp.status_code == 302
        assert TenantMembership.objects.filter(tenant=tenant, user=existing).exists()

    def test_invite_duplicate_member_rejected(self, admin_setup):
        user, tenant, _, project = admin_setup
        client = _logged_in_client(user, tenant, project)
        # admin is already a member
        resp = client.post(
            "/tenants/users/invite/",
            {
                "email": "admin@example.com",
                "role": "viewer",
            },
        )
        assert resp.status_code == 302
        # Should still only have 1 membership
        assert TenantMembership.objects.filter(tenant=tenant, user=user).count() == 1

    def test_invite_empty_email_rejected(self, admin_setup):
        user, tenant, _, project = admin_setup
        client = _logged_in_client(user, tenant, project)
        before = User.objects.count()
        resp = client.post("/tenants/users/invite/", {"email": "", "role": "viewer"})
        assert resp.status_code == 302
        assert User.objects.count() == before


# ---------------------------------------------------------------------------
# User role update
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserRoleUpdate:
    def test_update_role(self, admin_setup):
        user, tenant, _, project = admin_setup
        target = User.objects.create_user("target", "target@example.com", "pass1234")
        tm = TenantMembership.objects.create(
            tenant=tenant,
            user=target,
            role=TenantMembership.Role.VIEWER,
        )
        ProjectMembership.objects.create(
            project=project,
            user=target,
            role=TenantMembership.Role.VIEWER,
        )

        client = _logged_in_client(user, tenant, project)
        resp = client.post(f"/tenants/users/{tm.pk}/role/", {"role": "editor"})
        assert resp.status_code == 302
        tm.refresh_from_db()
        assert tm.role == TenantMembership.Role.EDITOR

    def test_cannot_demote_last_admin(self, admin_setup):
        user, tenant, membership, project = admin_setup
        client = _logged_in_client(user, tenant, project)
        resp = client.post(f"/tenants/users/{membership.pk}/role/", {"role": "viewer"})
        assert resp.status_code == 302
        membership.refresh_from_db()
        assert membership.role == TenantMembership.Role.ADMIN  # unchanged


# ---------------------------------------------------------------------------
# User remove
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserRemove:
    def test_remove_member(self, admin_setup):
        user, tenant, _, project = admin_setup
        target = User.objects.create_user("removeme", "rm@example.com", "pass1234")
        tm = TenantMembership.objects.create(
            tenant=tenant,
            user=target,
            role=TenantMembership.Role.VIEWER,
        )
        ProjectMembership.objects.create(
            project=project,
            user=target,
            role=TenantMembership.Role.VIEWER,
        )

        client = _logged_in_client(user, tenant, project)
        resp = client.post(f"/tenants/users/{tm.pk}/remove/")
        assert resp.status_code == 302
        assert not TenantMembership.objects.filter(pk=tm.pk).exists()
        assert not ProjectMembership.objects.filter(user=target, project=project).exists()

    def test_cannot_remove_self(self, admin_setup):
        user, tenant, membership, project = admin_setup
        client = _logged_in_client(user, tenant, project)
        resp = client.post(f"/tenants/users/{membership.pk}/remove/")
        assert resp.status_code == 302
        assert TenantMembership.objects.filter(pk=membership.pk).exists()


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSettingsPage:
    def test_get_settings(self, admin_setup):
        user, tenant, _, project = admin_setup
        client = _logged_in_client(user, tenant, project)
        resp = client.get("/tenants/settings/")
        assert resp.status_code == 200

    def test_update_profile(self, admin_setup):
        user, tenant, _, project = admin_setup
        client = _logged_in_client(user, tenant, project)
        resp = client.post(
            "/tenants/settings/",
            {
                "form_type": "profil",
                "first_name": "John",
                "last_name": "Doe",
            },
        )
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.first_name == "John"
        assert user.last_name == "Doe"

    def test_update_tenant_name(self, admin_setup):
        user, tenant, _, project = admin_setup
        client = _logged_in_client(user, tenant, project)
        resp = client.post(
            "/tenants/settings/",
            {
                "form_type": "espace",
                "name": "Renamed Corp",
            },
        )
        assert resp.status_code == 302
        tenant.refresh_from_db()
        assert tenant.name == "Renamed Corp"

    def test_update_project(self, admin_setup):
        user, tenant, _, project = admin_setup
        client = _logged_in_client(user, tenant, project)
        resp = client.post(
            "/tenants/settings/?tab=projet",
            {
                "form_type": "projet",
                "name": "Renamed Project",
                "description": "New desc",
            },
        )
        assert resp.status_code == 302
        project.refresh_from_db()
        assert project.name == "Renamed Project"

    def test_viewer_cannot_update_tenant(self, admin_setup):
        _, tenant, _, project = admin_setup
        viewer = User.objects.create_user("settviewer", "sv@example.com", "pass1234")
        TenantMembership.objects.create(
            tenant=tenant,
            user=viewer,
            role=TenantMembership.Role.VIEWER,
        )
        ProjectMembership.objects.create(
            project=project,
            user=viewer,
            role=TenantMembership.Role.VIEWER,
        )
        client = _logged_in_client(viewer, tenant, project)
        resp = client.post(
            "/tenants/settings/",
            {
                "form_type": "espace",
                "name": "Hacked",
            },
        )
        # Should not update — viewer lacks is_admin
        assert resp.status_code == 200
        tenant.refresh_from_db()
        assert tenant.name == "Acme Corp"
