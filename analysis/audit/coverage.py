"""Axe 3 — Couverture sémantique: TF-IDF, SVD/LSA, NMF topics, KMeans, LOF."""

import logging
import math

import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import NMF, TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import normalize

from nsg.stopwords import get_stopwords_for_sklearn

from .base import BaseAuditAxis

logger = logging.getLogger(__name__)


class CoverageAxis(BaseAuditAxis):
    axis_key = "coverage"
    axis_label = "Couverture sémantique"

    def analyze(self):
        from ingestion.models import DocumentChunk

        chunks = list(
            DocumentChunk.objects.filter(
                document__project=self.project,
                document__status="ready",
            )
            .select_related("document__connector")
            .values_list(
                "id", "content", "document_id", "document__title", "document__connector__name"
            )
        )

        if len(chunks) < 5:
            return (
                100.0,
                {"total_chunks": len(chunks)},
                {},
                {"message": "Trop peu de chunks pour l'analyse de couverture"},
            )

        cfg = self.config
        max_features = cfg.get("tfidf_max_features", 10000)
        n_components = cfg.get("svd_components", 50)
        max_topics = cfg.get("max_topics", 20)
        contamination = cfg.get("outlier_contamination", 0.05)

        texts = [c[1] for c in chunks]
        n_docs = len(texts)

        # TF-IDF
        vectorizer = TfidfVectorizer(
            max_features=max_features,
            ngram_range=(1, 2),
            stop_words=get_stopwords_for_sklearn(),
            min_df=2,
            max_df=0.95,
        )
        tfidf_matrix = vectorizer.fit_transform(texts)
        feature_names = vectorizer.get_feature_names_out()

        # SVD/LSA
        actual_components = min(n_components, tfidf_matrix.shape[1] - 1, n_docs - 1)
        if actual_components < 2:
            actual_components = 2
        svd = TruncatedSVD(n_components=actual_components, random_state=42)
        svd_matrix = svd.fit_transform(tfidf_matrix)
        svd_normed = normalize(svd_matrix)

        # NMF topic modeling
        k_topics = max(3, min(int(math.sqrt(n_docs / 2)), max_topics))
        k_topics = min(k_topics, tfidf_matrix.shape[1])
        nmf = NMF(n_components=k_topics, random_state=42, max_iter=300)
        nmf_matrix = nmf.fit_transform(tfidf_matrix)

        # Topic terms
        topics = []
        for i, comp in enumerate(nmf.components_):
            top_idx = comp.argsort()[-10:][::-1]
            terms = [str(feature_names[j]) for j in top_idx]
            topics.append({"id": i, "terms": terms, "weight": float(comp.sum())})

        # KMeans clustering on SVD vectors
        km = KMeans(n_clusters=k_topics, random_state=42, n_init=10)
        labels = km.fit_predict(svd_normed)

        # Cluster sizes
        cluster_sizes = {}
        for label in labels:
            cluster_sizes[int(label)] = cluster_sizes.get(int(label), 0) + 1

        # Outlier detection
        if n_docs >= 20:
            lof = LocalOutlierFactor(contamination=contamination, n_neighbors=min(20, n_docs - 1))
            outlier_labels = lof.fit_predict(svd_normed)
            outlier_count = int((outlier_labels == -1).sum())
        else:
            outlier_labels = np.ones(n_docs)
            outlier_count = 0
        outlier_ratio = outlier_count / n_docs

        # PCA 2D projection for scatter chart
        from sklearn.decomposition import PCA

        pca = PCA(n_components=2, random_state=42)
        coords_2d = pca.fit_transform(svd_normed)

        # Metrics computation
        # Gini coefficient of cluster sizes
        sizes = sorted(cluster_sizes.values())
        gini = self._gini(sizes)
        balance_score = (1 - gini) * 100

        # Topic coverage: topics with >= 3 docs
        topic_doc_counts = []
        for i in range(k_topics):
            count = int((nmf_matrix[:, i] > 0.01).sum())
            topic_doc_counts.append(count)
        covered_topics = sum(1 for c in topic_doc_counts if c >= 3)
        coverage_ratio = covered_topics / k_topics if k_topics > 0 else 1
        coverage_score = coverage_ratio * 100

        # Outlier cleanliness
        outlier_score = max(0, (1 - outlier_ratio * 5)) * 100

        # Intra-cluster coherence
        coherences = []
        for i in range(k_topics):
            mask = labels == i
            if mask.sum() > 1:
                cluster_vecs = svd_normed[mask]
                centroid = cluster_vecs.mean(axis=0)
                sims = cluster_vecs @ centroid
                coherences.append(float(sims.mean()))
        avg_coherence = sum(coherences) / len(coherences) if coherences else 0.5
        coherence_score = avg_coherence * 100

        score = (
            0.30 * balance_score
            + 0.30 * coverage_score
            + 0.20 * outlier_score
            + 0.20 * coherence_score
        )

        metrics = {
            "total_chunks": n_docs,
            "k_topics": k_topics,
            "gini_coefficient": round(gini, 4),
            "balance_score": round(balance_score, 1),
            "covered_topics": covered_topics,
            "coverage_ratio": round(coverage_ratio, 4),
            "outlier_count": outlier_count,
            "outlier_ratio": round(outlier_ratio, 4),
            "avg_coherence": round(avg_coherence, 4),
            "sub_scores": {
                "balance": round(balance_score, 1),
                "coverage": round(coverage_score, 1),
                "outliers": round(outlier_score, 1),
                "coherence": round(coherence_score, 1),
            },
        }

        # Chart data
        # 2D scatter (sampled if too many)
        sample_n = min(500, n_docs)
        indices = (
            np.random.default_rng(42).choice(n_docs, sample_n, replace=False)
            if n_docs > sample_n
            else np.arange(n_docs)
        )
        scatter = []
        for idx in indices:
            scatter.append(
                {
                    "x": float(coords_2d[idx, 0]),
                    "y": float(coords_2d[idx, 1]),
                    "topic": int(labels[idx]),
                    "outlier": int(outlier_labels[idx] == -1),
                    "doc_title": chunks[idx][3][:50],
                }
            )

        # Topic volumes bar chart
        topic_volumes = [
            {"topic": i, "terms": topics[i]["terms"][:5], "count": cluster_sizes.get(i, 0)}
            for i in range(k_topics)
        ]

        # Topics x source stacked bar
        source_topic = {}
        for i, c in enumerate(chunks):
            source = c[4] or "Inconnu"
            topic = int(labels[i])
            key = (source, topic)
            source_topic[key] = source_topic.get(key, 0) + 1

        stacked = []
        for (source, topic), count in source_topic.items():
            stacked.append({"source": source, "topic": topic, "count": count})

        chart_data = {
            "scatter_2d": scatter,
            "topic_volumes": topic_volumes,
            "source_topic_stacked": stacked,
            "topics_table": topics,
        }

        details = {
            "topics": topics,
            "cluster_sizes": cluster_sizes,
            "outlier_chunks": [
                {"chunk_id": str(chunks[i][0]), "doc_title": chunks[i][3][:80]}
                for i in range(n_docs)
                if outlier_labels[i] == -1
            ][:50],
        }

        return score, metrics, chart_data, details

    def _gini(self, values):
        """Compute Gini coefficient for a list of non-negative values."""
        if not values or sum(values) == 0:
            return 0
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        cumsum = sum((i + 1) * v for i, v in enumerate(sorted_vals))
        return (2 * cumsum) / (n * sum(sorted_vals)) - (n + 1) / n
