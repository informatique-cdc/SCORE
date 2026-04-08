# Production Deployment Guide

## Prerequisites

- Docker and Docker Compose
- A Redis instance (or use the built-in SQLite broker for single-server deployments)
- An LLM API key (OpenAI, Azure OpenAI, or Azure Mistral)

## Quick Start with Docker

```bash
# Copy and configure environment
cp .env.example .env
# Edit .env with your API keys and settings

# Generate a secure SECRET_KEY
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# Build and run
docker-compose up -d
```

## Environment Configuration

### Required Settings

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Cryptographically random key (see above) |
| `FIELD_ENCRYPTION_KEY` | Key for encrypting connector secrets (falls back to `SECRET_KEY`). Generate with: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DEBUG` | **Must be `False` in production** |
| `ALLOWED_HOSTS` | Comma-separated list of your domain(s) |
| `LLM_PROVIDER` | `openai`, `azure`, or `azure_mistral` |
| API keys | Corresponding API key for your provider |

### Security Settings (automatic when DEBUG=False)

When `DEBUG=False`, the following are enabled automatically:
- `SECURE_SSL_REDIRECT` — redirects HTTP to HTTPS
- `SECURE_HSTS_SECONDS` — 1-year HSTS header
- `SESSION_COOKIE_SECURE` — cookies only over HTTPS
- `CSRF_COOKIE_SECURE` — CSRF cookie only over HTTPS
- `SECRET_KEY` validation — refuses to start with placeholder key

### Default Credentials

The sample data script creates default users with weak passwords. **Change these immediately** or do not load sample data in production:

```bash
# Create a proper admin user instead
python manage.py createsuperuser
```

## Migrating Connector Secrets (Upgrading from Pre-Encryption Versions)

If you are upgrading from a version that used `credential_ref` (env var names) for connector credentials, follow these steps after deploying the new code:

```bash
# 1. Run the schema migration to add the encrypted_secret column
python manage.py migrate

# 2. Generate and set FIELD_ENCRYPTION_KEY in .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Add the output to .env as FIELD_ENCRYPTION_KEY=<generated-key>

# 3. Ensure the env vars referenced by your connectors are still set

# 4. Preview what will be migrated (dry run)
python manage.py migrate_connector_secrets

# 5. Encrypt the credentials
python manage.py migrate_connector_secrets --apply

# 6. Verify connectors still work (trigger a sync)

# 7. Optionally remove env var references (secrets are now in the DB)
python manage.py migrate_connector_secrets --apply --clear-ref
```

**Important:** Back up your database before running the migration. The `FIELD_ENCRYPTION_KEY` (or `SECRET_KEY` if no dedicated key is set) is required to decrypt secrets — if you lose it, encrypted credentials cannot be recovered.

---

## Database Considerations

### SQLite (Default)

SCORE ships with SQLite as the default database, which is suitable for:
- Single-server deployments with low concurrency
- Evaluation and testing
- Small teams (< 10 concurrent users)

**Limitations:**
- No concurrent write support (writes are serialized)
- Performance degrades beyond ~100K documents
- Not suitable for multi-server deployments
- No built-in replication or backup

### Migrating to PostgreSQL

For production deployments with concurrent users, migrate to PostgreSQL:

1. Install PostgreSQL and create a database:
   ```bash
   createdb score
   ```

2. Update `.env`:
   ```
   DATABASE_URL=postgres://user:password@localhost:5432/score
   ```

3. Add `psycopg2-binary` to your dependencies:
   ```bash
   pip install psycopg2-binary
   ```

4. Update `settings.py` to use `dj-database-url` or `django-environ` for database config:
   ```python
   DATABASES = {
       "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
   }
   ```

5. Add connection pooling:
   ```python
   CONN_MAX_AGE = 600  # Keep connections alive for 10 minutes
   ```

6. Run migrations:
   ```bash
   python manage.py migrate
   ```

## Celery Workers

SCORE uses Celery for background task processing (analysis pipelines, audits).

### With Redis (recommended)
```bash
# .env
CELERY_BROKER_BACKEND=redis
CELERY_BROKER_URL=redis://localhost:6379/0

# Start worker
celery -A score worker -l info --pool=threads
```

### Without Redis (SQLite broker)
```bash
# .env
CELERY_BROKER_BACKEND=database

# Start worker
celery -A score worker -l info --pool=threads
```

## Reverse Proxy

Place SCORE behind a reverse proxy (nginx, Caddy) for:
- TLS termination
- Static file serving
- Request buffering

Example nginx configuration:

```nginx
server {
    listen 443 ssl;
    server_name score.example.com;

    ssl_certificate /etc/ssl/certs/score.pem;
    ssl_certificate_key /etc/ssl/private/score.key;

    location /static/ {
        alias /app/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Monitoring

- Health endpoint: `GET /health/` — returns 200 if the app is running
- Celery: use `celery -A score inspect active` to check worker status
- Logs: structured logging to stdout (configure your log aggregator accordingly)

## Backups

- **SQLite:** copy `data/db.sqlite3` and `data/vec.sqlite3` while the application is stopped
- **PostgreSQL:** use `pg_dump` for regular backups
- **Media files:** back up the `media/` directory
