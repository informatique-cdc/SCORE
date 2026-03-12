# Technical Reference

Detailed technical documentation for SCORE's internals: task queue, Django apps, database schema, URL routes, authentication, and scoring system.

---

## Table of Contents

- [Task Queue (Celery)](#task-queue-celery)
- [Django Apps](#django-apps)
- [Database Schema](#database-schema)
- [URL Routes](#url-routes)
- [Authentication & Multi-Tenancy](#authentication--multi-tenancy)
- [SCORE Scoring](#score-scoring)

---

## Task Queue (Celery)

SCORE uses Celery for all long-running operations. Three main task types exist:

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
     ├─ Phase 2: ClaimsExtractor.extract_all()     ┘ run in parallel
     ├─ Phase 3: Semantic graph building (optional)
     ├─ Phase 4: TopicClusterEngine.run()
     ├─ Phase 5: GapDetector.run()
     ├─ Phase 6: Tree building
     ├─ Phase 7: ContradictionDetector.run()
     ├─ Phase 8: HallucinationDetector.run()
     │
     ├─ (if audit enabled)
     ├─ Audit: 6 axes run in parallel ──────────┐
     │   Hygiene, Structure, Coverage,           │
     │   Coherence, Retrievability, Governance   │
     │                                           ┘
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
| `score`      | Django project root: settings, scoring engine, CSP middleware, health check, rate limiting, utilities |
| `tenants`    | Multi-tenant system: Tenant, Membership, role-based access       |
| `connectors` | Document source connectors with registry pattern                 |
| `ingestion`  | Ingestion pipeline: fetch, extract, chunk, embed, store          |
| `vectorstore`| sqlite-vec vector storage, KNN search, tenant-scoped queries     |
| `analysis`   | Duplicate, contradiction, clustering, gap, hallucination detection + RAG audit |
| `llm`        | Unified LLM client (OpenAI / Azure / Azure Mistral), rate limiting, fallback models |
| `nsg`        | Semantic graph: concept extraction (spaCy), knowledge map (NetworkX + FAISS) |
| `chat`       | RAG chat interface: conversations, document Q&A, configurable system prompt |
| `reports`    | Report generation: CSV, JSON, and PDF export with radar charts   |
| `dashboard`  | Web UI with stats, SCORE grade, job history, and D3.js visualizations |

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

| Path                                    | View                        | Description                      |
|-----------------------------------------|-----------------------------|----------------------------------|
| `/`                                     | redirect                    | Redirects to `/dashboard/`       |
| `/dashboard/`                           | home                        | Stats, SCORE grade, quick links  |
| `/dashboard/_stats/`                    | stats_partial               | HTMX partial: dashboard stats    |
| `/dashboard/_latest-analysis/`          | latest_analysis_partial     | HTMX partial: latest analysis    |
| `/dashboard/_recent-jobs/`              | recent_jobs_partial         | HTMX partial: recent jobs        |
| `/dashboard/_score-detail/`             | score_detail_json           | Score breakdown (JSON API)       |
| `/dashboard/feedback/`                  | submit_feedback             | Submit user feedback (POST)      |
| `/healthz/`                             | healthz                     | Health check (for Docker/LB)     |
| `/auth/login/`                          | allauth LoginView           | Login page                       |
| `/auth/logout/`                         | allauth LogoutView          | Logout                           |
| `/admin/`                               | Django Admin                | Admin interface                  |

### Connectors

| Path                                              | View                          | Description                          |
|----------------------------------------------------|-------------------------------|--------------------------------------|
| `/connectors/`                                     | connector_list                | List all connectors                  |
| `/connectors/create/`                              | connector_create              | Add a new connector                  |
| `/connectors/_cards/`                              | connector_cards_partial       | HTMX partial: connector cards        |
| `/connectors/<uuid>/`                              | connector_detail              | Connector details + job history      |
| `/connectors/<uuid>/sync/`                         | connector_sync                | Trigger ingestion (POST)             |
| `/connectors/<uuid>/delete/`                       | connector_delete              | Delete connector (POST)              |
| `/connectors/<uuid>/_jobs/`                        | connector_jobs_partial        | HTMX partial: job list               |
| `/connectors/<uuid>/_live/`                        | connector_detail_live_partial | HTMX partial: live connector status  |
| `/connectors/<uuid>/documents/<uuid>/content/`     | document_content              | View document content                |
| `/connectors/<uuid>/documents/<uuid>/file/`        | document_file                 | Download original document file      |

### Analysis

| Path                                               | View                        | Description                            |
|----------------------------------------------------|-----------------------------|----------------------------------------|
| `/analysis/`                                       | analysis_list               | Analysis job history                   |
| `/analysis/run/`                                   | analysis_run                | Start new analysis (POST)              |
| `/analysis/_jobs/`                                 | analysis_jobs_partial       | HTMX partial: jobs list                |
| `/analysis/<uuid>/`                                | analysis_detail             | Analysis results overview              |
| `/analysis/<uuid>/retry/`                          | analysis_retry              | Retry failed analysis                  |
| `/analysis/<uuid>/cancel/`                         | analysis_cancel             | Cancel running analysis                |
| `/analysis/<uuid>/delete/`                         | analysis_delete             | Delete analysis job                    |
| `/analysis/<uuid>/duplicates/`                     | duplicates_report           | Duplicate pairs with scores            |
| `/analysis/<uuid>/contradictions/`                 | contradictions_report       | Contradiction pairs + resolution       |
| `/analysis/<uuid>/contradictions/<uuid>/resolve/`  | contradiction_resolve       | Resolve single contradiction (POST)    |
| `/analysis/<uuid>/contradictions/batch-resolve/`   | contradiction_batch_resolve | Batch-resolve contradictions (POST)    |
| `/analysis/<uuid>/clusters/`                       | clusters_view               | Topic cluster visualization            |
| `/analysis/<uuid>/gaps/`                           | gaps_report                 | Coverage gap reports + resolution      |
| `/analysis/<uuid>/gaps/<uuid>/resolve/`            | gap_resolve                 | Resolve single gap (POST)              |
| `/analysis/<uuid>/gaps/batch-resolve/`             | gap_batch_resolve           | Batch-resolve gaps (POST)              |
| `/analysis/<uuid>/hallucinations/`                 | hallucination_report        | Hallucination risk items + resolution  |
| `/analysis/<uuid>/hallucinations/<uuid>/resolve/`  | hallucination_resolve       | Resolve single hallucination (POST)    |
| `/analysis/<uuid>/hallucinations/batch-resolve/`   | hallucination_batch_resolve | Batch-resolve hallucinations (POST)    |
| `/analysis/<uuid>/tree/`                           | tree_view                   | Hierarchical document taxonomy         |
| `/analysis/<uuid>/knowledge-map/`                  | knowledge_map_view          | Semantic concept graph (D3.js)         |
| `/analysis/<uuid>/trace/`                          | trace_view                  | Pipeline execution trace               |
| `/analysis/<uuid>/audit/`                          | analysis_audit_overview     | Audit results overview                 |
| `/analysis/<uuid>/_progress/`                      | analysis_progress_partial   | HTMX partial: progress bar             |
| `/analysis/<uuid>/_progress_full/`                 | analysis_progress_full_partial | HTMX partial: full progress         |
| `/analysis/<uuid>/_results/`                       | analysis_results_partial    | HTMX partial: results summary          |
| `/analysis/<uuid>/api/clusters/`                   | clusters_json               | Cluster data (JSON API)                |
| `/analysis/<uuid>/api/tree/`                       | tree_json                   | Tree data (JSON API)                   |
| `/analysis/<uuid>/api/concept-graph/`              | concept_graph_json          | Concept graph data (JSON API)          |
| `/analysis/<uuid>/api/concept-graph/query/`        | concept_graph_query         | Concept graph query (JSON API)         |

### Audit (standalone)

| Path                                         | View                  | Description                      |
|----------------------------------------------|-----------------------|----------------------------------|
| `/analysis/audit/`                           | audit_list            | Audit job history                |
| `/analysis/audit/run/`                       | audit_run             | Start standalone audit (POST)    |
| `/analysis/audit/<uuid>/`                    | audit_detail          | Audit results overview           |
| `/analysis/audit/<uuid>/retry/`              | audit_retry           | Retry failed audit               |
| `/analysis/audit/<uuid>/delete/`             | audit_delete          | Delete audit job                 |
| `/analysis/audit/<uuid>/hygiene/`            | audit_hygiene         | Hygiene axis details             |
| `/analysis/audit/<uuid>/structure/`          | audit_structure       | Structure axis details           |
| `/analysis/audit/<uuid>/coverage/`           | audit_coverage        | Coverage axis details            |
| `/analysis/audit/<uuid>/coherence/`          | audit_coherence       | Coherence axis details           |
| `/analysis/audit/<uuid>/retrievability/`     | audit_retrievability  | Retrievability axis details      |
| `/analysis/audit/<uuid>/governance/`         | audit_governance      | Governance axis details          |
| `/analysis/audit/<uuid>/_progress/`          | audit_progress_partial| HTMX partial: audit progress     |
| `/analysis/audit/<uuid>/api/<axis>/`         | api_audit_axis        | Per-axis data (JSON API)         |

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

| Path                                    | View                    | Description                        |
|-----------------------------------------|-------------------------|------------------------------------|
| `/tenants/select/`                      | tenant_select           | Switch active tenant               |
| `/tenants/create/`                      | tenant_create           | Create new tenant                  |
| `/tenants/settings/`                    | settings_page           | Tenant name + member management    |
| `/tenants/projects/`                    | project_list            | List tenant projects               |
| `/tenants/projects/create/`             | project_create          | Create new project                 |
| `/tenants/projects/<uuid>/delete/`      | project_delete          | Delete a project (POST)            |
| `/tenants/users/invite/`               | user_invite             | Invite user to tenant (POST)       |
| `/tenants/users/<uuid>/role/`          | user_role_update        | Update user role (POST)            |
| `/tenants/users/<uuid>/remove/`        | user_remove             | Remove user from tenant (POST)     |

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

## SCORE Scoring

SCORE computes a **0-100 quality score** from the latest completed analysis, then maps it to a letter grade (**A** through **E**, Nutri-Score style). Seven dimensions are evaluated:

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

For the complete scoring formula, edge cases, and examples, see [SCORE_FORMULA.md](SCORE_FORMULA.md).
