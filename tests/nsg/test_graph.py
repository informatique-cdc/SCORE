"""Tests for the NeuralSemanticGraph core."""

import pytest

from nsg.config import NSGConfig
from nsg.graph import NeuralSemanticGraph


@pytest.fixture
def nsg() -> NeuralSemanticGraph:
    return NeuralSemanticGraph(config=NSGConfig())


SAMPLE_TEXT = (
    "Machine learning is a branch of artificial intelligence. "
    "Deep learning, a subset of machine learning, uses neural networks. "
    "Natural language processing enables computers to understand human language."
)


class TestBuildDocumentSubgraph:
    def test_subgraph_has_nodes_and_edges(self, nsg: NeuralSemanticGraph) -> None:
        sub = nsg.build_document_subgraph("doc1", SAMPLE_TEXT)
        assert sub.number_of_nodes() > 0
        assert sub.number_of_edges() > 0

    def test_node_has_expected_attributes(self, nsg: NeuralSemanticGraph) -> None:
        sub = nsg.build_document_subgraph("doc1", SAMPLE_TEXT)
        for _, data in sub.nodes(data=True):
            assert "concept" in data
            assert "frequency" in data
            assert "created_at" in data

    def test_edge_has_expected_attributes(self, nsg: NeuralSemanticGraph) -> None:
        sub = nsg.build_document_subgraph("doc1", SAMPLE_TEXT)
        for _, _, data in sub.edges(data=True):
            assert "relation_type" in data
            assert "weight" in data
            assert "doc_id" in data
            assert "evidence" in data


class TestMerge:
    def test_merge_increases_frequency(self, nsg: NeuralSemanticGraph) -> None:
        nsg.add_document("doc1", SAMPLE_TEXT)
        freqs_before = {n: d["frequency"] for n, d in nsg.graph.nodes(data=True)}

        # Re-index the same document.
        nsg.add_document("doc2", SAMPLE_TEXT)
        for n, d in nsg.graph.nodes(data=True):
            assert d["frequency"] >= freqs_before.get(n, 0)

    def test_merge_adds_new_nodes(self, nsg: NeuralSemanticGraph) -> None:
        nsg.add_document("doc1", "Python is a programming language.")
        nodes_before = set(nsg.graph.nodes)
        nsg.add_document("doc2", "Rust is a systems programming language.")
        nodes_after = set(nsg.graph.nodes)
        assert nodes_after >= nodes_before  # superset or equal


FRENCH_TEXT = (
    "Le système de gestion des données permet un traitement efficace. "
    "Les bases de données relationnelles stockent les informations structurées. "
    "La sécurité des données est une priorité pour les entreprises."
)


class TestNoStopwordNodes:
    def test_graph_excludes_french_stopwords(self, nsg: NeuralSemanticGraph) -> None:
        from nsg.stopwords import STOPWORDS_FR
        nsg.add_document("doc_fr", FRENCH_TEXT)
        for node in nsg.graph.nodes:
            assert node not in STOPWORDS_FR, (
                f"Stopword {node!r} found as graph node"
            )

    def test_graph_excludes_english_stopwords(self, nsg: NeuralSemanticGraph) -> None:
        from nsg.stopwords import STOPWORDS_EN
        nsg.add_document("doc1", SAMPLE_TEXT)
        for node in nsg.graph.nodes:
            assert node not in STOPWORDS_EN, (
                f"Stopword {node!r} found as graph node"
            )


class TestQuery:
    def test_query_returns_nonempty_subgraph(self, nsg: NeuralSemanticGraph) -> None:
        nsg.add_document("doc1", SAMPLE_TEXT)
        nsg.build_or_update_index()
        result = nsg.query_subgraph("What is deep learning?")
        assert len(result["seeds"]) > 0
        assert len(result["nodes"]) > 0

    def test_query_output_format(self, nsg: NeuralSemanticGraph) -> None:
        nsg.add_document("doc1", SAMPLE_TEXT)
        nsg.build_or_update_index()
        result = nsg.query_subgraph("artificial intelligence")
        assert "query" in result
        assert "seeds" in result
        assert "nodes" in result
        assert "edges" in result

    def test_query_on_empty_graph_returns_empty(self, nsg: NeuralSemanticGraph) -> None:
        result = nsg.query_subgraph("anything")
        assert result["seeds"] == []
        assert result["nodes"] == []
        assert result["edges"] == []
