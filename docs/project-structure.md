# Project Structure

```
score/
├── analysis/                  # Document analysis (duplicates, contradictions, gaps, clustering, hallucination)
│   ├── models.py              #   AnalysisJob, AuditJob, AuditAxisResult, DuplicateGroup,
│   │                          #   DuplicatePair, Claim, ContradictionPair, TopicCluster,
│   │                          #   ClusterMembership, GapReport, HallucinationReport,
│   │                          #   TreeNode, PipelineTrace, PhaseTrace, TraceEvent
│   ├── duplicates.py          #   Multi-signal duplicate detection + LLM verification
│   ├── claims.py              #   Claims extraction from document chunks
│   ├── contradictions.py      #   Claims-based contradiction & staleness detection
│   ├── clustering.py          #   HDBSCAN/KMeans topic clustering + tree building
│   ├── gaps.py                #   QG/RAG coverage, orphan, stale, and adjacent gap detection
│   ├── hallucination.py       #   RAG hallucination risk detection (acronyms, jargon, hedging)
│   ├── semantic_graph.py      #   Project-level semantic graph builder
│   ├── constants.py           #   Shared analysis constants
│   ├── pipeline.py            #   Pipeline orchestration (analysis + audit phases)
│   ├── presenters.py          #   Data presenters for views
│   ├── trace.py               #   Pipeline tracing helpers (PhaseEventBuffer)
│   ├── tasks.py               #   Celery tasks: run_analysis, run_audit
│   ├── audit/                 #   RAG quality audit (6 axes, no LLM)
│   │   ├── base.py            #     BaseAuditAxis ABC
│   │   ├── runner.py          #     Audit runner + AXIS_ORDER registry
│   │   ├── hygiene.py         #     Corpus hygiene: near-duplicates, boilerplate, language mix
│   │   ├── structure_rag.py   #     RAG structure: chunk size, info density, readability
│   │   ├── coverage.py        #     Semantic coverage: topic diversity, outliers
│   │   ├── coherence.py       #     Internal coherence: terminology consistency
│   │   ├── retrievability.py  #     Retrievability: embedding quality, search relevance
│   │   └── governance.py      #     Governance: metadata, ownership, freshness
│   ├── views.py               #   Analysis list, detail, sub-reports, resolve/batch-resolve
│   ├── views_audit.py         #   Audit list, detail, per-axis reports
│   ├── views_json.py          #   JSON API endpoints for D3.js visualizations
│   ├── views_reports.py       #   Report-specific view helpers
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
│   ├── elasticsearch.py       #   Elasticsearch connector (optional dep)
│   ├── views.py               #   CRUD + sync trigger + document content/file endpoints
│   ├── urls.py
│   └── templates/connectors/  #   list, create, detail
│
├── dashboard/                 # Main web UI
│   ├── models.py              #   Dashboard-specific models
│   ├── admin.py               #   Admin registration
│   ├── views.py               #   Home view with stats, scoring, and recent jobs
│   ├── scoring.py             #   Re-exports from score.scoring (backward compat)
│   ├── urls.py
│   └── templates/dashboard/   #   base.html (Bootstrap 5 + D3.js layout), home.html, login.html
│
├── score/                     # Django project root
│   ├── settings.py            #   Settings (reads .env + config.yaml), security hardening
│   ├── urls.py                #   Root URL router
│   ├── celery.py              #   Celery app configuration
│   ├── scoring.py             #   SCORE 0-100 scoring with A-E grades (7 dimensions)
│   ├── utils.py               #   Shared utilities (JSON parsing, etc.)
│   ├── middleware.py           #   Content Security Policy middleware
│   ├── health.py              #   Health check endpoint
│   ├── issues.py              #   Issue tracking helpers
│   ├── ratelimit.py           #   Rate-limiting utilities
│   ├── asgi.py                #   ASGI entry point
│   └── wsgi.py                #   WSGI entry point
│
├── ingestion/                 # Document ingestion pipeline
│   ├── models.py              #   Document, DocumentChunk, IngestionJob
│   ├── pipeline.py            #   IngestionPipeline orchestrator
│   ├── extraction.py          #   Text extraction (HTML, PDF, DOCX, PPTX, Markdown)
│   ├── chunking.py            #   Heading-aware and fixed-token chunking
│   ├── hashing.py             #   Content normalization + SHA-256 hashing
│   └── tasks.py               #   Celery task: run_ingestion
│
├── llm/                       # LLM abstraction layer
│   ├── client.py              #   LLMClient (OpenAI + Azure + Azure Mistral), embed, chat,
│   │                          #   rate limiting, fallback models, separate embedding endpoint
│   ├── prompts.py             #   LLM prompt templates (French)
│   ├── prompts_en.py          #   LLM prompt templates (English)
│   ├── prompts_rag.py         #   RAG-specific prompts (French)
│   ├── prompts_rag_en.py      #   RAG-specific prompts (English)
│   └── prompt_loader.py       #   Dynamic prompt loading
│
├── nsg/                       # Semantic graph (concept extraction + knowledge map)
│   ├── cli.py                 #   CLI entry point for standalone graph operations
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
│   ├── adapters.py            #   allauth account adapter (tenant-aware)
│   ├── context_processors.py  #   Template context: current tenant, projects
│   ├── middleware.py           #   TenantMiddleware (resolves current tenant per request)
│   ├── views.py               #   Tenant selection, settings, projects, user management
│   ├── urls.py
│   ├── templatetags/          #   Custom template tags
│   │   └── tenant_tags.py     #     Tenant-related template filters
│   └── templates/tenants/     #   select, settings
│
├── vectorstore/               # Vector embedding storage
│   ├── models.py              #   Vectorstore Django models
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
│   ├── SCORE_FORMULA.md       #   Scoring formula, 7 dimensions, edge cases
│   ├── INGESTION_AND_ANALYSIS.md  # Ingestion pipeline + analysis methods
│   ├── stack-and-algorithms.md    # Technical stack and algorithms (French)
│   ├── deployment.md          #   Production deployment guide (Docker, PostgreSQL, nginx)
│   ├── project-structure.md   #   This file
│   ├── configuration.md       #   config.yaml reference
│   └── technical-reference.md #   Celery, Django apps, DB schema, routes, auth, scoring
│
├── data/                      # Runtime data (created at runtime, gitignored)
│   ├── db.sqlite3             #   Django database
│   └── vec.sqlite3            #   Vector database
│
├── locale/                    # Internationalization
│   ├── en/                    #   English translations
│   └── fr/                    #   French translations
│
├── config.yaml                # Analysis, audit, and LLM configuration
├── requirements.txt           # Python dependencies
├── pyproject.toml             # Project metadata, pytest and ruff config
├── Dockerfile                 # Multi-stage Docker build with health check
├── docker-compose.yml         # Docker Compose for full-stack deployment
├── .env.example               # Environment variable template
├── .dockerignore              # Docker build exclusions
├── .gitignore                 # Git exclusions
├── CHANGELOG.md               # Version history
├── CONTRIBUTING.md            # Contribution guidelines
├── SECURITY.md                # Security policy
├── LICENSE                    # Apache 2.0
└── manage.py
```
