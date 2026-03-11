# SCORE - Stack technique et algorithmes

## Stack technique

| Composant | Technologie | Version |
|-----------|-------------|---------|
| **Langage** | Python | >= 3.12 |
| **Framework Web** | Django | >= 5.1, < 5.2 |
| **Auth** | django-allauth | >= 65.0 |
| **WSGI** | Gunicorn | — |
| **DB primaire** | SQLite3 | (defaut Django) |
| **Vector store** | sqlite-vec | 0.1.6 |
| **Task queue** | Celery + Redis (prod) / SQLAlchemy SQLite (dev) | >= 5.4 |
| **Scheduled tasks** | django-celery-beat | >= 2.6 |
| **LLM SDK** | openai (Python) | >= 1.40 |
| **Modeles LLM** | GPT-4.1 (principal), GPT-4o-mini (fallback), Azure OpenAI, Azure Mistral | configurable |
| **Embeddings** | text-embedding-3-small (1536 dim) | configurable |
| **Tokenizer** | tiktoken | >= 0.7 |
| **NLP** | spaCy (fr_core_news_sm, en_core_web_sm), NLTK | >= 3.5 |
| **ML** | scikit-learn, HDBSCAN, FAISS (faiss-cpu) | — |
| **Graphe** | NetworkX | >= 3.1 |
| **LSH** | datasketch (MinHash) | >= 1.6 |
| **BM25** | rank-bm25 | >= 0.2 |
| **Detection langue** | langid | >= 1.1 |
| **Parsing docs** | pypdf, python-docx, python-pptx, BeautifulSoup4, markdown | — |
| **Export PDF** | WeasyPrint | >= 62 |
| **Connecteurs** | SharePoint (msal), Confluence (atlassian-python-api) | optionnel |
| **Conteneurisation** | Docker (python:3.12-slim), Redis 7-alpine | — |

---

## Algorithmes d'analyse

### 1. Detection de doublons (`analysis/duplicates.py`)

- **MinHash LSH** — Signatures MinHash sur des shingles de 3 mots, indexees par LSH (seuil Jaccard configurable, defaut 0.6) pour trouver les candidats en O(1) au lieu de O(n^2)
- **Similarite cosinus semantique** — Embeddings moyennes par document, normalises L2, matmul all-pairs pour les paires au-dessus du seuil
- **Score combine multi-signal** : `0.55 * semantique + 0.25 * lexical (Jaccard) + 0.20 * metadonnees (SequenceMatcher)`
- **Composantes connexes (BFS)** — Regroupement des paires en groupes de doublons
- **Verification LLM** — Classification par le LLM des paires a haute confiance

### 2. Extraction de claims (`analysis/claims.py`)

- **Extraction structuree LLM** — Triplets (sujet, predicat, objet) avec qualificateurs, mode JSON
- **Embedding batch** — Toutes les claims sont embarquees et stockees dans sqlite-vec

### 3. Detection de contradictions (`analysis/contradictions.py`)

- **Matrice cosinus N x N** — Produit matriciel sur les embeddings normalises de toutes les claims
- **Top-K par argpartition** — Selection efficace des K plus proches voisins (tri partiel NumPy)
- **Filtrage cross-document** — Seules les paires inter-documents sont retenues
- **Classification LLM** — entailment / contradiction / outdated / unrelated
- **Resolution d'autorite** — Poids par source (SharePoint=1.0, Confluence=0.9, generique=0.5) + biais de recence

### 4. Clustering thematique (`analysis/clustering.py`)

- **HDBSCAN** — Clustering par densite sur les vecteurs d'embeddings (min_cluster_size=5, min_samples=3)
- **KMeans (fallback)** — k = max(2, min(sqrt(n/2), 20))
- **PCA 2D** — Projection pour la visualisation
- **Sous-clustering hierarchique (KMeans)** — Clusters larges (>=10 membres) subdivises
- **Taxonomie LLM** — Generation de labels, resumes, et organisation hierarchique par le LLM

### 5. Detection de lacunes (`analysis/gaps.py`)

- **QG/RAG** — Generation de questions par cluster, embedding, recherche vectorielle, verification de couverture
- **Pre-filtre par similarite** — Seuils haut (>=0.82, auto-repondu) et bas (<0.35, auto-non-repondu) pour economiser les appels LLM
- **Score de couverture** : `1.0 - (non_repondues / total_questions)`
- **Detection de topics orphelins** — Clusters avec <=2 documents
- **Detection de zones obsoletes** — >=70% de docs non mis a jour depuis 180 jours
- **Inference de gaps adjacents** — Distance euclidienne entre centroides 2D + LLM
- **Gaps structurels via graphe semantique** — `connected_components` et `bridges` de NetworkX

### 6. Graphe semantique (`analysis/semantic_graph.py`)

- **Ingestion documents et claims** — Alimente le NSG avec le texte complet et les claims extraites
- **Embedding batch des concepts** — Tous les embeddings manquants generes en un seul appel batch
- **Persistance** — Graphe en JSON node-link (NetworkX) + embeddings en NumPy .npz

---

## Audit (6 axes)

### 7. Hygiene (`analysis/audit/hygiene.py`)

- **Dedup exacte par hash** — Ratio de chunks dupliques par content_hash
- **MinHash LSH near-duplicate** — Au niveau chunk (seuil Jaccard 0.5)
- **Detection de boilerplate** — Frequence de lignes normalisees (>30% des docs = boilerplate)
- **Homogeneite linguistique** — Classification langid sur echantillon
- **Detection PII/secrets** — Regex (email, telephone FR/intl, cle API, IP, secrets)

**Score** : `0.30 * unicite + 0.20 * neardup + 0.20 * boilerplate + 0.15 * langue + 0.15 * pii`

### 8. Structure RAG (`analysis/audit/structure_rag.py`)

- **Statistiques de taille de chunks** — Moyenne, ecart-type, coefficient de variation
- **Densite informationnelle** — `1 - (stopwords / total_mots)` par chunk (stopwords FR+EN)
- **Metriques de lisibilite** — Phrases/chunk, mots/phrase, caracteres/mot
- **Overlap Jaccard consecutif** — Similarite Jaccard token-level entre chunks consecutifs du meme document

**Score** : `0.30 * uniformite + 0.25 * outliers + 0.25 * densite + 0.20 * lisibilite`

### 9. Couverture (`analysis/audit/coverage.py`)

- **TF-IDF** — Bigrams (1,2), max_features=5000, stopwords bilingues
- **SVD/LSA (Latent Semantic Analysis)** — Reduction dimensionnelle (TruncatedSVD), sortie normalisee L2
- **NMF (Non-negative Matrix Factorization)** — Topic modeling, k = max(3, min(sqrt(n/2), max_topics))
- **KMeans sur vecteurs SVD** — Clustering des chunks
- **Coefficient de Gini** — Mesure de l'equilibre des tailles de clusters
- **Local Outlier Factor (LOF)** — Detection d'outliers dans l'espace SVD (contamination=0.05)
- **Coherence intra-cluster** — Similarite cosinus moyenne au centroide

**Score** : `0.30 * balance + 0.30 * couverture + 0.20 * outliers + 0.20 * coherence`

### 10. Coherence (`analysis/audit/coherence.py`)

- **Extraction de termes TF-IDF** — Top-20 termes par document
- **Detection de variantes terminologiques** — Stemming Snowball (FR) + SequenceMatcher (seuil 0.85)
- **Detection de conflits cle-valeur** — Regex pour SLA, version, port, URL, date, timeout, limite
- **Verification de coherence d'entites** — Extraction regex (dates, versions, URLs, IPs)

**Score** : `0.40 * conflits_kv + 0.30 * terminologie + 0.30 * entites`

### 11. Retrievability (`analysis/audit/retrievability.py`)

- **BM25 (Okapi BM25)** — Index de recherche sur chunks tokenises
- **Generation de requetes** — 3 strategies : titres, chemins de headings, top bigrams TF-IDF
- **MRR (Mean Reciprocal Rank)** — Moyenne de 1/rang du document attendu
- **Recall@K** — Presence du document attendu dans le top-K (K=1, 5, 10)
- **Zero-Result Rate** — Fraction de requetes sans resultat
- **Diversite** — Fraction de documents apparaissant dans un top-10

**Score** : `0.35 * MRR + 0.30 * Recall@10 + 0.20 * (1 - zero_ratio) + 0.15 * diversite` (x100)

### 12. Gouvernance (`analysis/audit/governance.py`)

- **Completude des metadonnees** — Taux de remplissage (author, source_modified_at, doc_type, path)
- **Detection d'obsolescence** — Docs non modifies depuis 180 jours
- **Detection d'orphelins** — Docs sans path ni source_url
- **Connectivite par graphe de chemins** — Groupement par prefixe de chemin, graphe de parents partages

**Score** : `0.30 * completude + 0.25 * fraicheur + 0.25 * orphelins + 0.20 * connectivite`

---

## Graphe Semantique Neural (`nsg/`)

### Extraction de concepts (`nsg/concepts.py`)

- **spaCy NER + noun chunks** — Extraction d'entites nommees et de groupes nominaux, filtrage des spans fonctionnels (DET, ADP, PRON, AUX, PUNCT)
- **Normalisation bilingue FR/EN** — Minuscule, suppression des articles en tete (le/la/les/un/une/des/du/the/a/an), gestion des articles elides (`l'exemple` -> `exemple`)
- **Filtrage stopwords bilingue** — Stopwords FR+EN + pronoms demonstratifs
- **Chunking par phrases** — Decoupage sur ponctuation de fin + retours a la ligne, packing glouton jusqu'a max_chars (defaut 800)

### Construction du graphe (`nsg/graph.py`)

- **Ponderation par co-occurrence** — Poids d'arete incremente pour chaque paire de concepts dans un meme chunk, preuves limitees a evidence_cap (defaut 5)
- **Fusion incrementale de sous-graphes** — Chaque document produit un sous-graphe isole, la fusion incremente les frequences de noeuds et les poids d'aretes
- **Embedding lazy / batch** — Calcul a la demande ou batch via `embed_all_missing()`, normalises L2
- **Expansion BFS ponderee** — File de priorite (max-heap par poids d'arete) pour expandre les graines sur N hops jusqu'a max_nodes
- **MultiDiGraph (NetworkX)** — Support de multiples aretes dirigees entre memes paires de noeuds

### Index vectoriel (`nsg/index.py`)

- **FAISS IndexFlatIP** — Index par produit scalaire sur vecteurs normalises L2 (equivalent cosinus)
- **Fallback brute-force NumPy** — Dot product matriciel + argpartition pour top-K quand FAISS est indisponible

### Stopwords (`nsg/stopwords.py`)

- **Listes bilingues FR/EN** — `STOPWORDS_FR`, `STOPWORDS_EN`, `STOPWORDS_ALL` (frozensets)
- **Integration sklearn** — `get_stopwords_for_sklearn()` pour TfidfVectorizer

### Persistance (`nsg/persistence.py`)

- **Serialisation graphe** — Format JSON node-link NetworkX, fallback pickle pour migration
- **Stockage embeddings** — Liste de concepts en JSON + matrice en NumPy .npy
- **Persistance index FAISS** — Sauvegarde/chargement best-effort du fichier d'index

---

## Pipeline d'orchestration

Le pipeline complet (`analysis/tasks.py`, `analysis/pipeline.py`) execute 13 phases :

**Phases d'analyse LLM (0-55%) :**

1. **Duplicates** (0%) — `DuplicateDetector`
2. **Claims** (12%) — `ClaimsExtractor` (en parallele avec Duplicates)
3. **Semantic Graph** (18%) — `ProjectGraphBuilder` (optionnel)
4. **Clustering** (24%) — `TopicClusterEngine`
5. **Gaps** (36%) — `GapDetector` (utilise le NSG si disponible)
6. **Tree** (45%) — Index hierarchique (construit pendant le clustering)
7. **Contradictions** (55%) — `ContradictionDetector`

**Phases d'audit (60-93%, toutes en parallele via ThreadPoolExecutor) :**

8. Hygiene (60%)
9. Structure (67%)
10. Couverture (73%)
11. Coherence (80%)
12. Retrievability (87%)
13. Gouvernance (93%)

Fonctionnalites : checkpoint/resume depuis toute phase echouee, overrides de config par job, tracing du pipeline (comptage de tokens, timings, logs d'evenements), callbacks de progression rate-limited avec estimation ETA.

---

## Score final SCORE (`dashboard/scoring.py`)

**SCORE = 100 - somme des penalites**

| Dimension | Penalite max | Sources |
|-----------|-------------|---------|
| Unicite | 15 | Groupes de doublons LLM |
| Coherence | 15 | Contradictions ponderees (high x 3 + med x 2 + low) |
| Couverture | 20 | Gaps LLM (12) + audit coverage (8) |
| Structure | 15 | Cohesion clusters LLM (9) + audit structure (6) |
| Sante | 10 | Ratio docs ready/error/total |
| Retrievability | 15 | Audit retrievability (9) + audit hygiene (6) |
| Gouvernance | 10 | Audit governance (6) + audit coherence (4) |

**Score global de l'audit** : somme ponderee des 6 axes. Poids : hygiene=0.20, structure=0.15, coverage=0.20, coherence=0.15, retrievability=0.20, governance=0.10.

**Grade : A >= 80, B >= 60, C >= 40, D >= 20, E < 20**
