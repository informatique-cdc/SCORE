# ---- CSS stage ----
FROM node:22-slim AS css

WORKDIR /css

COPY package.json package-lock.json ./
RUN npm ci

# Tailwind ne génère que les classes qu'il rencontre : les gabarits sont une entrée du build.
COPY assets ./assets
COPY analysis/templates ./analysis/templates
COPY chat/templates ./chat/templates
COPY connectors/templates ./connectors/templates
COPY dashboard/templates ./dashboard/templates
COPY dashboard/static/js ./dashboard/static/js
COPY reports/templates ./reports/templates
COPY tenants/templates ./tenants/templates

RUN npm run build

# ---- Builder stage ----
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Runtime stage ----
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app

COPY . .
COPY --from=css /css/dashboard/static/css/app.css dashboard/static/css/app.css
COPY --from=css /css/dashboard/static/fonts dashboard/static/fonts

RUN python manage.py collectstatic --noinput 2>/dev/null || true

ENV PORT=8000

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health/ || exit 1

CMD sh -c "gunicorn score.wsgi:application --bind 0.0.0.0:${PORT} --workers 4 --threads 4 --timeout 120"
