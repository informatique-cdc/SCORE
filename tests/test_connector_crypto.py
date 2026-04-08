"""Tests for per-tenant connector secret encryption."""

import uuid

import pytest

from connectors.crypto import decrypt_secret, encrypt_secret


# ---------------------------------------------------------------------------
# Pure crypto tests
# ---------------------------------------------------------------------------


class TestEncryptDecrypt:
    def test_roundtrip(self, settings):
        settings.FIELD_ENCRYPTION_KEY = "test-master-key-for-unit-tests"
        tenant_id = str(uuid.uuid4())
        secret = "my-super-secret-api-key"

        encrypted = encrypt_secret(secret, tenant_id)
        assert encrypted != secret
        assert decrypt_secret(encrypted, tenant_id) == secret

    def test_empty_string_returns_empty(self, settings):
        settings.FIELD_ENCRYPTION_KEY = "test-key"
        tenant_id = str(uuid.uuid4())

        assert encrypt_secret("", tenant_id) == ""
        assert decrypt_secret("", tenant_id) == ""

    def test_different_tenants_produce_different_ciphertext(self, settings):
        settings.FIELD_ENCRYPTION_KEY = "test-key"
        secret = "same-secret"
        t1 = str(uuid.uuid4())
        t2 = str(uuid.uuid4())

        enc1 = encrypt_secret(secret, t1)
        enc2 = encrypt_secret(secret, t2)
        assert enc1 != enc2

    def test_wrong_tenant_cannot_decrypt(self, settings):
        settings.FIELD_ENCRYPTION_KEY = "test-key"
        tenant_id = str(uuid.uuid4())
        wrong_tenant = str(uuid.uuid4())
        secret = "sensitive-value"

        encrypted = encrypt_secret(secret, tenant_id)
        result = decrypt_secret(encrypted, wrong_tenant)
        assert result == ""  # graceful failure

    def test_falls_back_to_secret_key(self, settings):
        settings.FIELD_ENCRYPTION_KEY = ""
        settings.SECRET_KEY = "django-fallback-secret"
        tenant_id = str(uuid.uuid4())
        secret = "fallback-test"

        encrypted = encrypt_secret(secret, tenant_id)
        assert decrypt_secret(encrypted, tenant_id) == secret

    def test_corrupted_ciphertext_returns_empty(self, settings):
        settings.FIELD_ENCRYPTION_KEY = "test-key"
        tenant_id = str(uuid.uuid4())

        result = decrypt_secret("not-valid-ciphertext", tenant_id)
        assert result == ""


# ---------------------------------------------------------------------------
# Model integration tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConnectorConfigSecrets:
    def test_set_and_get_secret(self, settings, tenant, project):
        settings.FIELD_ENCRYPTION_KEY = "test-model-key"
        from connectors.models import ConnectorConfig

        connector = ConnectorConfig.objects.create(
            tenant=tenant,
            project=project,
            name="Secret Test",
            connector_type="generic",
        )
        connector.set_secret("my-api-token")
        connector.save()

        # Reload from DB
        connector.refresh_from_db()
        assert connector.encrypted_secret != ""
        assert connector.encrypted_secret != "my-api-token"
        assert connector.get_secret() == "my-api-token"

    def test_env_var_fallback(self, settings, tenant, project, monkeypatch):
        settings.FIELD_ENCRYPTION_KEY = "test-model-key"
        monkeypatch.setenv("MY_TEST_SECRET", "env-secret-value")
        from connectors.models import ConnectorConfig

        connector = ConnectorConfig.objects.create(
            tenant=tenant,
            project=project,
            name="Env Fallback",
            connector_type="generic",
            credential_ref="MY_TEST_SECRET",
        )
        # No encrypted_secret set — should fall back to env var
        assert connector.get_secret() == "env-secret-value"

    def test_encrypted_secret_preferred_over_env(self, settings, tenant, project, monkeypatch):
        settings.FIELD_ENCRYPTION_KEY = "test-model-key"
        monkeypatch.setenv("MY_TEST_SECRET", "env-value")
        from connectors.models import ConnectorConfig

        connector = ConnectorConfig.objects.create(
            tenant=tenant,
            project=project,
            name="Prefer Encrypted",
            connector_type="generic",
            credential_ref="MY_TEST_SECRET",
        )
        connector.set_secret("encrypted-value")
        connector.save()
        connector.refresh_from_db()

        assert connector.get_secret() == "encrypted-value"

    def test_cross_tenant_isolation(self, settings, db):
        settings.FIELD_ENCRYPTION_KEY = "test-isolation-key"
        from connectors.models import ConnectorConfig
        from tenants.models import Project, Tenant

        t1 = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        t2 = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        p1 = Project.objects.create(tenant=t1, name="P1", slug="p1")
        p2 = Project.objects.create(tenant=t2, name="P2", slug="p2")

        c1 = ConnectorConfig.objects.create(
            tenant=t1, project=p1, name="C1", connector_type="generic"
        )
        c2 = ConnectorConfig.objects.create(
            tenant=t2, project=p2, name="C2", connector_type="generic"
        )

        c1.set_secret("shared-secret")
        c1.save()
        c2.set_secret("shared-secret")
        c2.save()

        c1.refresh_from_db()
        c2.refresh_from_db()

        # Same plaintext but different ciphertext (different tenant keys)
        assert c1.encrypted_secret != c2.encrypted_secret
        # Each can decrypt its own
        assert c1.get_secret() == "shared-secret"
        assert c2.get_secret() == "shared-secret"

    def test_no_secret_returns_empty(self, settings, tenant, project):
        settings.FIELD_ENCRYPTION_KEY = "test-key"
        from connectors.models import ConnectorConfig

        connector = ConnectorConfig.objects.create(
            tenant=tenant,
            project=project,
            name="No Secret",
            connector_type="generic",
        )
        assert connector.get_secret() == ""
