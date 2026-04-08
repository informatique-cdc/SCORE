"""
Per-tenant encryption for connector secrets.

Uses HKDF key derivation to produce a unique Fernet key per tenant from a
single master key, so each tenant's secrets are cryptographically isolated.
"""

import base64
import logging

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

logger = logging.getLogger(__name__)

_HKDF_INFO_PREFIX = b"docuscore-connector-secret-v1:"


def _get_master_key() -> bytes:
    """Return the master key bytes used for HKDF derivation."""
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or settings.SECRET_KEY
    return key.encode("utf-8") if isinstance(key, str) else key


def _derive_fernet_key(tenant_id: str) -> bytes:
    """Derive a tenant-specific Fernet key via HKDF."""
    hkdf = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO_PREFIX + tenant_id.encode("utf-8"),
    )
    derived = hkdf.derive(_get_master_key())
    return base64.urlsafe_b64encode(derived)


def encrypt_secret(plain_text: str, tenant_id: str) -> str:
    """Encrypt a secret string for a specific tenant. Returns base64 ciphertext."""
    if not plain_text:
        return ""
    key = _derive_fernet_key(tenant_id)
    f = Fernet(key)
    return f.encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted_text: str, tenant_id: str) -> str:
    """Decrypt a tenant-specific secret. Returns empty string on failure."""
    if not encrypted_text:
        return ""
    try:
        key = _derive_fernet_key(tenant_id)
        f = Fernet(key)
        return f.decrypt(encrypted_text.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        logger.warning("Failed to decrypt secret for tenant %s", tenant_id)
        return ""
