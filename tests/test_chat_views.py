"""Tests for chat views: home, ask, conversations, system prompt."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from chat.models import Conversation, Message
from tenants.models import Project, ProjectMembership, Tenant, TenantMembership


@pytest.fixture
def chat_setup(db):
    user = User.objects.create_user("chatuser", "chat@example.com", "pass1234")
    tenant = Tenant.objects.create(name="ChatTenant", slug="chat-tenant")
    TenantMembership.objects.create(tenant=tenant, user=user, role=TenantMembership.Role.ADMIN)
    project = Project.objects.create(tenant=tenant, name="ChatProject", slug="chat-project")
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
class TestChatHome:
    def test_loads(self, chat_setup):
        user, tenant, project = chat_setup
        client = _client(user, tenant, project)
        resp = client.get("/chat/")
        assert resp.status_code == 200

    def test_requires_login(self, chat_setup):
        c = Client()
        resp = c.get("/chat/")
        assert resp.status_code == 302
        assert "/auth/login/" in resp.url


@pytest.mark.django_db
class TestChatAsk:
    @patch("chat.views.get_llm_client")
    @patch("chat.views.ask_documents")
    def test_returns_answer(self, mock_ask, mock_llm, chat_setup):
        user, tenant, project = chat_setup
        mock_resp = MagicMock()
        mock_resp.content = "Test Title"
        mock_llm.return_value.chat.return_value = mock_resp
        mock_ask.return_value = {
            "answer": "Test answer",
            "sources": [{"title": "doc1"}],
            "suggestions": [],
        }
        client = _client(user, tenant, project)
        resp = client.post(
            "/chat/ask/",
            json.dumps({"message": "Hello?"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "Test answer"
        assert "conversation_id" in data

    @patch("chat.views.get_llm_client")
    @patch("chat.views.ask_documents")
    def test_creates_conversation(self, mock_ask, mock_llm, chat_setup):
        user, tenant, project = chat_setup
        mock_resp = MagicMock()
        mock_resp.content = "Test Title"
        mock_llm.return_value.chat.return_value = mock_resp
        mock_ask.return_value = {"answer": "Hi", "sources": [], "suggestions": []}
        client = _client(user, tenant, project)
        client.post(
            "/chat/ask/",
            json.dumps({"message": "Hello?"}),
            content_type="application/json",
        )
        assert Conversation.objects.filter(user=user, project=project).count() == 1
        assert Message.objects.count() == 2  # user + assistant

    def test_empty_message_rejected(self, chat_setup):
        user, tenant, project = chat_setup
        client = _client(user, tenant, project)
        resp = client.post(
            "/chat/ask/",
            json.dumps({"message": ""}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_invalid_json_rejected(self, chat_setup):
        user, tenant, project = chat_setup
        client = _client(user, tenant, project)
        resp = client.post(
            "/chat/ask/",
            "not json",
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("chat.views.ask_documents", side_effect=ConnectionError("timeout"))
    def test_pipeline_error_returns_500(self, mock_ask, chat_setup):
        user, tenant, project = chat_setup
        client = _client(user, tenant, project)
        resp = client.post(
            "/chat/ask/",
            json.dumps({"message": "Hello?"}),
            content_type="application/json",
        )
        assert resp.status_code == 500


@pytest.mark.django_db
class TestConversationMessages:
    @patch("chat.views.get_llm_client")
    @patch("chat.views.ask_documents")
    def test_returns_messages(self, mock_ask, mock_llm, chat_setup):
        user, tenant, project = chat_setup
        mock_resp = MagicMock()
        mock_resp.content = "Test Title"
        mock_llm.return_value.chat.return_value = mock_resp
        mock_ask.return_value = {"answer": "Reply", "sources": [], "suggestions": []}
        client = _client(user, tenant, project)

        # Create conversation via ask
        resp = client.post(
            "/chat/ask/",
            json.dumps({"message": "Hey"}),
            content_type="application/json",
        )
        conv_id = resp.json()["conversation_id"]

        # Fetch messages
        resp = client.get(f"/chat/conversations/{conv_id}/messages/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 2

    def test_other_users_conversation_404(self, chat_setup):
        user, tenant, project = chat_setup
        other = User.objects.create_user("other", "other@example.com", "pass1234")
        conv = Conversation.objects.create(
            tenant=tenant, project=project, user=other, title="Private"
        )
        client = _client(user, tenant, project)
        resp = client.get(f"/chat/conversations/{conv.id}/messages/")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestConversationDelete:
    def test_delete_conversation(self, chat_setup):
        user, tenant, project = chat_setup
        conv = Conversation.objects.create(
            tenant=tenant, project=project, user=user, title="To delete"
        )
        client = _client(user, tenant, project)
        resp = client.post(f"/chat/conversations/{conv.id}/delete/")
        assert resp.status_code == 200
        assert not Conversation.objects.filter(id=conv.id).exists()


@pytest.mark.django_db
class TestSaveSystemPrompt:
    def test_save_valid_prompt(self, chat_setup):
        user, tenant, project = chat_setup
        client = _client(user, tenant, project)
        resp = client.post(
            "/chat/config/system-prompt/",
            json.dumps({"system_prompt": "Answer based on {context}"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["ok"]

    def test_missing_context_variable(self, chat_setup):
        user, tenant, project = chat_setup
        client = _client(user, tenant, project)
        resp = client.post(
            "/chat/config/system-prompt/",
            json.dumps({"system_prompt": "No variable here"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
