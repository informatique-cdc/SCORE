"""Pytest configuration and fixtures for SCORE tests."""
import uuid

import pytest
from django.contrib.auth.models import User

from tenants.models import Tenant, TenantMembership


# Use plain static files storage in tests (no manifest required)
@pytest.fixture(autouse=True)
def _use_plain_staticfiles(settings):
    settings.STORAGES = {
        **getattr(settings, "STORAGES", {}),
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }


@pytest.fixture
def tenant(db):
    """Create a test tenant."""
    return Tenant.objects.create(
        name="Test Tenant",
        slug="test-tenant",
    )


@pytest.fixture
def user(db):
    """Create a test user."""
    return User.objects.create_user(
        username="testuser",
        password="testpass123",
        email="test@example.com",
    )


@pytest.fixture
def admin_membership(tenant, user):
    """Create admin membership for the test user."""
    return TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=TenantMembership.Role.ADMIN,
    )


@pytest.fixture
def editor_membership(tenant, user):
    """Create editor membership for the test user."""
    return TenantMembership.objects.create(
        tenant=tenant,
        user=user,
        role=TenantMembership.Role.EDITOR,
    )


# ---------------------------------------------------------------------------
# Shared fixtures for analysis pipeline tests
# ---------------------------------------------------------------------------

from tenants.models import Project  # noqa: E402
from connectors.models import ConnectorConfig  # noqa: E402
from analysis.models import AnalysisJob  # noqa: E402
from ingestion.models import Document, DocumentChunk  # noqa: E402


@pytest.fixture
def project(tenant):
    return Project.objects.create(tenant=tenant, name="Test Project", slug="test-project")


@pytest.fixture
def connector(tenant, project):
    return ConnectorConfig.objects.create(
        tenant=tenant, project=project,
        name="Test Connector", connector_type="generic",
    )


@pytest.fixture
def analysis_job(tenant, project):
    return AnalysisJob.objects.create(tenant=tenant, project=project)


def make_document(tenant, project, connector, title="Doc", status="ready", **kwargs):
    """Helper to create a Document with sensible defaults."""
    import hashlib
    return Document.objects.create(
        tenant=tenant, project=project, connector=connector,
        source_id=kwargs.pop("source_id", str(uuid.uuid4())),
        title=title, content_hash=hashlib.sha256(title.encode()).hexdigest(),
        status=status, **kwargs,
    )


def make_chunk(tenant, doc, index=0, content="Sample text."):
    """Helper to create a DocumentChunk."""
    import hashlib
    return DocumentChunk.objects.create(
        tenant=tenant, document=doc, chunk_index=index,
        content=content, content_hash=hashlib.sha256(content.encode()).hexdigest(),
        token_count=len(content.split()),
    )


def make_llm_response(content="", model="gpt-5-mini", usage=None):
    """Build an LLMResponse for mocking."""
    from llm.client import LLMResponse
    return LLMResponse(content=content, model=model, usage=usage or {})


def random_embedding(dim=1536):
    """Random unit vector for mock embeddings."""
    import numpy as np
    v = np.random.randn(dim).astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-10)
