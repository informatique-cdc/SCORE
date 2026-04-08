# Configuration Reference — config.yaml

SCORE reads analysis tuning parameters from `config.yaml` at the project root. Environment variables (`.env`) take precedence for overlapping settings like `llm.provider`.

See the main [README](../README.md#configuration) for `.env` environment variable documentation.

---

## Full config.yaml Reference

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
    elasticsearch: 0.8
    generic: 0.5
  recency_bias: true
```

---

## Section Details

### llm

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `provider` | string | `openai` | LLM provider: `openai`, `azure`, or `azure_mistral` |
| `chat_model` | string | `gpt-4.1` | Model used for chat completions and JSON-mode structured output |
| `embedding_model` | string | `text-embedding-3-small` | Model used for text embeddings |
| `embedding_dimensions` | int | `1536` | Embedding vector dimensionality |
| `requests_per_minute` | int | `500` | Rate limit for LLM API calls |
| `fallback_models` | list | `[gpt-4o-mini]` | Models tried in order when the primary model returns a 429 rate-limit error |

### analysis.duplicate

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `semantic_weight` | float | `0.55` | Weight for semantic (embedding) similarity in combined score |
| `lexical_weight` | float | `0.25` | Weight for lexical (Jaccard) similarity |
| `metadata_weight` | float | `0.20` | Weight for metadata (SequenceMatcher) similarity |
| `semantic_threshold` | float | `0.92` | Minimum cosine similarity to consider a semantic match |
| `combined_threshold` | float | `0.85` | Minimum combined score to flag as duplicate |

### analysis.contradiction

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `confidence_threshold` | float | `0.90` | Minimum LLM confidence to accept a contradiction classification |
| `similarity_threshold` | float | `0.90` | Minimum cosine similarity between claims to check for contradictions |
| `max_claims_per_chunk` | int | `2` | Maximum claims extracted per chunk |
| `staleness_days` | int | `180` | Days after which a claim is considered potentially stale |

### analysis.clustering

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `algorithm` | string | `hdbscan` | Clustering algorithm: `hdbscan` or `kmeans` |
| `min_cluster_size` | int | `5` | HDBSCAN minimum cluster size |
| `min_samples` | int | `3` | HDBSCAN minimum samples |

### analysis.gap_detection

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `coverage_question_count` | int | `2` | Number of coverage questions generated per cluster |
| `confidence_threshold` | float | `0.5` | Minimum confidence for gap detection |
| `orphan_cluster_max_size` | int | `2` | Clusters at or below this size are flagged as orphan topics |

### analysis.hallucination

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `min_acronym_frequency` | int | `2` | Minimum occurrences for an acronym to be flagged |
| `jargon_tfidf_threshold` | float | `0.15` | TF-IDF threshold for jargon detection |
| `hedging_density_threshold` | float | `0.02` | Hedging word density threshold |
| `max_items_per_type` | int | `50` | Maximum items reported per hallucination type |

### audit.axis_weights

Weights for the 6 RAG audit axes. Must sum to 1.0.

| Axis | Default Weight |
|------|---------------|
| `hygiene` | `0.20` |
| `structure` | `0.15` |
| `coverage` | `0.20` |
| `coherence` | `0.15` |
| `retrievability` | `0.20` |
| `governance` | `0.10` |

### semantic_graph

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable semantic graph construction |
| `spacy_model` | string | `fr_core_news_sm` | spaCy model for concept extraction |
| `top_k` | int | `5` | Number of nearest neighbors in concept search |
| `max_nodes` | int | `40` | Maximum nodes in the knowledge map visualization |

### authority_rules

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `source_weights` | dict | see below | Trust weights per connector source type |
| `recency_bias` | bool | `true` | Whether more recent documents are preferred in conflict resolution |

Default source weights: SharePoint `1.0`, Confluence `0.9`, Elasticsearch `0.8`, generic `0.5`.
