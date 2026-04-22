import hashlib
import json
from unittest.mock import patch, MagicMock

import pytest
from django.contrib.auth.models import User

from api.models import APIToken
from ingestion.models import Document
from tenants.models import Project, Tenant


@pytest.fixture
def api_setup(db):
    tenant = Tenant.objects.create(name="DocTenant", slug="doc-tenant")
    project = Project.objects.create(tenant=tenant, name="DocProject", slug="doc-project")
    user = User.objects.create_user(username="docuser", password="pass")
    raw_token = "score_doc_test_token"
    APIToken.objects.create(
        key_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        user=user,
        tenant=tenant,
        project=project,
        name="doc-token",
    )
    return {"tenant": tenant, "project": project, "user": user, "token": raw_token}


def _mock_llm_and_vecstore():
    """Return context managers that mock LLM embed and vector store."""
    mock_llm = MagicMock()
    mock_llm.embed.return_value = [[0.1] * 768]
    mock_vs = MagicMock()
    return (
        patch("api.views_documents.get_llm_client", return_value=mock_llm),
        patch("api.views_documents.get_vector_store", return_value=mock_vs),
    )


class TestDocumentCreate:
    def test_create_document(self, client, api_setup):
        p1, p2 = _mock_llm_and_vecstore()
        with p1, p2:
            response = client.post(
                "/api/v1/documents/",
                data=json.dumps({
                    "title": "Test Doc",
                    "content": "This is a test document with enough words for chunking.",
                    "content_type": "text/plain",
                }),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {api_setup['token']}",
            )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Test Doc"
        assert "id" in data

    def test_create_document_no_auth(self, client):
        response = client.post(
            "/api/v1/documents/",
            data=json.dumps({"title": "X", "content": "Y"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_create_document_missing_fields(self, client, api_setup):
        response = client.post(
            "/api/v1/documents/",
            data=json.dumps({"title": "No content"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {api_setup['token']}",
        )
        assert response.status_code == 400


class TestDocumentList:
    def test_list_documents(self, client, api_setup):
        p1, p2 = _mock_llm_and_vecstore()
        with p1, p2:
            client.post(
                "/api/v1/documents/",
                data=json.dumps({
                    "title": "Listed Doc",
                    "content": "Content here for listing test.",
                    "content_type": "text/plain",
                }),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {api_setup['token']}",
            )
        response = client.get(
            "/api/v1/documents/",
            HTTP_AUTHORIZATION=f"Bearer {api_setup['token']}",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert len(data["documents"]) >= 1


class TestDocumentDelete:
    def test_delete_document(self, client, api_setup):
        p1, p2 = _mock_llm_and_vecstore()
        with p1, p2:
            resp = client.post(
                "/api/v1/documents/",
                data=json.dumps({
                    "title": "To Delete",
                    "content": "Will be deleted soon.",
                    "content_type": "text/plain",
                }),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {api_setup['token']}",
            )
        doc_id = resp.json()["id"]
        with patch("api.views_documents.get_vector_store", return_value=MagicMock()):
            response = client.delete(
                f"/api/v1/documents/{doc_id}/",
                HTTP_AUTHORIZATION=f"Bearer {api_setup['token']}",
            )
        assert response.status_code == 204
