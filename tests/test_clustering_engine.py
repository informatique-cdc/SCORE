"""Tests for analysis.clustering — TopicClusterEngine."""

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

pytest.importorskip("openai", reason="openai not installed")

from analysis.clustering import TopicClusterEngine  # noqa: E402
from analysis.models import ClusterMembership, TopicCluster, TreeNode
from tests.conftest import make_chunk, make_document, make_llm_response


def _make_engine(tenant, analysis_job, project, **overrides):
    """Bypass __init__ and wire up a TopicClusterEngine with mocked deps."""
    eng = TopicClusterEngine.__new__(TopicClusterEngine)
    eng.tenant = tenant
    eng.job = analysis_job
    eng.project = project
    eng.on_progress = None
    eng.llm = MagicMock()
    eng.vec_store = MagicMock()
    eng.config = overrides.get("config", {})
    eng.algorithm = overrides.get("algorithm", "kmeans")
    eng.min_cluster_size = overrides.get("min_cluster_size", 3)
    eng.min_samples = overrides.get("min_samples", 2)
    return eng


def _random_vectors(n, dim=1536):
    vecs = np.random.randn(n, dim).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / (norms + 1e-10)


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestClustering:
    def test_kmeans_creates_clusters(self, tenant, project, connector, analysis_job):
        n = 12
        docs = [make_document(tenant, project, connector, title=f"Doc {i}") for i in range(n)]
        chunks = [make_chunk(tenant, d, 0, f"Content for doc {i}") for i, d in enumerate(docs)]
        chunk_ids = [str(c.id) for c in chunks]
        vectors = _random_vectors(n)

        eng = _make_engine(
            tenant,
            analysis_job,
            project,
            algorithm="kmeans",
            config={"kmeans_k": 3},
            min_cluster_size=2,
        )
        eng.vec_store.get_all_vectors_for_tenant.return_value = [
            (cid, vec) for cid, vec in zip(chunk_ids, vectors)
        ]

        # Mock summaries / taxonomy / tree dependencies
        eng.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response(
                json.dumps(
                    {
                        "label": f"Topic {i}",
                        "summary": f"Summary {i}",
                        "key_concepts": [f"concept_{i}"],
                        "content_purpose": f"Purpose {i}",
                    }
                )
            )
            for i in range(3)
        ]
        eng.llm.chat.return_value = make_llm_response(
            json.dumps({"taxonomy": [{"category": "All", "clusters": [0, 1, 2]}]})
        )
        eng.vec_store.get_chunk_embeddings_batch.return_value = {}

        clusters = eng.run()

        assert len(clusters) == 3
        assert TopicCluster.objects.filter(analysis_job=analysis_job, level=0).count() == 3
        assert ClusterMembership.objects.filter(cluster__analysis_job=analysis_job).count() > 0

    def test_not_enough_chunks_returns_empty(self, tenant, project, analysis_job):
        eng = _make_engine(tenant, analysis_job, project, min_cluster_size=5)
        eng.vec_store.get_all_vectors_for_tenant.return_value = [
            ("c1", np.zeros(1536, dtype=np.float32)),
        ]

        clusters = eng.run()
        assert clusters == []

    def test_2d_projection_shape(self, tenant, project, analysis_job):
        eng = _make_engine(tenant, analysis_job, project)
        vectors = _random_vectors(10, dim=1536)
        coords = eng._project_2d(vectors)
        assert coords.shape == (10, 2)

    def test_2d_projection_small(self, tenant, project, analysis_job):
        eng = _make_engine(tenant, analysis_job, project)
        vectors = _random_vectors(2, dim=1536)
        coords = eng._project_2d(vectors)
        assert coords.shape == (2, 2)
        # Small input returns zeros
        np.testing.assert_array_equal(coords, np.zeros((2, 2)))

    @patch("analysis.clustering.KMeans")
    def test_hdbscan_import_error_falls_back_to_kmeans(
        self, mock_kmeans_cls, tenant, project, analysis_job
    ):
        eng = _make_engine(
            tenant, analysis_job, project, algorithm="hdbscan", config={"kmeans_k": 2}
        )
        vectors = _random_vectors(6, dim=1536)

        mock_kmeans = MagicMock()
        mock_kmeans.fit_predict.return_value = np.array([0, 0, 0, 1, 1, 1])
        mock_kmeans_cls.return_value = mock_kmeans

        with patch.dict("sys.modules", {"hdbscan": None}):
            with patch(
                "builtins.__import__",
                side_effect=lambda name, *a, **kw: (
                    (_ for _ in ()).throw(ImportError("no hdbscan"))
                    if name == "hdbscan"
                    else __builtins__.__import__(name, *a, **kw)
                ),
            ):
                labels = eng._cluster(vectors)

        mock_kmeans_cls.assert_called_once()
        assert len(labels) == 6


# ---------------------------------------------------------------------------
# Generate summaries
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGenerateSummaries:
    def test_summary_from_llm(self, tenant, project, connector, analysis_job):
        cluster = TopicCluster.objects.create(
            tenant=tenant,
            project=project,
            analysis_job=analysis_job,
            label="Placeholder",
            doc_count=2,
            chunk_count=4,
        )
        doc = make_document(tenant, project, connector, title="Doc for summary")
        chunk = make_chunk(tenant, doc, 0, "Some relevant content.")
        ClusterMembership.objects.create(
            tenant=tenant,
            project=project,
            cluster=cluster,
            chunk=chunk,
            document=doc,
            similarity_to_centroid=0.9,
        )

        eng = _make_engine(tenant, analysis_job, project)
        eng.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response(
                json.dumps(
                    {
                        "label": "Security Policies",
                        "summary": "Documents about security.",
                        "key_concepts": ["security", "policy"],
                        "content_purpose": "Define security guidelines",
                    }
                )
            ),
        ]

        eng._generate_summaries([cluster])

        cluster.refresh_from_db()
        assert cluster.label == "Security Policies"
        assert cluster.summary == "Documents about security."
        assert "security" in cluster.key_concepts

    def test_summary_fallback_on_error(self, tenant, project, connector, analysis_job):
        cluster = TopicCluster.objects.create(
            tenant=tenant,
            project=project,
            analysis_job=analysis_job,
            label="Placeholder",
            doc_count=1,
            chunk_count=1,
        )
        doc = make_document(tenant, project, connector, title="Fallback Doc Title")
        chunk = make_chunk(tenant, doc, 0, "Content.")
        ClusterMembership.objects.create(
            tenant=tenant,
            project=project,
            cluster=cluster,
            chunk=chunk,
            document=doc,
            similarity_to_centroid=0.8,
        )

        eng = _make_engine(tenant, analysis_job, project)
        eng.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response("not valid json {{{"),
        ]

        eng._generate_summaries([cluster])

        cluster.refresh_from_db()
        assert "Fallback Doc Title" in cluster.label


# ---------------------------------------------------------------------------
# Generate taxonomy
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGenerateTaxonomy:
    def test_valid_taxonomy_accepted(self, tenant, project, analysis_job):
        clusters = [
            TopicCluster.objects.create(
                tenant=tenant,
                project=project,
                analysis_job=analysis_job,
                label=f"Cluster {i}",
                summary=f"Summary {i}",
                doc_count=5,
                chunk_count=15,
            )
            for i in range(3)
        ]

        eng = _make_engine(tenant, analysis_job, project)
        eng.llm.chat.return_value = make_llm_response(
            json.dumps(
                {
                    "taxonomy": [
                        {"category": "Cat A", "clusters": [0, 1]},
                        {"category": "Cat B", "clusters": [2]},
                    ]
                }
            )
        )

        taxonomy = eng._generate_taxonomy(clusters)

        assert len(taxonomy) == 2
        assigned = set()
        for cat in taxonomy:
            assigned.update(cat["clusters"])
        assert assigned == {0, 1, 2}

    def test_invalid_taxonomy_falls_back(self, tenant, project, analysis_job):
        clusters = [
            TopicCluster.objects.create(
                tenant=tenant,
                project=project,
                analysis_job=analysis_job,
                label=f"Cluster {i}",
                doc_count=5,
                chunk_count=15,
            )
            for i in range(3)
        ]

        eng = _make_engine(tenant, analysis_job, project)
        # Missing cluster index 2 → invalid
        eng.llm.chat.return_value = make_llm_response(
            json.dumps({"taxonomy": [{"category": "Cat A", "clusters": [0, 1]}]})
        )

        taxonomy = eng._generate_taxonomy(clusters)

        # Should fall back to single flat category
        assert len(taxonomy) == 1
        assert taxonomy[0]["clusters"] == [0, 1, 2]

    def test_single_cluster_returns_flat(self, tenant, project, analysis_job):
        cluster = TopicCluster.objects.create(
            tenant=tenant,
            project=project,
            analysis_job=analysis_job,
            label="Only One",
            doc_count=5,
            chunk_count=15,
        )

        eng = _make_engine(tenant, analysis_job, project)
        taxonomy = eng._generate_taxonomy([cluster])

        assert len(taxonomy) == 1
        assert taxonomy[0]["category"] == "Only One"
        assert taxonomy[0]["clusters"] == [0]


# ---------------------------------------------------------------------------
# Build tree
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildTree:
    def test_tree_nodes_created(self, tenant, project, connector, analysis_job):
        clusters = []
        for i in range(2):
            c = TopicCluster.objects.create(
                tenant=tenant,
                project=project,
                analysis_job=analysis_job,
                label=f"Cluster {i}",
                doc_count=2,
                chunk_count=4,
            )
            for j in range(2):
                doc = make_document(tenant, project, connector, title=f"Doc {i}-{j}")
                chunk = make_chunk(tenant, doc, 0, f"Content {i}-{j}")
                ClusterMembership.objects.create(
                    tenant=tenant,
                    project=project,
                    cluster=c,
                    chunk=chunk,
                    document=doc,
                )
            clusters.append(c)

        taxonomy = [
            {"category": "Category A", "clusters": [0]},
            {"category": "Category B", "clusters": [1]},
        ]

        eng = _make_engine(tenant, analysis_job, project)
        eng._build_tree(clusters, taxonomy)

        # 2 category nodes + 2 cluster nodes + 4 document nodes = 8
        tree_nodes = TreeNode.objects.filter(analysis_job=analysis_job)
        category_nodes = tree_nodes.filter(node_type="category")
        cluster_nodes = tree_nodes.filter(node_type="cluster")
        doc_nodes = tree_nodes.filter(node_type="document")

        assert category_nodes.count() == 2
        assert cluster_nodes.count() == 2
        assert doc_nodes.count() == 4

    def test_single_category_skips_level(self, tenant, project, connector, analysis_job):
        cluster = TopicCluster.objects.create(
            tenant=tenant,
            project=project,
            analysis_job=analysis_job,
            label="Only Cluster",
            doc_count=1,
            chunk_count=1,
        )
        doc = make_document(tenant, project, connector, title="Single Doc")
        chunk = make_chunk(tenant, doc, 0, "Single content.")
        ClusterMembership.objects.create(
            tenant=tenant,
            project=project,
            cluster=cluster,
            chunk=chunk,
            document=doc,
        )

        taxonomy = [{"category": "All", "clusters": [0]}]
        eng = _make_engine(tenant, analysis_job, project)
        eng._build_tree([cluster], taxonomy)

        tree_nodes = TreeNode.objects.filter(analysis_job=analysis_job)
        assert tree_nodes.filter(node_type="category").count() == 0
        cluster_node = tree_nodes.get(node_type="cluster")
        assert cluster_node.level == 0
        assert cluster_node.parent is None

    def test_subclusters_in_tree(self, tenant, project, connector, analysis_job):
        parent_cluster = TopicCluster.objects.create(
            tenant=tenant,
            project=project,
            analysis_job=analysis_job,
            label="Parent Cluster",
            doc_count=4,
            chunk_count=8,
        )
        subcluster = TopicCluster.objects.create(
            tenant=tenant,
            project=project,
            analysis_job=analysis_job,
            parent=parent_cluster,
            label="Sub Cluster",
            level=1,
            doc_count=2,
            chunk_count=4,
        )

        doc = make_document(tenant, project, connector, title="Sub Doc")
        chunk = make_chunk(tenant, doc, 0, "Sub content.")
        ClusterMembership.objects.create(
            tenant=tenant,
            project=project,
            cluster=subcluster,
            chunk=chunk,
            document=doc,
        )
        # Also add membership to parent so it shows up in the mapping
        ClusterMembership.objects.create(
            tenant=tenant,
            project=project,
            cluster=parent_cluster,
            chunk=chunk,
            document=doc,
        )

        taxonomy = [{"category": "All", "clusters": [0]}]
        eng = _make_engine(tenant, analysis_job, project)
        eng._build_tree([parent_cluster], taxonomy)

        tree_nodes = TreeNode.objects.filter(analysis_job=analysis_job)
        sc_nodes = tree_nodes.filter(node_type="subcluster")
        assert sc_nodes.count() == 1
        assert sc_nodes.first().parent.node_type == "cluster"
