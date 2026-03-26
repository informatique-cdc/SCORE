import hashlib
import pytest
from django.contrib.auth.models import User
from api.models import APIToken
from tenants.models import Tenant, Project


@pytest.fixture
def api_tenant(db):
    return Tenant.objects.create(name="API Tenant", slug="api-tenant")


@pytest.fixture
def api_project(api_tenant):
    return Project.objects.create(tenant=api_tenant, name="API Project", slug="api-project")


@pytest.fixture
def api_user(db):
    return User.objects.create_user(username="apiuser", password="pass")


class TestAPITokenModel:
    def test_create_token(self, api_tenant, api_project, api_user):
        raw_token = "score_test_token_abc123"
        token = APIToken.objects.create(
            key_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            user=api_user,
            tenant=api_tenant,
            project=api_project,
            name="test-token",
        )
        assert token.is_active is True
        assert token.name == "test-token"
        assert str(token) == "test-token (apiuser)"

    def test_lookup_by_hash(self, api_tenant, api_project, api_user):
        raw_token = "score_lookup_token"
        key_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        APIToken.objects.create(
            key_hash=key_hash,
            user=api_user,
            tenant=api_tenant,
            project=api_project,
            name="lookup-token",
        )
        found = APIToken.objects.filter(key_hash=key_hash, is_active=True).first()
        assert found is not None
        assert found.name == "lookup-token"


import json
from django.test import RequestFactory
from django.http import JsonResponse
from api.auth import authenticate_token, require_api_token


class TestTokenAuth:
    def test_valid_token(self, api_tenant, api_project, api_user):
        raw_token = "score_valid_token"
        APIToken.objects.create(
            key_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            user=api_user,
            tenant=api_tenant,
            project=api_project,
            name="valid",
        )
        result = authenticate_token(raw_token)
        assert result is not None
        assert result["user"] == api_user
        assert result["tenant"] == api_tenant
        assert result["project"] == api_project

    def test_invalid_token(self, db):
        result = authenticate_token("nonexistent")
        assert result is None

    def test_inactive_token(self, api_tenant, api_project, api_user):
        raw_token = "score_inactive"
        APIToken.objects.create(
            key_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            user=api_user,
            tenant=api_tenant,
            project=api_project,
            name="inactive",
            is_active=False,
        )
        result = authenticate_token(raw_token)
        assert result is None


class TestRequireApiTokenDecorator:
    def test_missing_header(self):
        @require_api_token
        def dummy_view(request):
            return JsonResponse({"ok": True})

        factory = RequestFactory()
        request = factory.get("/api/v1/test/")
        response = dummy_view(request)
        assert response.status_code == 401

    def test_valid_header(self, api_tenant, api_project, api_user):
        @require_api_token
        def dummy_view(request):
            return JsonResponse({"user": request.api_user.username})

        raw_token = "score_decorator_test"
        APIToken.objects.create(
            key_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            user=api_user,
            tenant=api_tenant,
            project=api_project,
            name="decorator-test",
        )
        factory = RequestFactory()
        request = factory.get("/api/v1/test/", HTTP_AUTHORIZATION=f"Bearer {raw_token}")
        response = dummy_view(request)
        assert response.status_code == 200
