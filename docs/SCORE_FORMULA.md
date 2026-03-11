# SCORE Formula

SCORE is a Nutri-Score-style quality grade (A through E) for a knowledge base. It evaluates the overall health, consistency, and completeness of a document repository by combining metrics from the latest completed LLM analysis **and** the latest RAG audit (if available).

## Grade Scale

| Grade | Score Range | Meaning |
|-------|------------|---------|
| **A** | 80 - 100 | Excellent |
| **B** | 60 - 79 | Good |
| **C** | 40 - 59 | Acceptable |
| **D** | 20 - 39 | Poor |
| **E** | 0 - 19 | Critical |

## How It Works

### LLM Analysis Score (5 dimensions)

The base score starts at **100** and penalties are subtracted across five dimensions. Each dimension has a maximum penalty, totalling 100 points.

```
llm_score = 100 - uniqueness_penalty
                - consistency_penalty
                - coverage_penalty
                - structure_penalty
                - health_penalty
```

The LLM score is clamped to `[0, 100]`.

### Composite Score (LLM + RAG Audit)

When both an LLM analysis and a RAG audit are completed, the final SCORE is a **weighted composite**:

```
final_score = llm_score × 0.85 + audit_rag_score × 0.15
```

The composite is clamped to `[0, 100]` and mapped to a letter grade.

| Scenario | Formula |
|---|---|
| LLM analysis + RAG audit both completed | `llm_score × 0.85 + audit_score × 0.15` |
| LLM analysis only (no audit) | `llm_score` (unchanged, no penalty) |
| RAG audit only (no LLM analysis) | `health_score × 0.15 + audit_score × 0.15` |
| No analysis at all | Health dimension only, capped at 15 points |

---

## Part 1 — LLM Analysis Dimensions

### 1. Uniqueness (max penalty: 20 points)

Measures how free the repository is from duplicate content.

**Inputs:**
- `actionable_dup_groups`: Number of `DuplicateGroup` records from the latest analysis, excluding groups with `recommended_action = "keep"` (these are related but distinct documents).
- `total_docs`: Total non-deleted documents in the tenant.

**Formula:**
```
dup_ratio = actionable_dup_groups / total_docs
uniqueness_penalty = min(20, dup_ratio / 0.30 * 20)
```

A duplicate ratio of **30% or higher** triggers the full 20-point penalty. Zero duplicates means zero penalty.

**Sub-score:** `uniqueness = 100 - (uniqueness_penalty / 20 * 100)`

---

### 2. Consistency (max penalty: 25 points)

Measures how free the repository is from contradictory or outdated information.

**Inputs:**
- Contradiction pairs from the latest analysis where `classification` is `"contradiction"` or `"outdated"`, grouped by severity:
  - `high_c`: Count with severity `"high"`
  - `med_c`: Count with severity `"medium"`
  - `low_c`: Count with severity `"low"`

**Formula:**
```
weighted_contradictions = high_c * 3 + med_c * 2 + low_c * 1
contra_ratio = weighted_contradictions / total_docs
consistency_penalty = min(25, contra_ratio / 0.50 * 25)
```

Severity weights reflect impact: a single high-severity contradiction counts 3x more than a low-severity one. A weighted ratio of **0.50 or higher** triggers the full 25-point penalty.

**Sub-score:** `consistency = 100 - (consistency_penalty / 25 * 100)`

---

### 3. Coverage (max penalty: 25 points)

Measures how complete the knowledge base is, based on detected gaps.

**Inputs:**
- Gap reports from the latest analysis (types: `missing_topic`, `low_coverage`, `stale_area`, `orphan_topic`, `weak_bridge`, `concept_island`), grouped by severity:
  - `high_g`, `med_g`, `low_g`: Counts per severity level
- `avg_coverage_score`: Average `coverage_score` across all gap reports (0-1 scale, where lower = bigger gap)

**Formula (two components):**
```
# Component A: Gap count penalty (max 15)
weighted_gaps = high_g * 3 + med_g * 2 + low_g * 1
gap_ratio = weighted_gaps / total_docs
gap_penalty = min(15, gap_ratio / 0.50 * 15)

# Component B: Coverage depth penalty (max 10)
if avg_coverage_score is available:
    coverage_adj = (1 - avg_coverage_score) * 10
else:
    coverage_adj = 5   # assume moderate gaps when no data

# Combined
coverage_penalty = min(25, gap_penalty + coverage_adj)
```

Component A penalizes the sheer number of gaps (weighted by severity). Component B penalizes low average coverage depth. Together they cap at 25 points.

**Sub-score:** `coverage = 100 - (coverage_penalty / 25 * 100)`

---

### 4. Structure (max penalty: 15 points)

Measures how well-organized the content is into coherent topic clusters.

**Inputs:**
- `avg_cohesion`: Average `similarity_to_centroid` across all `ClusterMembership` records for the latest analysis (0-1 scale, higher = tighter clusters).
- `cluster_count`: Number of `TopicCluster` records.

**Formula:**
```
structure_penalty = 0

if avg_cohesion is available:
    structure_penalty += max(0, (1 - avg_cohesion)) * 10    # max ~10
else:
    structure_penalty += 8   # no cohesion data available

if cluster_count == 0:
    structure_penalty += 5   # no clusters detected at all

structure_penalty = min(15, structure_penalty)
```

High cohesion (e.g. 0.85) means documents within each cluster are tightly related, resulting in a small penalty of ~1.5. Low cohesion (e.g. 0.40) means clusters are loose and overlapping, resulting in a penalty of ~6.

**Sub-score:** `structure = 100 - (structure_penalty / 15 * 100)`

---

### 5. Health (max penalty: 15 points)

Measures the operational state of the document pipeline.

**Inputs:**
- `ready_docs`: Documents with status `"ready"` (fully ingested, chunked, and embedded).
- `error_docs`: Documents with status `"error"`.
- `total_docs`: Total non-deleted documents.

**Health sub-score (0-100):**
```
error_penalty = min(50, (error_docs / total_docs) * 500)
ready_bonus = (ready_docs / total_docs) * 100
health = clamp(0, 100, round(ready_bonus - error_penalty))
```

An error rate of 10% triggers a 50-point health penalty. 100% readiness with 0% errors gives a perfect 100.

**Mapped to main score:**
```
health_penalty = (100 - health) / 100 * 15
```

**Sub-score:** `health` (the 0-100 value directly)

---

## Part 2 — RAG Audit (6th Dimension: "Qualité RAG")

The RAG audit is a **fully automated, LLM-free** evaluation that runs 6 axes sequentially via a Celery task (`analysis.audit.runner.run_audit`). Each axis produces a score (0-100), and the overall audit score is a **weighted average** of all axis scores:

```
overall_audit_score = Σ(axis_score × axis_weight) / Σ(axis_weight)
```

Default axis weight: `1/6` per axis (uniform). Custom weights can be set in `config.yaml` under `audit.axis_weights`.

The overall audit score is mapped to a letter grade using the same A-E scale as SCORE.

### Execution Order and Progress

| Order | Axis Key | Module | Progress Range |
|-------|----------|--------|----------------|
| 1 | `hygiene` | `analysis.audit.hygiene.HygieneAxis` | 0% - 15% |
| 2 | `structure` | `analysis.audit.structure_rag.StructureAxis` | 15% - 30% |
| 3 | `coverage` | `analysis.audit.coverage.CoverageAxis` | 30% - 50% |
| 4 | `coherence` | `analysis.audit.coherence.CoherenceAxis` | 50% - 65% |
| 5 | `retrievability` | `analysis.audit.retrievability.RetrievabilityAxis` | 65% - 82% |
| 6 | `governance` | `analysis.audit.governance.GovernanceAxis` | 82% - 97% |

---

### Axis 1 — Hygiène du corpus (Corpus Hygiene)

**Purpose:** Detects exact duplicates, near-duplicates, boilerplate content, language fragmentation, and PII/secrets exposure.

**Algorithms and libraries:**

| Sub-metric | Algorithm | Library | Parameters |
|---|---|---|---|
| Exact dedup | SHA-256 `content_hash` counting via `collections.Counter` | Python stdlib (`collections`, `hashlib`) | — |
| Near-duplicate | MinHash LSH (Locality-Sensitive Hashing) | **`datasketch`** (`MinHash`, `MinHashLSH`) | `num_perm=128`, shingle size: 3-word, Jaccard threshold: `0.5`, sample cap: 2000 chunks |
| Boilerplate | Normalized line frequency analysis (line appears in >N% of documents) | Python stdlib (`collections.Counter`) | Frequency threshold: `0.30` (30% of docs), min line length: 10 chars |
| Language detection | Statistical language classifier | **`langid`** (`langid.classify`) | Sample: 200 chunks, first 500 chars per chunk, min 20 chars |
| PII / secrets | Regular expression pattern matching | Python stdlib (`re`) | 6 patterns: email, phone_fr, phone_intl, api_key, ip_address, secret_generic; sample cap: 1000 chunks |

**Scoring formula:**
```
uniqueness_score  = max(0, 100 × (1 - exact_dup_ratio × 5))
neardup_score     = max(0, 100 × (1 - neardup_ratio × 3))
boilerplate_score = max(0, 100 × (1 - boilerplate_ratio × 3))
pii_score         = max(0, 100 × (1 - pii_ratio × 10))
lang_score        = min(100, dominant_language_ratio × 100)

hygiene_score = 0.30 × uniqueness_score
              + 0.20 × neardup_score
              + 0.20 × boilerplate_score
              + 0.15 × lang_score
              + 0.15 × pii_score
```

**Near-duplicate algorithm detail:**
1. For each chunk (up to 2000), extract 3-word shingles from lowercased content
2. Build a `MinHash` signature with 128 permutations per chunk
3. Insert all signatures into a `MinHashLSH` index with Jaccard threshold 0.5
4. Query each signature to find candidate pairs
5. Deduplicate pairs using sorted tuple keys
6. `neardup_ratio = len(unique_pairs) / len(sampled_chunks)`

---

### Axis 2 — Structure RAG (RAG Structure)

**Purpose:** Evaluates chunk sizing uniformity, information density, readability, and inter-chunk overlap.

**Algorithms and libraries:**

| Sub-metric | Algorithm | Library | Parameters |
|---|---|---|---|
| Size uniformity | Coefficient of Variation (CV = std / mean) | Python stdlib (`math.sqrt`) | min_tokens: `50`, max_tokens: `1024`, optimal: `512` |
| Outlier detection | Count of chunks below `min_tokens` or above `max_tokens` | — | Penalty multiplier: `×3` on outlier_ratio |
| Info density | Stopword ratio (1 - stopwords/total_words) | Python stdlib (`re`) | Combined FR + EN stopword set (~150 words), word extraction via `\w+` regex |
| Readability | Sentences/chunk and words/sentence heuristics | Python stdlib (`re.split`) | Sentence split on `[.!?]+`, penalty if avg words/sentence > 30 or avg sentences < 2 |
| Overlap | Jaccard similarity on token sets between consecutive same-document chunks | Python stdlib (`set` operations) | Token extraction via `\w+` regex |

**Scoring formula:**
```
cv = std_tokens / mean_tokens
uniformity_score  = max(0, 100 × (1 - cv))
outlier_score     = max(0, 100 × (1 - outlier_ratio × 3))
density_score     = min(100, avg_density × 150)
readability_score = 100 - penalties (words/sentence > 30: -2 per excess word, cap 40; sentences < 2: -20)

structure_score = 0.30 × uniformity_score
                + 0.25 × outlier_score
                + 0.25 × density_score
                + 0.20 × readability_score
```

**Visualizations:** Token histogram (25 bins), box plot per source (Q1/median/Q3/min/max), scatter plot (tokens vs density, up to 500 points).

---

### Axis 3 — Couverture sémantique (Semantic Coverage)

**Purpose:** Measures topic diversity, balance, and semantic coverage of the corpus using unsupervised NLP.

**Algorithms and libraries:**

| Sub-metric | Algorithm | Library | Parameters |
|---|---|---|---|
| Vectorization | TF-IDF (Term Frequency - Inverse Document Frequency) | **`scikit-learn`** (`TfidfVectorizer`) | `max_features=10000`, `ngram_range=(1, 2)`, `min_df=2`, `max_df=0.95` |
| Dimensionality reduction | Truncated SVD (Latent Semantic Analysis / LSA) | **`scikit-learn`** (`TruncatedSVD`) | `n_components=50` (capped at `min(50, n_features-1, n_docs-1)`, min 2), `random_state=42` |
| Normalization | L2 normalization on SVD vectors | **`scikit-learn`** (`sklearn.preprocessing.normalize`) | — |
| Topic modeling | NMF (Non-Negative Matrix Factorization) | **`scikit-learn`** (`NMF`) | `k = max(3, min(√(n_docs/2), 20))`, `max_iter=300`, `random_state=42`, top 10 terms per topic |
| Clustering | KMeans on L2-normalized SVD vectors | **`scikit-learn`** (`KMeans`) | `n_clusters=k_topics`, `n_init=10`, `random_state=42` |
| Outlier detection | Local Outlier Factor (LOF) | **`scikit-learn`** (`LocalOutlierFactor`) | `contamination=0.05`, `n_neighbors=min(20, n_docs-1)`, requires `n_docs >= 20` |
| Topic balance | Gini coefficient on cluster sizes | Custom implementation | `gini = (2 × Σ(i+1)×v_i) / (n × Σv_i) - (n+1)/n` |
| 2D projection | PCA (Principal Component Analysis) | **`scikit-learn`** (`PCA`) | `n_components=2`, `random_state=42`, applied on normalized SVD vectors |

**Pipeline:**
1. TF-IDF vectorization (uni+bigrams) → sparse matrix
2. TruncatedSVD (50 components) → dense matrix for clustering
3. L2 normalization → unit vectors
4. NMF topic modeling on TF-IDF matrix → topic-document matrix + topic terms
5. KMeans clustering on normalized SVD vectors → cluster assignments
6. LOF on normalized SVD vectors → outlier labels (if n_docs >= 20)
7. PCA 2D on normalized SVD vectors → scatter coordinates
8. Gini coefficient on cluster size distribution

**Scoring formula:**
```
balance_score   = (1 - gini_coefficient) × 100
coverage_score  = (covered_topics / k_topics) × 100           # covered = topics with ≥ 3 docs
outlier_score   = max(0, (1 - outlier_ratio × 5)) × 100
coherence_score = avg_intra_cluster_cosine_similarity × 100   # centroid dot product

coverage_axis_score = 0.30 × balance_score
                    + 0.30 × coverage_score
                    + 0.20 × outlier_score
                    + 0.20 × coherence_score
```

**Intra-cluster coherence detail:**
For each cluster: compute mean vector (centroid), then average cosine similarity of all cluster members to the centroid (`cluster_vecs @ centroid.mean()`). Final coherence = mean across all clusters.

---

### Axis 4 — Cohérence interne (Internal Coherence)

**Purpose:** Detects terminology variants, key-value conflicts, and entity inconsistencies across the corpus.

**Algorithms and libraries:**

| Sub-metric | Algorithm | Library | Parameters |
|---|---|---|---|
| Term extraction | TF-IDF per-document, top-20 terms | **`scikit-learn`** (`TfidfVectorizer`) | `max_features=5000`, `ngram_range=(1, 1)`, `min_df=1`, `max_df=0.95` |
| Variant detection (stemming) | Snowball stemmer (French) | **`nltk`** (`nltk.stem.snowball.SnowballStemmer`) | Language: `"french"` |
| Variant detection (similarity) | SequenceMatcher ratio | Python stdlib (`difflib.SequenceMatcher`) | Similarity threshold: `0.85` |
| KV conflict detection | Regex extraction for 7 key types | Python stdlib (`re`) | Keys: `sla`, `version`, `port`, `url`, `date`, `timeout`, `limit` |
| Entity consistency | Regex extraction for 4 entity types | Python stdlib (`re`) | Types: `date`, `version`, `url`, `ip` |

**Variant detection algorithm:**
1. Extract top-20 TF-IDF terms per document
2. Group all terms by their French Snowball stem
3. For groups with ≥2 surface forms, check pairwise `SequenceMatcher.ratio()`
4. If any pair has ratio ≥ 0.85 and different surface forms → variant group
5. Canonical form = most frequent surface form across documents

**KV conflict detection:**
Regex patterns extract structured values (e.g., `SLA := 99.9%`, `port = 8080`, `timeout = 30s`) from all chunks. Values are normalized (strip + lowercase). A conflict exists when the same key has multiple distinct values across different documents.

**Scoring formula:**
```
conflict_ratio = total_conflicting_values / total_docs
conflict_score = max(0, 100 × (1 - conflict_ratio × 5))

term_consistency = max(0, 100 - variant_groups × 2)

entity_conflict_count = Σ(len(values) - 1) for entities with > 1 value
entity_score = max(0, 100 × (1 - entity_conflict_count / (total_docs × 3)))

coherence_score = 0.40 × conflict_score
                + 0.30 × term_consistency
                + 0.30 × entity_score
```

---

### Axis 5 — Retrievability

**Purpose:** Evaluates how well documents can be found using full-text search, by building a BM25 index and running auto-generated queries against it.

**Algorithms and libraries:**

| Sub-metric | Algorithm | Library | Parameters |
|---|---|---|---|
| Full-text index | BM25 Okapi | **`rank-bm25`** (`BM25Okapi`) | Tokenization: `\w+` regex on lowercased content |
| Query generation (titles) | Direct document title usage | — | — |
| Query generation (headings) | Direct heading path usage | — | Min length: 3 chars |
| Query generation (bigrams) | TF-IDF bigram extraction per document | **`scikit-learn`** (`TfidfVectorizer`) | `ngram_range=(2, 2)`, `max_features=5000`, `min_df=1`, `max_df=0.9`, top N per doc |
| MRR | Mean Reciprocal Rank | Custom | `MRR = avg(1/rank_of_expected_doc)` |
| Recall@k | Recall at k values | Custom | k = `[1, 3, 5, 10, 20]` |
| Diversity | Unique doc ratio in top-10 results | Custom | `diversity = |unique_docs_in_top10| / total_docs` |

**Pipeline:**
1. Tokenize all chunks (lowercased, `\w+` regex) → build `BM25Okapi` index
2. Generate queries:
   - Document titles as queries
   - Chunk heading paths as queries
   - Top TF-IDF bigrams per document (configurable `queries_per_doc`, default 3)
   - Deduplicate and limit to 500 queries
3. For each query, score against BM25 index, rank results
4. Evaluate: find rank of expected document in results
5. Compute MRR, Recall@k, zero-result ratio, diversity

**Scoring formula:**
```
retrievability_score = 0.35 × MRR × 100
                     + 0.30 × Recall@10 × 100
                     + 0.20 × (1 - zero_result_ratio) × 100
                     + 0.15 × min(diversity, 1.0) × 100
```

---

### Axis 6 — Gouvernance & metadata (Governance & Metadata)

**Purpose:** Evaluates metadata completeness, document freshness, orphan documents, and path-based connectivity.

**Algorithms and libraries:**

| Sub-metric | Algorithm | Library | Parameters |
|---|---|---|---|
| Metadata completeness | Field fill-rate across required fields | — | Required fields: `["author", "source_modified_at", "doc_type", "path"]` |
| Staleness | Age computation from `source_modified_at` (or `created_at` fallback) | Python stdlib (`datetime.timedelta`) | Threshold: `180` days |
| Orphan detection | Documents with no `path` AND no `source_url` | — | — |
| Path connectivity | Shared path prefix grouping (first 2 path segments) | Python stdlib (`collections.defaultdict`) | Connectivity = fraction of docs in groups > 1 |
| Per-source completeness | Aggregate fill-rate by connector source | — | — |

**Scoring formula:**
```
completeness_score = avg_field_fill_rate × 100

freshness_score = max(0, (1 - stale_ratio) × 100)

orphan_score = max(0, (1 - orphan_ratio × 3) × 100)

connectivity_score:
  - No edges between path prefix groups → 30.0
  - Otherwise: min(100, (docs_in_multi_doc_groups / total_docs_with_paths) × 100)

governance_score = 0.30 × completeness_score
                 + 0.25 × freshness_score
                 + 0.25 × orphan_score
                 + 0.20 × connectivity_score
```

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| **No documents** | Score = 0, Grade = E |
| **Documents exist but no completed analysis and no audit** | Only the Health dimension is scored (worth up to 15 points). Other dimensions show as "N/A". |
| **Documents + completed audit but no LLM analysis** | `score = health × 0.15 + audit_score × 0.15` |
| **Documents + LLM analysis + completed audit** | `score = llm_score × 0.85 + audit_score × 0.15` |
| **Documents + LLM analysis but no audit** | `score = llm_score` (no penalty) |
| **No gap reports** | `avg_coverage_score` is `None`, so a default penalty of 5 is applied for Component B. |
| **No cluster memberships** | `avg_cohesion` is `None`, applying an 8-point structure penalty. |
| **No contradictions** | Zero penalty for Consistency. |
| **No duplicates** | Zero penalty for Uniqueness. |
| **Audit axis: < 5 chunks** | Coverage, Coherence, and Retrievability axes return 100 (insufficient data). |
| **Audit axis: < 3 chunks** | Coherence axis returns 100 (insufficient data). |
| **Audit axis: datasketch not installed** | Near-duplicate analysis skipped, returns 0 pairs. |
| **Audit axis: rank-bm25 not installed** | Retrievability returns a fixed 50.0 score. |
| **Audit axis: n_docs < 20** | LOF outlier detection skipped (all docs treated as inliers). |

## Examples

### Perfect Score (A, 100)
- 50 documents, all `ready`, 0 errors
- 0 duplicate groups
- 0 contradictions
- 0 gaps, average coverage = 1.0
- 10 clusters, average cohesion = 1.0
- Audit: all 6 axes at 100/100

### Typical Good Score (B, ~72)
- 100 documents, 90 ready, 2 errors
- 5 duplicate groups (actionable)
- 3 high contradictions, 5 medium
- 4 gaps (1 high, 2 medium, 1 low), avg coverage = 0.7
- 8 clusters, avg cohesion = 0.75
- No audit completed (rag_quality = None)

### Poor Score (D, ~30)
- 20 documents, 12 ready, 4 errors
- 8 duplicate groups
- 10 high contradictions, 8 medium
- 12 gaps (5 high, 4 medium, 3 low), avg coverage = 0.3
- 2 clusters, avg cohesion = 0.4
- Audit completed with overall score 35/100

## Implementation

Source: `score/scoring.py`

The `compute_score(project)` function returns:
```python
{
    "grade": "B",        # Letter grade A-E
    "score": 72,         # Numerical score 0-100
    "breakdown": {
        "uniqueness": 88,      # Sub-score 0-100
        "consistency": 64,
        "coverage": 58,
        "structure": 83,
        "health": 95,
        "rag_quality": 71,     # RAG audit overall score (0-100 or None)
    },
    "has_docs": True,     # Whether project has any documents
    "has_analysis": True, # Whether a completed LLM analysis exists
}
```

The `compute_score_detail(project)` function returns the same score plus per-dimension explanations, details, and recommendations. It includes a 6th dimension **"Qualité RAG"** with axis-level breakdown when an audit is completed.

## Python Dependencies (RAG Audit)

| Package | Version | Used By |
|---|---|---|
| `scikit-learn` | `>=1.5` | TF-IDF, TruncatedSVD, NMF, KMeans, LOF, PCA, normalize (Coverage, Coherence, Retrievability) |
| `datasketch` | `>=1.6` | MinHash, MinHashLSH near-duplicate detection (Hygiene) |
| `numpy` | `>=1.26` | Array operations, outlier label handling (Coverage, Governance) |
| `langid` | `>=1.1` | Language identification (Hygiene) |
| `rank-bm25` | `>=0.2` | BM25Okapi full-text search index (Retrievability) |
| `nltk` | `>=3.8` | SnowballStemmer French stemming (Coherence) |

All audit axes inherit from `analysis.audit.base.BaseAuditAxis` which provides the `execute()` lifecycle (timing, error handling) and calls the axis-specific `analyze()` method.
