# Changelog

All notable changes to SCORE will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
