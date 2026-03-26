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
