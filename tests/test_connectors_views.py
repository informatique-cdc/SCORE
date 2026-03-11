"""Tests for connectors.views — connector CRUD, detail, sync, delete."""
import json
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from connectors.models import ConnectorConfig
from ingestion.models import Document, IngestionJob
from tenants.models import Project, ProjectMembership, Tenant, TenantMembership
from tests.conftest import make_chunk, make_document


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn_setup(db):
    """Full setup: admin user + tenant + project + memberships."""
    user = User.objects.create_user("connuser", "conn@example.com", "pass1234")
    tenant = Tenant.objects.create(name="ConnTenant", slug="conn-tenant")
    TenantMembership.objects.create(
        tenant=tenant, user=user, role=TenantMembership.Role.ADMIN,
    )
    project = Project.objects.create(tenant=tenant, name="ConnProject", slug="conn-project")
    ProjectMembership.objects.create(
        project=project, user=user, role=TenantMembership.Role.ADMIN,
    )
    return user, tenant, project


def _client(user, tenant, project):
    c = Client()
    c.login(username=user.username, password="pass1234")
    session = c.session
    session["tenant_id"] = str(tenant.id)
    session["project_id"] = str(project.id)
    session.save()
    return c


# ---------------------------------------------------------------------------
# Connector list
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestConnectorList:
    def test_shows_connectors(self, conn_setup):
        user, tenant, project = conn_setup
        ConnectorConfig.objects.create(
            tenant=tenant, project=project,
            name="My Source", connector_type="generic",
        )
        client = _client(user, tenant, project)
        resp = client.get("/connectors/")
        assert resp.status_code == 200
        assert b"My Source" in resp.content

    def test_empty_list(self, conn_setup):
        user, tenant, project = conn_setup
        client = _client(user, tenant, project)
        resp = client.get("/connectors/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Connector create
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestConnectorCreate:
    def test_get_form(self, conn_setup):
        user, tenant, project = conn_setup
        client = _client(user, tenant, project)
        resp = client.get("/connectors/create/")
        assert resp.status_code == 200

    def test_post_creates_connector(self, conn_setup):
        user, tenant, project = conn_setup
        client = _client(user, tenant, project)
        resp = client.post("/connectors/create/", {
            "name": "Test Connector",
            "connector_type": "generic",
            "config_base_path": "/data/docs",
        })
        assert resp.status_code == 302
        connector = ConnectorConfig.objects.get(name="Test Connector")
        assert connector.tenant == tenant
        assert connector.project == project
        assert connector.connector_type == "generic"
        assert connector.config.get("base_path") == "/data/docs"

    def test_viewer_cannot_create(self, conn_setup):
        _, tenant, project = conn_setup
        viewer = User.objects.create_user("connviewer", "cv@example.com", "pass1234")
        TenantMembership.objects.create(
            tenant=tenant, user=viewer, role=TenantMembership.Role.VIEWER,
        )
        ProjectMembership.objects.create(
            project=project, user=viewer, role=TenantMembership.Role.VIEWER,
        )
        client = _client(viewer, tenant, project)
        resp = client.post("/connectors/create/", {
            "name": "Nope",
            "connector_type": "generic",
        })
        assert resp.status_code == 302
        assert not ConnectorConfig.objects.filter(name="Nope").exists()


# ---------------------------------------------------------------------------
# Connector detail
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestConnectorDetail:
    def test_shows_detail(self, conn_setup):
        user, tenant, project = conn_setup
        connector = ConnectorConfig.objects.create(
            tenant=tenant, project=project,
            name="Detail Source", connector_type="generic",
        )
        client = _client(user, tenant, project)
        resp = client.get(f"/connectors/{connector.pk}/")
        assert resp.status_code == 200
        assert b"Detail Source" in resp.content

    def test_shows_documents(self, conn_setup):
        user, tenant, project = conn_setup
        connector = ConnectorConfig.objects.create(
            tenant=tenant, project=project,
            name="WithDocs", connector_type="generic",
        )
        make_document(tenant, project, connector, title="Doc Alpha")

        client = _client(user, tenant, project)
        resp = client.get(f"/connectors/{connector.pk}/")
        assert resp.status_code == 200
        assert b"Doc Alpha" in resp.content


# ---------------------------------------------------------------------------
# Connector sync
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestConnectorSync:
    @patch("connectors.views.run_ingestion")
    def test_sync_creates_ingestion_job(self, mock_run, conn_setup):
        user, tenant, project = conn_setup
        connector = ConnectorConfig.objects.create(
            tenant=tenant, project=project,
            name="Syncable", connector_type="generic",
        )
        mock_task = mock_run.delay.return_value
        mock_task.id = "fake-celery-id"

        client = _client(user, tenant, project)
        resp = client.post(f"/connectors/{connector.pk}/sync/")
        assert resp.status_code == 302
        assert IngestionJob.objects.filter(connector=connector).exists()
        mock_run.delay.assert_called_once()

    @patch("connectors.views.run_ingestion")
    def test_viewer_cannot_sync(self, mock_run, conn_setup):
        _, tenant, project = conn_setup
        viewer = User.objects.create_user("syncviewer", "syncv@example.com", "pass1234")
        TenantMembership.objects.create(
            tenant=tenant, user=viewer, role=TenantMembership.Role.VIEWER,
        )
        ProjectMembership.objects.create(
            project=project, user=viewer, role=TenantMembership.Role.VIEWER,
        )
        connector = ConnectorConfig.objects.create(
            tenant=tenant, project=project,
            name="NoSync", connector_type="generic",
        )
        client = _client(viewer, tenant, project)
        resp = client.post(f"/connectors/{connector.pk}/sync/")
        assert resp.status_code == 302
        mock_run.delay.assert_not_called()


# ---------------------------------------------------------------------------
# Connector delete
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestConnectorDelete:
    @patch("connectors.views.get_vector_store")
    def test_delete_removes_connector(self, mock_get_vs, conn_setup):
        user, tenant, project = conn_setup
        connector = ConnectorConfig.objects.create(
            tenant=tenant, project=project,
            name="Deletable", connector_type="generic",
        )
        mock_vs = mock_get_vs.return_value

        client = _client(user, tenant, project)
        resp = client.post(f"/connectors/{connector.pk}/delete/")
        assert resp.status_code == 302
        assert not ConnectorConfig.objects.filter(pk=connector.pk).exists()

    @patch("connectors.views.get_vector_store")
    def test_delete_cleans_up_vectors(self, mock_get_vs, conn_setup):
        user, tenant, project = conn_setup
        connector = ConnectorConfig.objects.create(
            tenant=tenant, project=project,
            name="WithVecs", connector_type="generic",
        )
        doc = make_document(tenant, project, connector, title="Vec Doc")
        mock_vs = mock_get_vs.return_value

        client = _client(user, tenant, project)
        resp = client.post(f"/connectors/{connector.pk}/delete/")
        assert resp.status_code == 302
        mock_vs.delete_by_documents.assert_called_once()

    @patch("connectors.views.get_vector_store")
    def test_viewer_cannot_delete(self, mock_get_vs, conn_setup):
        _, tenant, project = conn_setup
        viewer = User.objects.create_user("delviewer", "dv@example.com", "pass1234")
        TenantMembership.objects.create(
            tenant=tenant, user=viewer, role=TenantMembership.Role.VIEWER,
        )
        ProjectMembership.objects.create(
            project=project, user=viewer, role=TenantMembership.Role.VIEWER,
        )
        connector = ConnectorConfig.objects.create(
            tenant=tenant, project=project,
            name="NoDel", connector_type="generic",
        )
        client = _client(viewer, tenant, project)
        resp = client.post(f"/connectors/{connector.pk}/delete/")
        assert resp.status_code == 302
        assert ConnectorConfig.objects.filter(pk=connector.pk).exists()


# ---------------------------------------------------------------------------
# Document content API
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDocumentContent:
    def test_returns_json(self, conn_setup):
        user, tenant, project = conn_setup
        connector = ConnectorConfig.objects.create(
            tenant=tenant, project=project,
            name="DocContent", connector_type="generic",
        )
        doc = make_document(tenant, project, connector, title="API Doc")
        make_chunk(tenant, doc, 0, "First paragraph of content.")
        make_chunk(tenant, doc, 1, "Second paragraph.")

        client = _client(user, tenant, project)
        resp = client.get(f"/connectors/{connector.pk}/documents/{doc.pk}/content/")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["title"] == "API Doc"
        assert "First paragraph" in data["content_html"]
        assert "Second paragraph" in data["content_html"]

    def test_empty_document(self, conn_setup):
        user, tenant, project = conn_setup
        connector = ConnectorConfig.objects.create(
            tenant=tenant, project=project,
            name="EmptyDoc", connector_type="generic",
        )
        doc = make_document(tenant, project, connector, title="Empty")

        client = _client(user, tenant, project)
        resp = client.get(f"/connectors/{connector.pk}/documents/{doc.pk}/content/")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert "Aucun contenu" in data["content_html"]
