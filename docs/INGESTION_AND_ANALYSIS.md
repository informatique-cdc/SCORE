# Ingestion Pipeline & Semantic Analysis

Complete technical reference for SCORE's document ingestion pipeline and all analysis methods.

---

## Table of Contents

- [Ingestion Pipeline Overview](#ingestion-pipeline-overview)
- [Pipeline Stages](#pipeline-stages)
  - [1. Change Detection](#1-change-detection)
  - [2. Document Fetching (Connectors)](#2-document-fetching-connectors)
  - [3. Text Extraction](#3-text-extraction)
  - [4. Content Hashing](#4-content-hashing)
  - [5. Chunking](#5-chunking)
  - [6. Embedding](#6-embedding)
  - [7. Vector Storage](#7-vector-storage)
- [Analysis Overview](#analysis-overview)
- [Analysis Methods](#analysis-methods)
  - [1. Duplicate Detection](#1-duplicate-detection)
  - [2. Claims Extraction](#2-claims-extraction)
  - [3. Semantic Graph Construction](#3-semantic-graph-construction)
  - [4. Topic Clustering](#4-topic-clustering)
  - [5. Gap Detection](#5-gap-detection)
  - [6. Contradiction Detection](#6-contradiction-detection)
- [Vector Store Internals](#vector-store-internals)
- [Chat RAG Pipeline](#chat-rag-pipeline)
- [LLM Client](#llm-client)
- [Configuration Reference](#configuration-reference)

---

## Ingestion Pipeline Overview

The ingestion pipeline transforms raw documents from external sources into chunked, embedded, and searchable content. It is implemented in `ingestion/pipeline.py` as the `IngestionPipeline` class and executed asynchronously via the Celery task `run_ingestion`.

```
  Source (filesystem, HTTP, SharePoint, Confluence, Elasticsearch)
    │
    ▼
  ┌──────────────────────────────────────────────────┐
  │ 1. Change Detection                              │
  │    Compare source versions against stored versions│
  │    → new_or_changed[], deleted_ids[]             │
  └──────────────┬───────────────────────────────────┘
                 │
    ┌────────────┴────────────┐
    ▼                         ▼
  Deletions               For each new/changed doc:
  (mark DELETED,          ┌─────────────────────────┐
   remove vectors)        │ 2. Fetch (connector)    │
                          │ 3. Extract text          │
                          │ 4. Hash content          │
                          │    (skip if unchanged)   │
                          │ 5. Chunk document        │
                          │ 6. Embed chunks (LLM)   │
                          │ 7. Store vectors         │
                          └─────────────────────────┘
                                     │
                                     ▼
                          Document status: READY
```

### Celery Task: `run_ingestion`

**Location:** `ingestion/tasks.py`

```python
@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def run_ingestion(self, job_id: str)
```

- Fetches the `IngestionJob` by ID
- Stores the Celery task ID on the job for tracking
- Instantiates `IngestionPipeline(job)` and calls `pipeline.run()`
- On failure: retries up to 2 times with 60s delay between attempts

### Pipeline Statistics

The pipeline tracks counts throughout execution:

| Counter    | Meaning                                      |
|------------|----------------------------------------------|
| `new`      | Documents ingested for the first time        |
| `updated`  | Documents re-ingested due to content changes |
| `deleted`  | Documents removed from source                |
| `unchanged`| Documents skipped (same content hash)        |
| `errors`   | Documents that failed processing             |

These are persisted on the `IngestionJob` record at completion.

---

## Pipeline Stages

### 1. Change Detection

**Location:** `ingestion/pipeline.py` — `run()` method, lines 55-68

The pipeline performs incremental sync by comparing document versions:

1. Query all existing `Document` records for the current tenant + connector
2. Build a dict: `{source_id: source_version}` for known documents
3. Call `connector.list_changed_documents(known_versions)` which returns:
   - `new_or_changed`: documents where `source_version` differs or `source_id` is new
   - `deleted_ids`: `source_id`s present in DB but absent from source

This avoids re-processing unchanged documents entirely.

### 2. Document Fetching (Connectors)

**Location:** `connectors/base.py`, `connectors/generic.py`

#### Connector Interface

All connectors implement `BaseConnector`:

```python
class BaseConnector(ABC):
    def __init__(self, config: dict, credential: str = ""): ...
    def test_connection(self) -> bool: ...
    def list_documents(self) -> list[dict]: ...
    def fetch_document(self, source_id: str) -> RawDocument: ...
    def list_changed_documents(self, known_versions) -> tuple[list[dict], list[str]]: ...
```

Connectors are registered via the `@register_connector("name")` decorator and instantiated at runtime by `get_connector(connector_type, config, credential)`.

#### Credential Handling

The `credential` parameter passed to connectors is the **decrypted secret value**, resolved by `ConnectorConfig.get_secret()` at pipeline init time. The resolution order is:

1. **Encrypted secret** — if `encrypted_secret` is set on the model, it is decrypted using the per-tenant Fernet key (derived via HKDF from `FIELD_ENCRYPTION_KEY` + tenant UUID)
2. **Environment variable fallback** — if no encrypted secret exists, `credential_ref` is treated as an env var name and looked up via `os.environ.get()`
3. **Empty string** — if neither is available

This means connectors receive the actual secret directly and do not need to perform env var lookups themselves. See `connectors/crypto.py` for the encryption implementation.

#### `RawDocument` Dataclass

The output of `fetch_document()`:

```python
@dataclass
class RawDocument:
    source_id: str              # Unique ID in the source system
    title: str
    content: bytes | str        # Raw bytes (binary) or string (text/HTML)
    content_type: str           # MIME type or file extension
    source_url: str = ""
    author: str = ""
    path: str = ""
    doc_type: str = ""
    source_version: str = ""
    source_created_at: datetime | None = None
    source_modified_at: datetime | None = None
    metadata: dict = {}
```

#### Generic Connector (filesystem + HTTP)

**Filesystem mode** (`source_type: "filesystem"`):
- Walks a directory tree recursively (configurable)
- Filters by supported extensions: `.txt`, `.md`, `.html`, `.pdf`, `.docx`, `.pptx`, `.csv`, `.json`, `.xml`, `.rst`, `.yaml`, `.yml`
- Version = `"{mtime:.0f}-{size}"` — detects content changes via timestamp and size

**HTTP mode** (`source_type: "http"`):
- Fetches a single URL via `httpx`
- Version = `ETag` header, or SHA-256 of response content (first 16 chars)

#### Available Connectors

| Type            | Module                        | Dependencies              |
|-----------------|-------------------------------|---------------------------|
| `generic`       | `connectors/generic.py`       | (built-in)                |
| `sharepoint`    | `connectors/sharepoint.py`    | msal, office365-rest-python-client |
| `confluence`    | `connectors/confluence.py`    | atlassian-python-api      |
| `elasticsearch` | `connectors/elasticsearch.py` | elasticsearch>=8.0        |

#### Elasticsearch Connector

**Module:** `connectors/elasticsearch.py`

Connects to an Elasticsearch cluster and retrieves documents from a specified index.

**Authentication methods:**
- `api_key` (default) — Elasticsearch API key
- `basic_auth` — username + password tuple
- `bearer_token` — OAuth2 / service account bearer token
- Elastic Cloud via `cloud_id` (compatible with any auth method)

**Pagination:** Uses Point in Time (PIT) + `search_after` for memory-efficient, consistent reads. Falls back to `helpers.scan()` (scroll API) for older Elasticsearch versions that don't support PIT.

**Config keys:**

| Key              | Default       | Description                                         |
|------------------|---------------|-----------------------------------------------------|
| `hosts`          | (required\*)  | Elasticsearch URL(s), comma-separated               |
| `cloud_id`       | —             | Elastic Cloud deployment ID (alternative to `hosts`) |
| `index`          | (required)    | Index name or pattern                               |
| `auth_method`    | `api_key`     | `api_key`, `basic_auth`, or `bearer_token`          |
| `username`       | —             | Username for `basic_auth`                           |
| `verify_certs`   | `true`        | Whether to verify TLS certificates                  |
| `ca_certs`       | —             | Path to CA bundle for TLS                           |
| `content_field`  | `content`     | Document field containing the main text             |
| `title_field`    | `title`       | Document field containing the title                 |
| `author_field`   | `author`      | Document field containing the author                |
| `date_field`     | `updated_at`  | Document field containing the modification date     |
| `query`          | `match_all`   | Elasticsearch Query DSL (JSON) to filter documents  |
| `batch_size`     | `500`         | Documents per search_after page                     |

\* Either `hosts` or `cloud_id` is required.

**Install:** `pip install elasticsearch>=8.0` or `pip install score[elasticsearch]`

---

### 3. Text Extraction

**Location:** `ingestion/extraction.py`

Converts raw document content into plain text with structural heading information.

#### Output: `ExtractedText`

```python
@dataclass
class ExtractedText:
    text: str                   # Full extracted plain text
    headings: list[dict]        # [{"level": 1, "text": "Title", "offset": 0}, ...]
    word_count: int = 0
    metadata: dict = {}
```

The `headings` list captures the document's hierarchical structure. Each heading records its level (1-6), text, and character offset in the extracted text. This structure is used by the heading-aware chunker to preserve document context.

#### Dispatcher: `extract_text(content, content_type)`

Routes to format-specific extractors based on MIME type:

| Content Type                          | Extractor          | Library         |
|---------------------------------------|--------------------|-----------------|
| `text/html` or `*html*`              | `_extract_html()`  | BeautifulSoup4  |
| `application/pdf` or `*pdf*`         | `_extract_pdf()`   | pypdf           |
| `*wordprocessingml*` or `*docx*`     | `_extract_docx()`  | python-docx     |
| `*presentationml*` or `*pptx*`       | `_extract_pptx()`  | python-pptx     |
| `text/markdown` or `*markdown*`      | `_extract_markdown()` | regex        |
| (default)                             | Plain text passthrough | —           |

#### Format-Specific Details

**HTML:**
1. Parse with BeautifulSoup (`html.parser`)
2. Remove non-content elements: `<script>`, `<style>`, `<nav>`, `<footer>`
3. Extract `<h1>`–`<h6>` tags as headings with character offsets
4. Collapse excessive whitespace (`\n{3,}` → `\n\n`)

**PDF:**
1. Read with `pypdf.PdfReader`
2. Extract text page-by-page, join with `\n\n`
3. Attempt heading extraction from PDF outline/bookmarks

**DOCX:**
1. Read with `python-docx`
2. Iterate paragraphs; detect headings via `para.style.name` (e.g., `"Heading 2"` → level 2)
3. Preserve heading hierarchy

**PPTX:**
1. Read with `python-pptx`
2. Each slide title becomes an H2 heading
3. Extract text from all shapes with text frames

**Markdown:**
1. Line-by-line regex: `^(#{1,6})\s+(.+)$`
2. Number of `#` characters determines heading level
3. Original markdown text is preserved as-is

**Error handling:** All extractors catch exceptions and return an empty `ExtractedText` with the error stored in `metadata["extraction_error"]`.

---

### 4. Content Hashing

**Location:** `ingestion/hashing.py`

Used for two purposes:
1. **Incremental ingestion** — skip documents whose content hasn't changed
2. **Chunk identity** — detect identical chunks across re-ingestions

#### Algorithm

```
normalize(text) → SHA-256 hex digest (64 chars)
```

**Normalization steps:**
1. Lowercase the entire text
2. Collapse all whitespace sequences (`\s+`) to a single space
3. Strip leading/trailing whitespace

This prevents false negatives from formatting differences (extra newlines, tabs, trailing spaces) while still detecting actual content changes.

#### Functions

| Function          | Input    | Output                | Purpose                     |
|-------------------|----------|-----------------------|-----------------------------|
| `hash_content()`  | str      | 64-char hex SHA-256   | Document-level change detection |
| `hash_chunk()`    | str      | 64-char hex SHA-256   | Chunk-level identity        |

---

### 5. Chunking

**Location:** `ingestion/chunking.py`

Splits extracted text into overlapping chunks suitable for embedding. Two strategies are available.

#### Output: `Chunk` Dataclass

```python
@dataclass
class Chunk:
    index: int              # Position in document (0-based)
    content: str            # Chunk text
    token_count: int        # Token count (tiktoken cl100k_base)
    heading_path: str       # e.g., "Chapter 1 > Section 2 > Subsection A"
    content_hash: str       # SHA-256 of chunk content
```

#### Parameters

| Parameter       | Default          | Description                                |
|-----------------|------------------|--------------------------------------------|
| `strategy`      | `heading_aware`  | Chunking strategy                          |
| `chunk_size`    | 512              | Target tokens per chunk                    |
| `chunk_overlap` | 64               | Overlap tokens between consecutive chunks  |
| `min_chunk_size`| 50               | Minimum tokens to keep a chunk             |

#### Tokenizer

Uses `tiktoken` with the `cl100k_base` encoding (same tokenizer as GPT-4 and text-embedding-3). The encoder is cached as a module-level singleton.

#### Strategy 1: Heading-Aware Chunking (default)

Preserves document structure by splitting on headings first, then subdividing large sections.

**Step 1 — Split by headings** (`_split_by_headings()`):
- Sort headings by character offset
- Maintain a heading hierarchy stack (trimmed when a same-or-higher level heading appears)
- Build heading path string: `"Chapter 1 > Section 2 > Subsection A"`
- Extract section text between heading offsets
- Include preamble text before the first heading (if any)

**Step 2 — Subdivide large sections** (`_split_tokens()`):
- If section token count <= `chunk_size`: keep as a single chunk (if >= `min_chunk_size`)
- If section token count > `chunk_size`: apply sliding window
  ```
  step = chunk_size - chunk_overlap   (e.g., 512 - 64 = 448)
  windows: [0, 512), [448, 960), [896, 1408), ...
  ```
- Each window is decoded back to text via `enc.decode()`
- Chunks below `min_chunk_size` tokens are discarded

**Result:** Each chunk inherits the full heading path from its parent section, providing hierarchical context for retrieval.

#### Strategy 2: Token-Fixed Chunking

Simple sliding window over the entire document with no heading awareness.

1. Tokenize the full text
2. Apply the same `_split_tokens()` sliding window
3. All chunks have `heading_path = ""`

#### Example

For a 1500-token document section under `"API Reference > Rate Limits"` with default settings:

```
Chunk 0: tokens [0, 512)    heading_path="API Reference > Rate Limits"
Chunk 1: tokens [448, 960)  heading_path="API Reference > Rate Limits"
Chunk 2: tokens [896, 1408) heading_path="API Reference > Rate Limits"
Chunk 3: tokens [1344, 1500) heading_path="API Reference > Rate Limits"
```

---

### 6. Embedding

**Location:** `llm/client.py` — `LLMClient.embed()`

Chunks are embedded in batches via the configured LLM provider.

**Process:**
1. Collect all chunk texts from the document
2. Call `llm.embed(texts)` which auto-batches in groups of `embedding_batch_size` (default: 100)
3. Each text returns a float vector of `embedding_dimensions` dimensions (default: 1536)
4. Rate limiting: enforces `requests_per_minute` (default: 60) across all API calls

**Models used:**
- OpenAI: `text-embedding-3-small` (default) — 1536 dimensions
- Azure: Configured deployment name

---

### 7. Vector Storage

**Location:** `vectorstore/store.py`

After embedding, chunk vectors are stored in the sqlite-vec database.

**Process:**
1. For each chunk, prepare a tuple: `(chunk_id, tenant_id, vector, metadata)`
2. Call `vec_store.upsert_batch(items)` which:
   - Inserts/replaces into `vec_metadata` (tenant_id, document_id, doc_type, source_type)
   - Inserts/replaces into `vec_chunks` (chunk_id, serialized float32 embedding)
3. Mark chunks as `has_embedding = True`
4. Set document status to `READY`

**For document updates:** Old vectors are deleted first via `vec_store.delete_by_document(doc_id)`, then new vectors are inserted.

**Serialization:** Vectors are packed as raw `float32` arrays:
```python
struct.pack(f"{len(vector)}f", *vector)   # list[float] → bytes
```

---

## Analysis Overview

Analysis runs as a single Celery task (`analysis.tasks.run_unified_pipeline`) that executes seven phases sequentially:

```
AnalysisJob
  │
  ├─ Phase 1: Duplicate Detection        (analysis/duplicates.py)
  │            Multi-signal similarity + LLM verification
  │
  ├─ Phase 2: Claims Extraction           (analysis/claims.py)
  │            Extract atomic factual claims from chunks
  │
  ├─ Phase 3: Semantic Graph Construction (analysis/semantic_graph.py)  [optional]
  │            Build concept-level knowledge graph via NSG library
  │
  ├─ Phase 4: Topic Clustering            (analysis/clustering.py)
  │            HDBSCAN/KMeans on embeddings + LLM summaries
  │
  ├─ Phase 5: Gap Detection               (analysis/gaps.py)
  │            QG/RAG + orphan + stale + adjacent + structural graph analysis
  │
  ├─ Phase 6: Tree                        (inline in clustering)
  │            Hierarchical document taxonomy
  │
  └─ Phase 7: Contradiction Detection     (analysis/contradictions.py)
               Vector-search related claims + LLM classification
```

Each phase updates `AnalysisJob.current_phase` and `progress_pct` for real-time tracking in the dashboard.

Phase 3 (Semantic Graph) is optional and controlled by `semantic_graph.enabled` in `config.yaml`. When enabled, the resulting graph is passed to Phase 5 (Gap Detection) for structural gap analysis, and is also used by the chat RAG pipeline for graph-augmented retrieval.

---

## Analysis Methods

### 1. Duplicate Detection

**Location:** `analysis/duplicates.py` — `DuplicateDetector`

Finds duplicate and near-duplicate documents using a three-signal weighted scoring system with optional LLM verification.

#### Algorithm

```
                    ┌─────────────────────────┐
                    │ Compute doc embeddings   │
                    │ (mean of chunk vectors)  │
                    └────────────┬────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │ Build MinHash index      │
                    │ (3-word shingles,        │
                    │  128 permutations)       │
                    └────────────┬────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │ Find semantic candidates │
                    │ (cosine ≥ threshold×0.7) │
                    └────────────┬────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │ Score each candidate     │
                    │ pair (3 signals)         │
                    └────────────┬────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │ Group by connected       │
                    │ components (BFS)         │
                    └────────────┬────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │ LLM verification         │
                    │ (high-scoring pairs)     │
                    └─────────────────────────┘
```

#### Step 1: Document-Level Embeddings

For each document, retrieve all chunk embeddings from sqlite-vec and compute:

```
doc_embedding = normalize(mean(chunk_embeddings))
```

L2 normalization enables cosine similarity via simple dot product.

#### Step 2: MinHash Index (Lexical Similarity)

Uses the `datasketch` library for fast approximate Jaccard similarity:

1. For each document, concatenate all chunk text
2. Tokenize (case-insensitive) and generate 3-word shingles
3. Build a `MinHash(num_perm=128)` signature per document

This enables O(1) Jaccard similarity estimation between any two documents.

#### Step 3: Candidate Selection

Compute pairwise cosine similarity on document embeddings. Keep pairs where:

```
cosine_similarity(doc_a, doc_b) >= combined_threshold * 0.7
```

The lower threshold (default: `0.80 * 0.7 = 0.56`) is intentionally permissive to avoid missing candidates that score well on other signals.

#### Step 4: Three-Signal Scoring

Each candidate pair is scored across three dimensions:

| Signal     | Weight | Method                                              |
|------------|--------|-----------------------------------------------------|
| **Semantic** | 0.55   | Cosine similarity of document embeddings             |
| **Lexical**  | 0.25   | MinHash Jaccard similarity (3-word shingles)         |
| **Metadata** | 0.20   | Average of: title similarity (SequenceMatcher), path similarity (SequenceMatcher), author match (1.0 or 0.0) |

**Combined score:**

```
combined = 0.55 × semantic + 0.25 × lexical + 0.20 × metadata
```

Pairs with `combined >= combined_threshold` (default: 0.80) are kept.

#### Step 5: Grouping

Duplicate pairs are grouped using connected components via BFS:
- Each document = graph node
- Each qualifying pair = edge
- Connected components = duplicate groups

This means if A~B and B~C, then {A, B, C} form one group even if A and C don't directly score high.

#### Step 6: LLM Verification

For pairs scoring above `cross_encoder_threshold` (default: 0.70):

1. Extract top 3 evidence chunks from each document (closest to each other by embedding)
2. Send to LLM with a structured verification prompt
3. LLM returns JSON:
   ```json
   {
     "classification": "duplicate" | "related" | "different",
     "confidence": 0.85,
     "evidence": "Both documents describe the same installation process..."
   }
   ```

**Group recommendation logic:**
- Any pair verified as `"duplicate"` → recommended action: `DELETE_OLDER`
- All pairs above `semantic_threshold` (0.92) but not verified → action: `REVIEW`
- Otherwise → action: `KEEP`

#### Output

- `DuplicateGroup`: A set of potentially duplicate documents with a recommended action
- `DuplicatePair`: Individual pair with all four scores + LLM verification results

---

### 2. Claims Extraction

**Location:** `analysis/contradictions.py` — `ClaimsExtractor`

Extracts atomic factual claims from document chunks for contradiction analysis.

#### Process

For each document chunk with content:

1. Send chunk text to LLM with the `CLAIMS_EXTRACTION` prompt
2. LLM returns structured JSON:
   ```json
   {
     "claims": [
       {
         "subject": "Free tier",
         "predicate": "allows",
         "object": "100 requests per minute",
         "qualifiers": {"as_of": "2024"},
         "raw_text": "Free tier: 100 requests per minute"
       }
     ]
   }
   ```
3. Create `Claim` records linked to the source document and chunk
4. Embed each claim's text representation via `llm.embed()`
5. Store claim vectors in `vec_claims` via `vec_store.upsert_claim()`

**Claim model fields:**
- `subject`: The entity the claim is about
- `predicate`: The relationship or action
- `object_value`: The value or target
- `qualifiers`: Additional context (dates, conditions) as JSON
- `raw_text`: Original text from which the claim was extracted

**Rate limiting:** Max `max_claims_per_chunk` (default: 5) claims per chunk to control cost.

---

### 3. Semantic Graph Construction

**Location:** `analysis/semantic_graph.py` — `ProjectGraphBuilder`

Builds a concept-level knowledge graph from all project documents and claims using the Neural Semantic Graph (NSG) library (`nsg/`). This phase is **optional** and controlled by `semantic_graph.enabled` in `config.yaml`.

#### How It Works

The NSG library extracts concepts (named entities, noun phrases) from text via spaCy, then links co-occurring concepts within each chunk as edges in a directed multigraph. SCORE plugs its own LLM embedding provider into NSG (via the `embed_fn` parameter), so all vectors use the same `text-embedding-3-small` model as the rest of the pipeline.

#### Process

1. **Feed document chunks** — For each `READY` document, concatenate all chunk texts and call `nsg.add_document(doc_id, text)`:
   - NSG internally chunks the text (by character limit)
   - spaCy extracts concepts from each chunk
   - Co-occurring concepts within a chunk become `co_occurs` edges
   - Frequency and evidence snippets are accumulated on nodes and edges

2. **Feed claims** — Each `Claim.as_text` is added as a separate "document" in the graph, enriching concept coverage with structured factual statements.

3. **Build vector index** — `nsg.build_or_update_index()` creates an in-memory index of concept embeddings for query-time retrieval.

4. **Persist to disk** — The graph is saved as:
   - `media/graphs/{project_id}/graph.json` (NetworkX node-link format)
   - `media/graphs/{project_id}/embeddings.npz` (concept vectors as compressed NumPy)

#### NSG Configuration (`config.yaml: semantic_graph`)

| Key              | Default           | Description                              |
|------------------|-------------------|------------------------------------------|
| `enabled`        | `true`            | Enable/disable the semantic graph phase  |
| `spacy_model`    | `fr_core_news_sm` | spaCy model for concept extraction       |
| `chunk_max_chars`| 800               | Max characters per internal NSG chunk    |
| `top_k`          | 12                | Seed concepts returned per query         |
| `hops`           | 2                 | BFS expansion hops from seed concepts    |
| `max_nodes`      | 80                | Max nodes in a query subgraph            |
| `evidence_cap`   | 5                 | Max evidence snippets per edge           |

#### Downstream Usage

- **Gap Detection (Phase 5):** The NSG object is passed to `GapDetector` for structural gap analysis (concept islands, weak bridges).
- **Chat RAG:** The persisted graph is loaded at query time to augment vector search results with concept-level context.

---

### 4. Topic Clustering

**Location:** `analysis/clustering.py` — `TopicClusterEngine`

Discovers topic clusters from chunk embeddings using density-based or centroid-based clustering, projects to 2D for visualization, and generates LLM summaries.

#### Algorithm

```
Fetch all chunk vectors for tenant
          │
          ▼
    ┌─────────────┐
    │  HDBSCAN     │  (or KMeans fallback)
    │  clustering  │
    └──────┬──────┘
           │
    ┌──────┴──────┐
    │  PCA → 2D   │  (for visualization)
    └──────┬──────┘
           │
    ┌──────┴──────┐
    │  Create      │  TopicCluster records
    │  clusters    │  ClusterMembership records
    └──────┬──────┘
           │
    ┌──────┴──────┐
    │  LLM         │  Generate label + summary
    │  summaries   │  per cluster
    └──────┬──────┘
           │
    ┌──────┴──────┐
    │  Build tree  │  TreeNode hierarchy
    └─────────────┘
```

#### Step 1: Clustering

**HDBSCAN** (default, density-based):
```python
hdbscan.HDBSCAN(
    min_cluster_size=3,    # Minimum points to form a cluster
    min_samples=2,         # Core point density threshold
    metric="euclidean"
)
```
- Automatically determines number of clusters
- Label `-1` = noise/outlier (not assigned to any cluster)
- Finds clusters of varying density and size

**KMeans** (fallback):
```python
# Auto k: max(2, min(sqrt(n/2), 20))
KMeans(n_clusters=k, random_state=42, n_init=10)
```
- Fixed number of clusters
- All points assigned (no noise label)

#### Step 2: 2D Projection

PCA with `n_components=2` projects high-dimensional embeddings onto a 2D plane. The coordinates (`centroid_x`, `centroid_y`) are stored on each cluster and used by the D3.js frontend for scatter-plot visualization.

#### Step 3: Cluster Record Creation

For each cluster label:
1. Compute centroid: `mean(cluster_vectors)`
2. Compute 2D centroid from projected coordinates
3. Count unique documents and chunks
4. Create `ClusterMembership` records with similarity-to-centroid:
   ```
   similarity = dot(vector, centroid) / (||vector|| × ||centroid||)
   ```

#### Step 4: LLM Summaries

For each cluster:
1. Select top 5 chunks closest to centroid (most representative)
2. Send to LLM with the `CLUSTER_SUMMARY` prompt
3. LLM returns: `{"label": "API Rate Limiting", "summary": "Documents covering..."}`
4. Update cluster record

#### Step 5: Hierarchical Tree

Build a `TreeNode` tree for frontend navigation:
- **Level 0**: Cluster nodes (type: `cluster`)
- **Level 1**: Document nodes under each cluster (type: `document`)

Documents are assigned to the cluster containing the most of their chunks.

#### Output

- `TopicCluster`: label, summary, doc/chunk counts, 2D centroid
- `ClusterMembership`: chunk-to-cluster assignment with similarity score
- `TreeNode`: Navigable hierarchy

---

### 5. Gap Detection

**Location:** `analysis/gaps.py` — `GapDetector`

Identifies documentation coverage gaps using five complementary strategies (the fifth requires the semantic graph from Phase 3).

```
┌──────────────────────────────────────────────────┐
│                 Gap Detection                     │
│                                                   │
│  ┌─────────────┐   ┌─────────────────┐           │
│  │ QG/RAG      │   │ Orphan Topics   │           │
│  │ Coverage    │   │ (tiny clusters) │           │
│  └─────────────┘   └─────────────────┘           │
│                                                   │
│  ┌─────────────┐   ┌─────────────────┐           │
│  │ Stale Areas │   │ Adjacent Cluster│           │
│  │ (old docs)  │   │ Gaps (LLM)     │           │
│  └─────────────┘   └─────────────────┘           │
│                                                   │
│  ┌───────────────────────────────────┐ optional  │
│  │ Structural Gaps (semantic graph)  │           │
│  │  • Concept islands               │           │
│  │  • Weak bridges                  │           │
│  └───────────────────────────────────┘           │
└──────────────────────────────────────────────────┘
```

#### Strategy 1: QG/RAG Coverage Analysis

Tests whether the documentation can actually answer questions about its own topics.

**For each topic cluster:**

1. **Generate questions** — LLM generates N questions (default: 5) that the cluster's documentation should be able to answer. Each question has an importance level (high/medium/low).

2. **Check retrieval coverage** — For each question:
   - Embed the question
   - Search `vec_chunks` for top 5 relevant chunks
   - If chunks found: LLM evaluates whether the retrieved passages answer the question
   - LLM returns: `{"answered": bool, "confidence": float, "missing_info": str}`

3. **Score coverage:**
   ```
   coverage_score = 1.0 - (unanswered_count / total_questions)
   ```
   - A question is "unanswered" if `answered=false` or `confidence < 0.5`

4. **Assign severity:**
   | Coverage Score | Severity |
   |---------------|----------|
   | < 0.3         | high     |
   | 0.3 – 0.6    | medium   |
   | > 0.6         | low      |

**Gap type:** `LOW_COVERAGE`

#### Strategy 2: Orphan Topic Detection

Identifies clusters too small to represent well-documented topics.

- Find clusters with `doc_count <= orphan_cluster_max_size` (default: 2)
- These represent isolated topics with minimal documentation
- Severity: `low`
- Coverage score: `min(1.0, doc_count / 5.0)`

**Gap type:** `ORPHAN_TOPIC`

#### Strategy 3: Stale Area Detection

Identifies clusters dominated by outdated documents.

For each cluster:
1. Count documents older than `staleness_days` (default: 180 days)
2. Compute stale ratio: `stale_count / total_count`
3. If `stale_ratio >= 0.7`: create a gap report

**Severity:**
| Stale Ratio | Severity |
|-------------|----------|
| >= 0.9      | high     |
| 0.7 – 0.9  | medium   |

**Coverage score:** `1.0 - stale_ratio`

**Gap type:** `STALE_AREA`

#### Strategy 4: Adjacent Cluster Gap Inference

Uses LLM reasoning to identify missing topics between related clusters.

For each cluster:
1. Find 2+ nearest clusters by euclidean distance on 2D centroids (`_get_adjacent_clusters()`)
2. Send cluster labels and summaries to LLM
3. LLM infers: "Is there a topic that should logically exist between these clusters?"
4. If yes: create a gap report with the suggested topic title and description

**Gap type:** `MISSING_TOPIC`

#### Strategy 5: Structural Gaps (Semantic Graph)

Only runs when the semantic graph phase is enabled and produces a graph with 3+ nodes. Uses NetworkX graph algorithms on the NSG's undirected projection.

**Concept Islands** — Disconnected components in the concept graph:
1. Find all connected components via `nx.connected_components()`
2. Sort by size; the largest component is the "main" graph
3. Small isolated components (size <= 5% of main component, or <= 3 nodes) are flagged
4. Severity: `medium` for 2+ concepts, `low` for singletons

**Gap type:** `CONCEPT_ISLAND`

**Weak Bridges** — Bridge edges whose removal would disconnect the graph:
1. Find all bridge edges in the largest component via `nx.bridges()`
2. Only flag bridges where both endpoints have degree >= 2 (non-trivial connections)
3. Each bridge represents a single-point-of-failure in the knowledge structure
4. Severity: `medium`

**Gap type:** `WEAK_BRIDGE`

#### Output

`GapReport` records with: gap_type, title, description, severity, coverage_score, related_cluster, and evidence (JSON).

---

### 6. Contradiction Detection

**Location:** `analysis/contradictions.py` — `ContradictionDetector`

Finds contradictory or outdated claims across documents by combining vector similarity search with LLM classification.

#### Algorithm

```
For each claim with embedding:
  │
  ├─ Vector search: find related claims (similarity >= 0.7)
  │   (via vec_claims KNN, excluding same-document claims)
  │
  ├─ For each related pair:
  │   │
  │   ├─ LLM classification
  │   │   → contradiction / outdated / entailment / unrelated
  │   │
  │   ├─ If contradiction or outdated (confidence >= threshold):
  │   │   ├─ Determine authoritative claim
  │   │   ├─ Adjust severity for staleness
  │   │   └─ Create ContradictionPair record
  │   │
  │   └─ Track checked pairs to avoid duplicates
  │
  └─ Continue to next claim
```

#### Step 1: Find Related Claims

For each claim, search `vec_claims` for the K nearest neighbors:
- Filter to same tenant
- Exclude claims from the same document (a document is typically self-consistent)
- Keep results with `similarity >= 0.7` (topically related)
- Track checked pairs (`frozenset(claim_a.id, claim_b.id)`) to avoid duplicate classification

#### Step 2: LLM Classification

Send both claims (with document titles and dates) to the LLM:

```json
{
  "classification": "contradiction",
  "severity": "high",
  "confidence": 0.92,
  "evidence": "Doc A says 100 req/min, Doc B says 50 req/min for the same tier",
  "newer_claim": "B"
}
```

**Classification types:**
| Classification  | Meaning                                              |
|-----------------|------------------------------------------------------|
| `contradiction` | Claims directly conflict on the same topic           |
| `outdated`      | One claim supersedes the other (newer information)   |
| `entailment`    | Claims are consistent / one implies the other        |
| `unrelated`     | Claims are not about the same topic                  |

Only `contradiction` and `outdated` are persisted (when `confidence >= confidence_threshold`).

#### Step 3: Authority Determination

When two claims conflict, the system determines which is authoritative:

```
1. Use LLM's "newer_claim" hint (if provided)
2. Fallback: compare source weights x recency
   - Source weights (config): sharepoint=1.0, confluence=0.9, generic=0.5
   - If recency_bias=true: prefer newer document when source weight is equal or higher
```

#### Step 4: Staleness Severity Adjustment

If either claim's source document is older than `staleness_days` (default: 180 days):
- `low` severity -> `medium`
- `medium` severity -> `high`

This escalates contradictions involving stale documents.

#### Output

`ContradictionPair` records with: classification, severity, confidence, evidence text, and the authoritative claim (if determinable).

---

## Vector Store Internals

**Location:** `vectorstore/store.py` — `VectorStore`

### Database Layout

```
vec.sqlite3
├── vec_chunks      (vec0 virtual table: chunk_id TEXT, embedding float[1536])
├── vec_metadata    (chunk_id TEXT PK, tenant_id, document_id, doc_type, source_type, extra JSON)
├── vec_claims      (vec0 virtual table: claim_id TEXT, embedding float[1536])
└── claim_metadata  (claim_id TEXT PK, tenant_id, document_id, chunk_id)
```

### Search Algorithm

KNN search with tenant-scoped post-filtering:

1. **Overfetch:** Query `k * 5` nearest neighbors from vec0 (no tenant filter at this level — sqlite-vec doesn't support WHERE clauses on virtual tables)
2. **Post-filter:** Join with metadata table to enforce tenant isolation, optional doc_type filter, and document exclusion
3. **Distance conversion:** sqlite-vec returns L2 distance; convert to cosine similarity:
   ```
   similarity = 1.0 - (distance² / 2.0)
   ```
   (Valid because vectors are L2-normalized before storage)
4. **Return** top `k` results after filtering

### Pragmas

- WAL mode (concurrent reads during writes)
- `synchronous=NORMAL` (balance durability vs. performance)

### Singleton Access

```python
from vectorstore.store import get_vector_store
store = get_vector_store()  # Lazy-initialized, ensures tables exist
```

---

## Chat RAG Pipeline

**Location:** `chat/rag.py` — `ask_documents()`

The chat feature uses a RAG (Retrieval-Augmented Generation) pipeline that optionally augments vector search results with concept-level context from the semantic graph.

### Standard RAG Flow

1. **Embed** the user question via `llm.embed_single()`
2. **Vector search** scoped to tenant + project (top 5 chunks)
3. **Build context** from retrieved chunks with document titles and heading paths
4. **LLM call** with system prompt containing the context passages + conversation history

### Graph-Augmented RAG (optional)

When `semantic_graph.enabled` is `true` and a persisted graph exists for the project:

1. After vector search, load the project graph via `analysis.semantic_graph.load_graph()`
2. Run `nsg.query_subgraph(question)` — finds the top 5 seed concepts closest to the query, then expands 1 hop to collect related concepts and edges (max 20 nodes)
3. Format the concept context: seed concept names + relationship triples (`src —[relation]→ dst`)
4. Prepend the concept context to the chunk context in the system prompt

This provides the LLM with a structural understanding of how concepts relate, complementing the raw text passages from vector search. For example, if a user asks about "rate limits", the graph might surface related concepts like "quotas", "throttling", "API tiers" with their relationships, even if those terms don't appear in the top vector search results.

The graph augmentation fails gracefully — if the graph file doesn't exist or loading fails, standard RAG continues without it.

---

## LLM Client

**Location:** `llm/client.py` — `LLMClient`

### Provider Support

| Provider | Chat Model | Embedding Model | Config Source |
|----------|-----------|-----------------|---------------|
| OpenAI   | gpt-4o (default) | text-embedding-3-small (default) | `OPENAI_*` env vars |
| Azure    | Deployment name  | Deployment name | `AZURE_OPENAI_*` env vars |

### Methods

**`chat(user_message, system="", temperature=0.0, max_tokens=4096, json_mode=False)`**
- Sends a chat completion request
- `json_mode=True` sets `response_format: {"type": "json_object"}` for structured output
- Returns `LLMResponse(content, model, usage)`

**`embed(texts: list[str]) -> list[list[float]]`**
- Batches texts in groups of `embedding_batch_size` (default: 100)
- Returns one vector per text

**`embed_single(text: str) -> list[float]`**
- Convenience wrapper for single text embedding

### Rate Limiting

Enforces `requests_per_minute` (default: 60) by tracking the last request time and sleeping if the minimum interval hasn't elapsed:

```
min_interval = 60.0 / requests_per_minute   # 1.0 second at 60 RPM
```

### Singleton Access

```python
from llm.client import get_llm_client
client = get_llm_client()
```

---

## Configuration Reference

### Chunking (`settings.CHUNKING_CONFIG`)

| Key              | Default          | Description                              |
|------------------|------------------|------------------------------------------|
| `strategy`       | `heading_aware`  | `heading_aware` or `token_fixed`         |
| `chunk_size`     | 512              | Target tokens per chunk                  |
| `chunk_overlap`  | 64               | Overlap between consecutive chunks       |
| `min_chunk_size` | 50               | Minimum tokens to keep a chunk           |

### Embedding (`settings`)

| Key                    | Default | Description                          |
|------------------------|---------|--------------------------------------|
| `EMBEDDING_DIMENSIONS` | 1536    | Vector dimensions (must match model) |

### Duplicate Detection (`config.yaml: duplicate`)

| Key                      | Default | Description                                     |
|--------------------------|---------|-------------------------------------------------|
| `semantic_weight`        | 0.55    | Weight for cosine similarity signal             |
| `lexical_weight`         | 0.25    | Weight for MinHash Jaccard signal               |
| `metadata_weight`        | 0.20    | Weight for title/path/author signal             |
| `semantic_threshold`     | 0.92    | Cosine threshold for "review" recommendation    |
| `combined_threshold`     | 0.80    | Minimum combined score to flag as duplicate     |
| `cross_encoder_threshold`| 0.70    | Minimum score to trigger LLM verification       |
| `minhash_num_perm`       | 128     | MinHash permutation count (accuracy vs. speed)  |

### Contradiction Detection (`config.yaml: contradiction`)

| Key                     | Default | Description                                      |
|-------------------------|---------|--------------------------------------------------|
| `confidence_threshold`  | 0.75    | Minimum LLM confidence to record a contradiction |
| `max_claims_per_chunk`  | 5       | Maximum claims extracted per chunk               |
| `staleness_days`        | 180     | Days after which a document is considered stale  |

### Authority Rules (`config.yaml: authority_rules`)

| Key              | Default                                      | Description                        |
|------------------|----------------------------------------------|------------------------------------|
| `source_weights` | `{sharepoint: 1.0, confluence: 0.9, generic: 0.5}` | Trust level per connector type |
| `recency_bias`   | `true`                                       | Prefer newer documents as authority|

### Clustering (`config.yaml: clustering`)

| Key                | Default   | Description                                 |
|--------------------|-----------|---------------------------------------------|
| `algorithm`        | `hdbscan` | `hdbscan` or `kmeans`                       |
| `min_cluster_size` | 3         | HDBSCAN: minimum points per cluster         |
| `min_samples`      | 2         | HDBSCAN: core point density threshold       |
| `kmeans_k`         | `null`    | KMeans: cluster count (null = auto-select)  |

### Gap Detection (`config.yaml: gap_detection`)

| Key                       | Default | Description                                  |
|---------------------------|---------|----------------------------------------------|
| `coverage_question_count` | 5       | Questions generated per cluster for QG/RAG   |
| `confidence_threshold`    | 0.5     | Minimum confidence to consider a question answered |
| `orphan_cluster_max_size` | 2       | Max docs in a cluster before it's "orphan"   |

### Semantic Graph (`config.yaml: semantic_graph`)

| Key              | Default           | Description                              |
|------------------|-------------------|------------------------------------------|
| `enabled`        | `true`            | Enable/disable the semantic graph phase  |
| `spacy_model`    | `fr_core_news_sm` | spaCy model for concept extraction       |
| `chunk_max_chars`| 800               | Max characters per internal NSG chunk    |
| `top_k`          | 12                | Seed concepts returned per query         |
| `hops`           | 2                 | BFS expansion hops from seed concepts    |
| `max_nodes`      | 80                | Max nodes in a query subgraph            |
| `evidence_cap`   | 5                 | Max evidence snippets per edge           |
