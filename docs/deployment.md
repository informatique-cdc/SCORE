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
