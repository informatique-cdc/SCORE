"""
Connector configuration models.

Each connector defines how to reach a document source (SharePoint, Confluence, etc.)
and stores credentials securely.
"""

import logging
import os
import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _
from tenants.models import ProjectScopedModel

logger = logging.getLogger(__name__)


class ConnectorConfig(ProjectScopedModel):
    """Configuration for a document source connector."""

    class ConnectorType(models.TextChoices):
        SHAREPOINT = "sharepoint", _("SharePoint")
        CONFLUENCE = "confluence", _("Confluence")
        GENERIC = "generic", _("Générique (Fichier/HTTP)")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    connector_type = models.CharField(max_length=20, choices=ConnectorType.choices)
    enabled = models.BooleanField(default=True)

    # Connection config stored as JSON (credentials are references to env vars or secrets)
    config = models.JSONField(
        default=dict,
        help_text="Connection parameters (site_url, space_key, base_path, etc.)",
    )
    # Credential references — never store raw secrets in DB
    credential_ref = models.CharField(
        max_length=500,
        blank=True,
        help_text="Reference to credential store (env var name or secret manager path)",
    )
    # Encrypted secret — per-tenant Fernet-encrypted credential value
    encrypted_secret = models.TextField(
        blank=True,
        default="",
        help_text="Fernet-encrypted secret (set via set_secret, read via get_secret)",
    )

    schedule_cron = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Cron expression for scheduled syncs (empty = manual only)",
    )
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=20, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.connector_type})"

    def set_secret(self, plain_text: str) -> None:
        """Encrypt and store a secret value for this connector's tenant."""
        from connectors.crypto import encrypt_secret

        self.encrypted_secret = encrypt_secret(plain_text, str(self.tenant_id))

    def get_secret(self) -> str:
        """Return the decrypted secret, falling back to credential_ref env var lookup."""
        if self.encrypted_secret:
            from connectors.crypto import decrypt_secret

            decrypted = decrypt_secret(self.encrypted_secret, str(self.tenant_id))
            if decrypted:
                return decrypted

        # Fallback: treat credential_ref as an env var name
        if self.credential_ref:
            return os.environ.get(self.credential_ref, "")

        return ""
