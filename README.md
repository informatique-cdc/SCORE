# SCORE - _SCORE Curates Organizational Repository for Embeddings_

Enterprise document repository analysis tool. Ingests documents from multiple sources, detects duplicates, extracts claims, finds contradictions, clusters topics, identifies documentation gaps, flags hallucination risks, runs RAG quality audits, and produces a Nutri-Score-style quality grade (A–E).

Built with Django, SQLite + sqlite-vec, Celery, OpenAI/Azure OpenAI, and spaCy.

---

## Table of Contents

- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Docker Deployment](#docker-deployment)
- [Configuration](#configuration)
- [Running the Application](#running-the-application)
- [Task Queue (Celery)](#task-queue-celery)
- [Django Apps](#django-apps)
- [Database Schema](#database-schema)
- [URL Routes](#url-routes)
- [Authentication & Multi-Tenancy](#authentication--multi-tenancy)
- [DocuScore Scoring](#docuscore-scoring)
- [Tests](#tests)
- [Dependencies](#dependencies)
- [Contributing](#contributing)
- [License](#license)

---

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Dashboard   │────▶│  Django Web  │────▶│  SQLite (ORM)    │
│  (Bootstrap  │     │  Server      │     │  db.sqlite3      │
│   + D3.js)   │     └──────┬───────┘     └──────────────────┘
└─────────────┘            │
                           │ enqueue
                           ▼
                    ┌──────────────┐     ┌──────────────────┐
                    │  Celery      │────▶│  sqlite-vec      │
                    │  Worker      │     │  vec.sqlite3     │
                    └──────┬───────┘     └──────────────────┘
                           │
                    ┌──────┴───────┐
                    │  OpenAI /    │
                    │  Azure API   │
                    └──────────────┘
```

- **Web server**: Django 5.1, serves the dashboard, chat interface, and triggers async jobs.
- **Task queue**: Celery workers run ingestion, analysis, and audit pipelines.
- **Primary database**: SQLite via Django ORM (`db.sqlite3`) for all relational data.
- **Vector database**: Separate SQLite file (`vec.sqlite3`) with the sqlite-vec extension for KNN embedding search.
- **LLM**: Unified client supporting OpenAI, Azure OpenAI, and Azure Mistral for embeddings, chat completions, and JSON-mode structured output. Supports fallback models on rate-limit errors.
- **Semantic graph**: spaCy-based concept extraction with NetworkX graph and FAISS index for knowledge-map visualizations.

---

## Project Structure

```
docuscore/
├── analysis/                  # Document analysis (duplicates, contradictions, gaps, clustering, hallucination)
│   ├── models.py              #   AnalysisJob, AuditJob, AuditAxisResult, DuplicateGroup,
│   │                          #   DuplicatePair, Claim, ContradictionPair, TopicCluster,
│   │                          #   ClusterMembership, GapReport, HallucinationReport,
│   │                          #   TreeNode, PipelineTrace, PhaseTrace, TraceEvent
│   ├── duplicates.py          #   Multi-signal duplicate detection + LLM verification
│   ├── contradictions.py      #   Claims-based contradiction & staleness detection
│   ├── clustering.py          #   HDBSCAN/KMeans topic clustering + tree building
│   ├── gaps.py                #   QG/RAG coverage, orphan, stale, and adjacent gap detection
│   ├── hallucination.py       #   RAG hallucination risk detection (acronyms, jargon, hedging)
│   ├── constants.py           #   Shared analysis constants
│   ├── pipeline.py            #   Pipeline orchestration (analysis + audit phases)
│   ├── tasks.py               #   Celery tasks: run_analysis, run_audit
│   ├── audit/                 #   RAG quality audit (6 axes, no LLM)
│   │   ├── base.py            #     BaseAuditAxis ABC
│   │   ├── hygiene.py         #     Corpus hygiene: near-duplicates, boilerplate, language mix
│   │   ├── structure_rag.py   #     RAG structure: chunk size, info density, readability
│   │   ├── coverage.py        #     Semantic coverage: topic diversity, outliers
│   │   ├── coherence.py       #     Internal coherence: terminology consistency
│   │   └── ...                #     Retrievability, governance axes
│   ├── views.py               #   Analysis list, detail, sub-reports, JSON APIs
│   ├── views_audit.py         #   Audit list, detail, per-axis reports
│   ├── urls.py
│   └── templates/analysis/    #   list, detail, duplicates, contradictions, clusters,
│                              #   gaps, hallucinations, tree, knowledge-map, trace, audit
│
├── chat/                      # RAG chat interface
│   ├── models.py              #   ChatConfig, Conversation, Message
│   ├── views.py               #   Chat page, RAG ask endpoint, conversation management
│   ├── rag.py                 #   RAG pipeline: retrieve chunks + LLM answer
│   ├── rag_techniques.py      #   Advanced RAG retrieval strategies
│   ├── urls.py
│   └── templates/chat/        #   home (chat UI)
│
├── connectors/                # Document source connectors
│   ├── models.py              #   ConnectorConfig
│   ├── base.py                #   BaseConnector ABC, RawDocument, connector registry
│   ├── generic.py             #   Filesystem + HTTP connector
│   ├── sharepoint.py          #   SharePoint Online connector (optional dep)
│   ├── confluence.py          #   Confluence connector (optional dep)
│   ├── views.py               #   CRUD + sync trigger
│   ├── urls.py
│   └── templates/connectors/  #   list, create, detail
│
├── dashboard/                 # Main web UI
│   ├── views.py               #   Home view with stats, scoring, and recent jobs
│   ├── scoring.py             #   Re-exports from docuscore.scoring (backward compat)
│   └── templates/dashboard/   #   base.html (Bootstrap 5 + D3.js layout), home.html, login.html
│
├── docuscore/                 # Django project root
│   ├── settings.py            #   Settings (reads .env + config.yaml), security hardening
│   ├── urls.py                #   Root URL router
│   ├── celery.py              #   Celery app configuration
│   ├── scoring.py             #   DocuScore 0-100 scoring with A-E grades (7 dimensions)
│   ├── utils.py               #   Shared utilities (JSON parsing, etc.)
│   ├── middleware.py          #   Content Security Policy middleware
│   ├── health.py              #   Health check endpoint
│   └── wsgi.py
│
├── ingestion/                 # Document ingestion pipeline
│   ├── models.py              #   Document, DocumentChunk, IngestionJob
│   ├── pipeline.py            #   IngestionPipeline orchestrator
│   ├── extraction.py          #   Text extraction (HTML, PDF, DOCX, PPTX, Markdown)
│   ├── chunking.py            #   Heading-aware and fixed-token chunking
│   ├── hashing.py             #   Content normalization + SHA-256 hashing
│   ├── tasks.py               #   Celery task: run_ingestion
│   ├── views.py
│   └── urls.py
│
├── llm/                       # LLM abstraction layer
│   ├── client.py              #   LLMClient (OpenAI + Azure + Azure Mistral), embed, chat,
│   │                          #   rate limiting, fallback models, separate embedding endpoint
│   ├── prompts.py             #   All LLM prompt templates
│   └── prompt_loader.py       #   Dynamic prompt loading
│
├── nsg/                       # Semantic graph (concept extraction + knowledge map)
│   ├── concepts.py            #   spaCy-based concept extraction + text chunking
│   ├── graph.py               #   NetworkX semantic graph building
│   ├── index.py               #   FAISS vector index for concept search
│   ├── persistence.py         #   Graph serialization / storage
│   ├── stopwords.py           #   French + English stopwords
│   └── config.py              #   NSG configuration
│
├── reports/                   # Report generation and export
│   ├── models.py              #   Report
│   ├── views.py               #   CSV, JSON, and PDF export endpoints
│   ├── pdf.py                 #   PDF report generation with radar charts (xhtml2pdf)
│   ├── urls.py
│   └── templates/reports/     #   list, pdf_report.html
│
├── tenants/                   # Multi-tenant system
│   ├── models.py              #   Tenant, TenantMembership, TenantScopedModel (abstract)
│   ├── middleware.py           #   TenantMiddleware (resolves current tenant per request)
│   ├── views.py               #   Tenant selection and settings
│   ├── urls.py
│   └── templates/tenants/     #   select, settings
│
├── vectorstore/               # Vector embedding storage
│   └── store.py               #   VectorStore class (sqlite-vec), upsert, search, KNN
│
├── scripts/
│   ├── run_dev.sh             #   Dev setup: venv, deps, migrations, sample data
│   └── load_sample_data.py    #   Creates demo users, tenants, and sample documents
│
├── tests/                     # Test suite (pytest)
│   ├── test_*.py              #   Unit & integration tests for all apps
│   └── nsg/                   #   Semantic graph tests
│
├── docs/                      # Documentation
│   ├── audit.md               #   Audit system documentation
│   └── stack-and-algorithms.md#   Technical stack and algorithm details
│
├── config.yaml                # Analysis, audit, and LLM configuration
├── requirements.txt           # Python dependencies
├── pyproject.toml             # Project metadata, pytest and ruff config
├── Dockerfile                 # Multi-stage Docker build with health check
├── .env.example               # Environment variable template
├── CHANGELOG.md               # Version history
├── CONTRIBUTING.md            # Contribution guidelines
├── SECURITY.md                # Security policy
├── LICENSE                    # Apache 2.0
├── manage.py
├── db.sqlite3                 # Django database (created at runtime)
└── vec.sqlite3                # Vector database (created at runtime)
```

---

## Prerequisites

- **Python 3.12+**
- **Redis** (for production Celery broker) or use the SQLAlchemy database broker for local development
- An **OpenAI API key**, **Azure OpenAI** deployment, or **Azure Mistral** deployment
- **spaCy model** (optional, for semantic graph): `python -m spacy download fr_core_news_sm`

---

## Installation

### Quick Start

```bash
bash scripts/run_dev.sh
```

This script will:
1. Create a `.venv` virtual environment
2. Install all dependencies from `requirements.txt`
3. Copy `.env.example` to `.env` (with database broker for dev)
4. Run Django migrations
5. Load sample data (users, tenants, documents)
6. Collect static files

### Manual Setup

```bash
# Create and activate virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and edit environment config
cp .env.example .env
# Edit .env with your API keys and settings

# Run migrations
python manage.py migrate --run-syncdb

# Load sample data (optional)
python scripts/load_sample_data.py

# Collect static files
python manage.py collectstatic --noinput
```

### Default Credentials (from sample data)

> **WARNING:** These are development-only credentials. Change them immediately in any non-local environment. Never use default passwords in production.

| User  | Password | Role              |
|-------|----------|-------------------|
| admin | admin    | Superuser (admin) |
| demo  | demo     | Editor            |

---

## Docker Deployment

A multi-stage Dockerfile is provided for production deployment.

```bash
# Build the image
docker build -t docuscore .

# Run the container
docker run -p 8000:8000 \
  -e SECRET_KEY=your-production-secret-key \
  -e DEBUG=False \
  -e LLM_PROVIDER=openai \
  -e OPENAI_API_KEY=sk-... \
  docuscore
```

The Docker image:
- Uses a multi-stage build (builder + runtime) for smaller image size
- Runs with **gunicorn** (4 workers, 4 threads)
- Includes a `/healthz/` health check endpoint (30s interval)
- Exposes port 8000

---

## Configuration

DocuScore reads configuration from two sources, with environment variables taking precedence:

### .env (environment variables)

```bash
# Django
# IMPORTANT: Generate a real secret key for production:
#   python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
SECRET_KEY=change-me-in-production
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Celery broker
CELERY_BROKER_BACKEND=database    # "database" for dev, "redis" for production
CELERY_BROKER_URL=redis://localhost:6379/0

# LLM Provider: "openai", "azure", or "azure_mistral"
LLM_PROVIDER=azure_mistral

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_CHAT_MODEL=gpt-4o
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_EMBEDDING_DIMENSIONS=1536

# Azure OpenAI (if LLM_PROVIDER=azure)
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-06-01
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
# Optional: separate Azure endpoint for embeddings (if different resource)
AZURE_OPENAI_EMBEDDING_ENDPOINT=
AZURE_OPENAI_EMBEDDING_API_KEY=

# Azure Mistral (if LLM_PROVIDER=azure_mistral)
AZURE_MISTRAL_ENDPOINT=https://your-resource.services.ai.azure.com
AZURE_MISTRAL_API_KEY=...
AZURE_MISTRAL_API_VERSION=2024-05-01-preview
AZURE_MISTRAL_DEPLOYMENT_NAME=...

# Rate limits
LLM_REQUESTS_PER_MINUTE=60
EMBEDDING_BATCH_SIZE=100
```

> **Security**: In production (`DEBUG=False`), Django will refuse to start if `SECRET_KEY` is set to a placeholder value.

### config.yaml (analysis tuning)

```yaml
llm:
  provider: openai
  chat_model: gpt-4.1
  embedding_model: text-embedding-3-small
  embedding_dimensions: 1536
  requests_per_minute: 500
  fallback_models: [gpt-4o-mini]   # tried in order on 429 errors

analysis:
  duplicate:
    semantic_weight: 0.55
    lexical_weight: 0.25
    metadata_weight: 0.20
    semantic_threshold: 0.92
    combined_threshold: 0.85
  contradiction:
    confidence_threshold: 0.90
    similarity_threshold: 0.90
    max_claims_per_chunk: 2
    staleness_days: 180
  clustering:
    algorithm: hdbscan       # or "kmeans"
    min_cluster_size: 5
    min_samples: 3
  gap_detection:
    coverage_question_count: 2
    confidence_threshold: 0.5
    orphan_cluster_max_size: 2
  hallucination:
    min_acronym_frequency: 2
    jargon_tfidf_threshold: 0.15
    hedging_density_threshold: 0.02
    max_items_per_type: 50

audit:
  axis_weights:
    hygiene: 0.20
    structure: 0.15
    coverage: 0.20
    coherence: 0.15
    retrievability: 0.20
    governance: 0.10

semantic_graph:
  enabled: true
  spacy_model: fr_core_news_sm
  top_k: 5
  max_nodes: 40

authority_rules:
  source_weights:
    sharepoint: 1.0
    confluence: 0.9
    generic: 0.5
  recency_bias: true
```

---

## Running the Application

### 1. Start the Django Development Server

```bash
source .venv/bin/activate
python manage.py runserver
```

Open http://localhost:8000 (redirects to `/dashboard/`).

### 2. Start the Celery Worker

In a separate terminal:

```bash
source .venv/bin/activate
celery -A docuscore worker -l info
```

**For dev mode without Redis** (using SQLAlchemy database broker):

```bash
# Make sure .env has: CELERY_BROKER_BACKEND=database
celery -A docuscore worker -l info -P solo
```

The `-P solo` flag runs a single-threaded worker, required for the database broker.

---

## Task Queue (Celery)

DocuScore uses Celery for all long-running operations. Three main task types exist:

### Ingestion Task (`ingestion.tasks.run_ingestion`)

Triggered when a user clicks **Sync Now** on a connector. Runs the full ingestion pipeline:
fetch documents from source, extract text, chunk, embed, and store vectors.

- Max retries: 2 (with 60s delay)
- Hard time limit: 3600s
- Soft time limit: 3000s

### Analysis Task (`analysis.tasks.run_analysis`)

Triggered from the analysis page. Runs all analysis phases sequentially, with optional audit:

```
AnalysisJob (QUEUED)
     │
     ├─ Phase 1: DuplicateDetector.run()          ┐
     ├─ Phase 2: ClaimsExtractor.extract_all()     ├─ run in parallel
     ├─ Phase 3: Semantic graph building           ┘
     ├─ Phase 4: ContradictionDetector.run()
     ├─ Phase 5: TopicClusterEngine.run()
     ├─ Phase 6: GapDetector.run()
     ├─ Phase 7: HallucinationDetector.run()
     ├─ Phase 8: Tree building
     │
     ├─ (if audit enabled)
     ├─ Audit: Hygiene → Structure → Coverage →
     │         Coherence → Retrievability → Governance
     │
     ▼
AnalysisJob (COMPLETED / FAILED)
```

Supports **resume from a specific phase** if a previous run was interrupted.

### Audit Task (`analysis.tasks.run_audit`)

Can also run as a standalone audit (without the LLM analysis phases). Evaluates 6 axes of RAG quality using statistical methods — no LLM calls required.

### Broker Configuration

| Mode       | Broker                          | Notes                                    |
|------------|---------------------------------|------------------------------------------|
| Production | Redis (`redis://...`)           | Default. Requires a running Redis server |
| Dev        | SQLAlchemy DB (`database`)      | No Redis needed. Use `-P solo` worker    |

Configuration is controlled by `CELERY_BROKER_BACKEND` in `.env`. Result backend always uses Django DB (`django-celery-results`).

---

## Django Apps

| App          | Purpose                                                          |
|--------------|------------------------------------------------------------------|
| `tenants`    | Multi-tenant system: Tenant, Membership, role-based access       |
| `connectors` | Document source connectors with registry pattern                 |
| `ingestion`  | Ingestion pipeline: fetch, extract, chunk, embed, store          |
| `vectorstore`| sqlite-vec vector storage, KNN search, tenant-scoped queries     |
| `analysis`   | Duplicate, contradiction, clustering, gap, hallucination detection + RAG audit |
| `llm`        | Unified LLM client (OpenAI / Azure / Azure Mistral), rate limiting, fallback models |
| `nsg`        | Semantic graph: concept extraction (spaCy), knowledge map (NetworkX + FAISS) |
| `chat`       | RAG chat interface: conversations, document Q&A, configurable system prompt |
| `reports`    | Report generation: CSV, JSON, and PDF export with radar charts   |
| `dashboard`  | Web UI with stats, DocuScore grade, job history, and D3.js visualizations |

---

## Database Schema

### Primary Database (`db.sqlite3`) — Django ORM

**Tenants:**
- `Tenant` — id (UUID), name, slug, max_documents, max_connectors
- `TenantMembership` — tenant, user, role (admin/editor/viewer)

**Connectors:**
- `ConnectorConfig` — tenant, name, connector_type (sharepoint/confluence/generic), config (JSON), credential_ref, schedule_cron, last_sync_at/status

**Ingestion:**
- `Document` — tenant, connector, source_id, title, author, doc_type, content_hash, source_version, version_number, status (PENDING → INGESTED → READY / ERROR / DELETED), word_count, chunk_count
- `DocumentChunk` — tenant, document, chunk_index, content, token_count, heading_path, content_hash, has_embedding
- `IngestionJob` — tenant, connector, status, progress counters, celery_task_id

**Analysis:**
- `AnalysisJob` — tenant, status, phase, progress_pct, includes_audit, config_overrides, celery_task_id
- `DuplicateGroup` — tenant, analysis_job, recommended_action, rationale
- `DuplicatePair` — tenant, group, doc_a, doc_b, semantic/lexical/metadata/combined scores, verification fields
- `Claim` — tenant, document, chunk, subject, predicate, object_value, qualifiers, claim_date, raw_text, has_embedding
- `ContradictionPair` — tenant, analysis_job, claim_a, claim_b, classification, severity, confidence, evidence, resolution, authoritative_claim
- `TopicCluster` — tenant, analysis_job, parent (self-FK), label, summary, key_concepts, content_purpose, level, doc_count, chunk_count, centroid_x/y
- `ClusterMembership` — tenant, cluster, chunk, document, similarity_to_centroid
- `GapReport` — tenant, analysis_job, gap_type, title, description, severity, resolution, related_cluster, coverage_score, evidence (JSON)
- `HallucinationReport` — tenant, analysis_job, risk_type, title, description, severity, resolution, term, expansions, document, doc_count, risk_score, evidence (JSON)
- `TreeNode` — tenant, analysis_job, parent (self-FK), label, node_type (category/cluster/subcluster/document/section), document, cluster, level, sort_order

**Audit:**
- `AuditJob` — tenant, analysis_job (optional FK), status, current_axis, progress_pct, overall_score, overall_grade
- `AuditAxisResult` — audit_job, axis (hygiene/structure/coverage/coherence/retrievability/governance), score, metrics (JSON), chart_data (JSON), details (JSON)

**Pipeline tracing:**
- `PipelineTrace` — analysis_job, total LLM/embed/search calls, token counts, duration
- `PhaseTrace` — pipeline_trace, phase_key, call counts, token counts, items_in/out, duration, status
- `TraceEvent` — phase_trace, event_type, token counts, duration, model_name

**Chat:**
- `ChatConfig` — project, user, system_prompt
- `Conversation` — project, user, title, tools
- `Message` — conversation, role, content

**Reports:**
- `Report` — tenant, analysis_job, type, format, title, summary, data (JSON)

### Vector Database (`vec.sqlite3`) — sqlite-vec

Managed outside Django migrations, created by `VectorStore.ensure_tables()`:

| Table            | Purpose                               |
|------------------|---------------------------------------|
| `vec_chunks`     | vec0 virtual table: chunk_id + float[1536] embedding |
| `vec_metadata`   | Companion table: tenant_id, document_id, doc_type, source_type |
| `vec_claims`     | vec0 virtual table: claim_id + float[1536] embedding |
| `claim_metadata` | Companion table: tenant_id, document_id, chunk_id    |

Tenant isolation is enforced via post-filtering on metadata tables after KNN retrieval.

---

## URL Routes

### Dashboard & Auth

| Path                                    | View                    | Description                      |
|-----------------------------------------|-------------------------|----------------------------------|
| `/`                                     | redirect                | Redirects to `/dashboard/`       |
| `/dashboard/`                           | dashboard.home          | Stats, DocuScore grade, quick links |
| `/healthz/`                             | healthz                 | Health check (for Docker/LB)     |
| `/auth/login/`                          | allauth LoginView       | Login page                       |
| `/auth/logout/`                         | allauth LogoutView      | Logout                           |
| `/admin/`                               | Django Admin            | Admin interface                  |

### Connectors

| Path                                    | View                    | Description                      |
|-----------------------------------------|-------------------------|----------------------------------|
| `/connectors/`                          | connector_list          | List all connectors              |
| `/connectors/create/`                   | connector_create        | Add a new connector              |
| `/connectors/<uuid>/`                   | connector_detail        | Connector details + job history  |
| `/connectors/<uuid>/sync/`             | connector_sync          | Trigger ingestion (POST)         |

### Analysis

| Path                                    | View                    | Description                      |
|-----------------------------------------|-------------------------|----------------------------------|
| `/analysis/`                            | analysis_list           | Analysis job history             |
| `/analysis/run/`                        | analysis_run            | Start new analysis (POST)        |
| `/analysis/<uuid>/`                     | analysis_detail         | Analysis results overview        |
| `/analysis/<uuid>/retry/`              | analysis_retry          | Retry failed analysis            |
| `/analysis/<uuid>/cancel/`             | analysis_cancel         | Cancel running analysis          |
| `/analysis/<uuid>/delete/`             | analysis_delete         | Delete analysis job              |
| `/analysis/<uuid>/duplicates/`          | duplicates_report       | Duplicate pairs with scores      |
| `/analysis/<uuid>/contradictions/`      | contradictions_report   | Contradiction pairs + resolution |
| `/analysis/<uuid>/clusters/`            | clusters_view           | Topic cluster visualization      |
| `/analysis/<uuid>/gaps/`                | gaps_report             | Coverage gap reports + resolution |
| `/analysis/<uuid>/hallucinations/`      | hallucination_report    | Hallucination risk items + resolution |
| `/analysis/<uuid>/tree/`                | tree_view               | Hierarchical document taxonomy   |
| `/analysis/<uuid>/knowledge-map/`       | knowledge_map_view      | Semantic concept graph (D3.js)   |
| `/analysis/<uuid>/trace/`               | trace_view              | Pipeline execution trace         |
| `/analysis/<uuid>/audit/`               | analysis_audit_overview | Audit results overview           |
| `/analysis/<uuid>/api/clusters/`        | clusters_json           | Cluster data (JSON API)          |
| `/analysis/<uuid>/api/tree/`            | tree_json               | Tree data (JSON API)             |
| `/analysis/<uuid>/api/concept-graph/`   | concept_graph_json      | Concept graph data (JSON API)    |

### Audit (standalone)

| Path                                    | View                    | Description                      |
|-----------------------------------------|-------------------------|----------------------------------|
| `/analysis/audit/`                      | audit_list              | Audit job history                |
| `/analysis/audit/run/`                  | audit_run               | Start standalone audit (POST)    |
| `/analysis/audit/<uuid>/`              | audit_detail            | Audit results overview           |
| `/analysis/audit/<uuid>/hygiene/`      | audit_hygiene           | Hygiene axis details             |
| `/analysis/audit/<uuid>/structure/`    | audit_structure         | Structure axis details           |
| `/analysis/audit/<uuid>/coverage/`     | audit_coverage          | Coverage axis details            |
| `/analysis/audit/<uuid>/coherence/`    | audit_coherence         | Coherence axis details           |
| `/analysis/audit/<uuid>/retrievability/`| audit_retrievability   | Retrievability axis details      |
| `/analysis/audit/<uuid>/governance/`   | audit_governance        | Governance axis details          |

### Chat

| Path                                    | View                    | Description                      |
|-----------------------------------------|-------------------------|----------------------------------|
| `/chat/`                                | chat_home               | Chat interface                   |
| `/chat/ask/`                            | chat_ask                | RAG question answering (POST)    |
| `/chat/config/system-prompt/`           | save_system_prompt      | Update chat system prompt        |
| `/chat/conversations/<uuid>/messages/`  | conversation_messages   | Get conversation messages        |
| `/chat/conversations/<uuid>/delete/`    | conversation_delete     | Delete conversation              |

### Reports

| Path                                    | View                       | Description                   |
|-----------------------------------------|----------------------------|-------------------------------|
| `/reports/`                             | report_list                | Report history                |
| `/reports/<uuid>/duplicates.csv`        | export_duplicates_csv      | Duplicates CSV export         |
| `/reports/<uuid>/contradictions.csv`    | export_contradictions_csv  | Contradictions CSV export     |
| `/reports/<uuid>/gaps.csv`              | export_gaps_csv            | Gaps CSV export               |
| `/reports/<uuid>/hallucinations.csv`    | export_hallucinations_csv  | Hallucinations CSV export     |
| `/reports/<uuid>/report.json`           | export_report_json         | Full JSON export              |
| `/reports/<uuid>/report.pdf`            | export_report_pdf          | PDF report with radar charts  |

### Tenants

| Path                                    | View                    | Description                      |
|-----------------------------------------|-------------------------|----------------------------------|
| `/tenants/select/`                      | tenant_select           | Switch active tenant             |
| `/tenants/settings/`                    | tenant_settings         | Tenant name + member management  |

---

## Authentication & Multi-Tenancy

- All views require login (`@login_required`).
- Authentication is handled by **django-allauth**.
- `TenantMiddleware` resolves the active tenant from the user's session on every request.
- All data models inherit from `TenantScopedModel` (or `ProjectScopedModel`), which adds a foreign key to `Tenant` and a custom manager that filters by the current tenant.
- Roles: **admin** (full access + settings), **editor** (create/sync connectors, run analysis), **viewer** (read-only dashboards and reports).
- **Content Security Policy** middleware adds CSP headers to all responses.
- **Production hardening**: HSTS, SSL redirect, secure cookies, full password validators, `SECRET_KEY` validation.

---

## DocuScore Scoring

DocuScore computes a **0-100 quality score** from the latest completed analysis, then maps it to a letter grade (**A** through **E**, Nutri-Score style). Seven dimensions are evaluated:

| Dimension       | Max Penalty | Sources                                      |
|-----------------|-------------|-----------------------------------------------|
| Unicité         | 15          | LLM duplicate groups                          |
| Cohérence       | 15          | LLM contradictions weighted by severity        |
| Couverture      | 20          | LLM gaps (12) + audit coverage (8)            |
| Structure       | 15          | LLM clusters (9) + audit structure (6)        |
| Santé           | 10          | Document pipeline readiness                    |
| Retrievability  | 15          | Audit retrievability (9) + audit hygiene (6)  |
| Gouvernance     | 10          | Audit governance (6) + audit coherence (4)    |

The score starts at 100 and deducts penalties per dimension. The grade mapping: A (80-100), B (60-79), C (40-59), D (20-39), E (0-19).

---

## Tests

```bash
source .venv/bin/activate
pytest tests/
```

Test configuration is in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "docuscore.settings"
```

The test suite covers all major modules: analysis views, audit views, chat, chunking, claims extraction, clustering, connectors, contradictions, dashboard, duplicates, gaps, hashing, LLM client, middleware, models, pipeline integration, reports, scoring, semantic graph (NSG), tenant isolation, tracing, and vector store.

---

## Dependencies

**Core:** Django 5.1, Celery 5.4, sqlite-vec 0.1.6, OpenAI SDK, tiktoken, django-allauth, whitenoise, gunicorn

**ML/Analysis:** scikit-learn, HDBSCAN, datasketch (MinHash), numpy, NLTK, langid, rank-bm25

**Semantic Graph:** spaCy, NetworkX, FAISS (faiss-cpu)

**Document Parsing:** BeautifulSoup4, pypdf, python-docx, python-pptx, markdown

**PDF Export:** xhtml2pdf

**HTTP:** httpx, requests

**Optional Connectors:** msal + office365-rest-python-client (SharePoint), atlassian-python-api (Confluence)

See `requirements.txt` for the full list with version constraints.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines.

---

## License

This project is licensed under the Apache License 2.0 — see [LICENSE](LICENSE) for details.
