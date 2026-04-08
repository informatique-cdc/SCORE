"""Tests for the migrate_connector_secrets management command."""

import pytest
from django.core.management import call_command
from io import StringIO

from connectors.models import ConnectorConfig


@pytest.mark.django_db
class TestMigrateConnectorSecrets:
    def test_dry_run_no_connectors(self, tenant, project):
        out = StringIO()
        call_command("migrate_connector_secrets", stdout=out)
        assert "Nothing to migrate" in out.getvalue()

    def test_dry_run_shows_candidates(self, settings, tenant, project, monkeypatch):
        settings.FIELD_ENCRYPTION_KEY = "test-migration-key"
        monkeypatch.setenv("SP_SECRET", "my-sharepoint-secret")

        ConnectorConfig.objects.create(
            tenant=tenant,
            project=project,
            name="Old SP",
            connector_type="sharepoint",
            credential_ref="SP_SECRET",
        )

        out = StringIO()
        call_command("migrate_connector_secrets", stdout=out)
        output = out.getvalue()
        assert "WOULD MIGRATE" in output
        assert "my-***" in output  # masked value

        # Verify nothing was actually changed
        connector = ConnectorConfig.objects.get(name="Old SP")
        assert connector.encrypted_secret == ""
        assert connector.credential_ref == "SP_SECRET"

    def test_apply_encrypts_secrets(self, settings, tenant, project, monkeypatch):
        settings.FIELD_ENCRYPTION_KEY = "test-migration-key"
        monkeypatch.setenv("CONF_TOKEN", "confluence-api-token")

        ConnectorConfig.objects.create(
            tenant=tenant,
            project=project,
            name="Old Confluence",
            connector_type="confluence",
            credential_ref="CONF_TOKEN",
        )

        out = StringIO()
        call_command("migrate_connector_secrets", "--apply", stdout=out)
        output = out.getvalue()
        assert "MIGRATED" in output

        connector = ConnectorConfig.objects.get(name="Old Confluence")
        assert connector.encrypted_secret != ""
        assert connector.get_secret() == "confluence-api-token"
        # credential_ref preserved by default
        assert connector.credential_ref == "CONF_TOKEN"

    def test_apply_with_clear_ref(self, settings, tenant, project, monkeypatch):
        settings.FIELD_ENCRYPTION_KEY = "test-migration-key"
        monkeypatch.setenv("MY_KEY", "secret-value")

        ConnectorConfig.objects.create(
            tenant=tenant,
            project=project,
            name="Clear Ref",
            connector_type="generic",
            credential_ref="MY_KEY",
        )

        out = StringIO()
        call_command("migrate_connector_secrets", "--apply", "--clear-ref", stdout=out)

        connector = ConnectorConfig.objects.get(name="Clear Ref")
        assert connector.encrypted_secret != ""
        assert connector.credential_ref == ""
        assert connector.get_secret() == "secret-value"

    def test_skips_unset_env_vars(self, settings, tenant, project):
        settings.FIELD_ENCRYPTION_KEY = "test-migration-key"

        ConnectorConfig.objects.create(
            tenant=tenant,
            project=project,
            name="Missing Env",
            connector_type="generic",
            credential_ref="NONEXISTENT_VAR",
        )

        out = StringIO()
        call_command("migrate_connector_secrets", "--apply", stdout=out)
        output = out.getvalue()
        assert "SKIPPED" in output
        assert "empty or unset" in output

    def test_skips_already_encrypted(self, settings, tenant, project, monkeypatch):
        settings.FIELD_ENCRYPTION_KEY = "test-migration-key"
        monkeypatch.setenv("SOME_KEY", "some-value")

        connector = ConnectorConfig.objects.create(
            tenant=tenant,
            project=project,
            name="Already Done",
            connector_type="generic",
            credential_ref="SOME_KEY",
        )
        connector.set_secret("already-encrypted")
        connector.save()

        out = StringIO()
        call_command("migrate_connector_secrets", stdout=out)
        assert "Nothing to migrate" in out.getvalue()
