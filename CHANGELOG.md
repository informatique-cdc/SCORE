# Changelog

All notable changes to SCORE will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Elasticsearch connector** — connect to Elasticsearch clusters and ingest documents
  - API key, basic auth, bearer token, and Elastic Cloud (`cloud_id`) authentication
  - PIT + `search_after` pagination with automatic fallback to `helpers.scan()` for older ES versions
  - Configurable field mapping (`content_field`, `title_field`, `author_field`, `date_field`)
  - Custom Elasticsearch Query DSL filter support
  - TLS certificate verification control
  - HTML content auto-detection
  - Fallback content extraction when configured content field is missing
  - 35 unit tests covering all connector functionality
- Per-tenant encrypted connector secrets: users can now enter API keys/secrets directly in the connector creation UI, stored encrypted in the DB using Fernet with HKDF-derived per-tenant keys
- `FIELD_ENCRYPTION_KEY` setting for dedicated encryption key (falls back to `SECRET_KEY`)
- `connectors/crypto.py` module with `encrypt_secret()` / `decrypt_secret()` utilities
- `encrypted_secret` field on `ConnectorConfig` model with `set_secret()` / `get_secret()` methods
- Password input field in connector creation form (both standalone page and modal)
- `migrate_connector_secrets` management command to migrate existing env-var-based credentials into encrypted secrets
- `cryptography>=42.0` dependency
- **Elasticsearch connector** — connect to Elasticsearch clusters and ingest documents
  - API key, basic auth, bearer token, and Elastic Cloud (`cloud_id`) authentication
  - PIT + `search_after` pagination with automatic fallback to `helpers.scan()` for older ES versions
  - Configurable field mapping (`content_field`, `title_field`, `author_field`, `date_field`)
  - Custom Elasticsearch Query DSL filter support
  - TLS certificate verification control
  - HTML content auto-detection
  - Fallback content extraction when configured content field is missing
  - 35 unit tests covering all connector functionality
- Apache 2.0 LICENSE file
- `CONTRIBUTING.md`, `CHANGELOG.md`, `SECURITY.md`
- `SECRET_KEY` startup validation (refuses to start with placeholder key in production)
- Full Django password validators (was only `MinimumLengthValidator`)
- Production HTTPS hardening settings (HSTS, SSL redirect, secure cookies)
- Multi-stage Dockerfile with health check
- `apps.py` for all Django apps
- `gunicorn` as explicit dependency
- `compute_penalty_score()` shared scoring function (eliminates 3x duplication)

### Changed
- Moved `scoring.py` from `dashboard/` to `score/` (shared across apps)
- Consolidated `_grade()` / `_audit_grade()` into single `grade()` function
- Synced `pyproject.toml` and `requirements.txt` dependencies
- Pinned `sqlite-vec==0.1.6` consistently across both dep files
- Added upper bound on `django-allauth` (`>=65.0,<67`)
- Added `faker` to dev dependencies

## [0.1.0] - 2025-12-01

### Added
- Initial release
- Multi-tenant document repository analysis
- Duplicate detection with LLM-assisted classification
- Contradiction detection with severity scoring
- Gap analysis with coverage scoring
- Topic clustering (HDBSCAN)
- RAG-based audit (hygiene, structure, coverage, coherence, retrievability, governance)
- SCORE 0-100 scoring with A-E letter grades
- PDF report generation with radar charts
- RAG chat interface
- SharePoint and Confluence connectors
- Docker deployment support
- French and English internationalization
