# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | Yes                |

## Reporting a Vulnerability

If you discover a security vulnerability in SCORE, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please email: **_to_be_defined**

Include the following in your report:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### What to Expect

- Acknowledgment within 48 hours
- An assessment and timeline within 1 week
- A fix or mitigation plan communicated privately before public disclosure

## Security Best Practices for Deployment

- Never use the default `SECRET_KEY` in production
- Generate a proper key: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`
- Set a dedicated `FIELD_ENCRYPTION_KEY` for connector secret encryption: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
- Keep `.env` files out of version control (already in `.gitignore`)
- Use HTTPS in production (HSTS is enabled when `DEBUG=False`)
- Rotate API keys regularly
- Change default credentials immediately after setup
- Use a production-grade database (PostgreSQL) for concurrent deployments

## Connector Secret Encryption

Connector credentials (API keys, client secrets) can be stored encrypted in the database using per-tenant Fernet encryption:

- **Key derivation:** HKDF (SHA-256) derives a unique Fernet key per tenant from the master `FIELD_ENCRYPTION_KEY` (or `SECRET_KEY` as fallback)
- **Info prefix:** `docuscore-connector-secret-v1:` followed by the tenant UUID, enabling future key rotation
- **Tenant isolation:** Each tenant's secrets are encrypted with a different derived key — a secret encrypted for tenant A cannot be decrypted by tenant B
- **Graceful fallback:** Connectors with only a `credential_ref` (env var name) continue working without encryption
- **Failure mode:** Decryption errors return an empty string and log a warning — the application does not crash
- **Migration:** Use `python manage.py migrate_connector_secrets --apply` to encrypt existing env-var-based credentials (see below)

### Migrating Existing Credentials

If you already have connectors configured with `credential_ref` pointing to environment variables, use the management command to encrypt them:

```bash
# 1. Set FIELD_ENCRYPTION_KEY in .env (or rely on SECRET_KEY fallback)
# 2. Ensure the env vars referenced by credential_ref are set

# Dry run — preview what would be migrated
python manage.py migrate_connector_secrets

# Apply — encrypt env var values into encrypted_secret
python manage.py migrate_connector_secrets --apply

# Optional: clear credential_ref after verifying encryption works
python manage.py migrate_connector_secrets --apply --clear-ref
```

The command is safe to run multiple times — it skips connectors that already have an `encrypted_secret` value.
