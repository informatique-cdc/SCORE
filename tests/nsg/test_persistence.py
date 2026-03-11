"""Tests for save / load round-trip."""

import pytest

from nsg.config import NSGConfig
from nsg.graph import NeuralSemanticGraph
from nsg import persistence


SAMPLE_TEXT = (
    "Quantum computing leverages quantum mechanics to process information. "
    "Qubits can exist in superposition, enabling parallel computation."
)


class TestPersistence:
    def test_round_trip(self, tmp_path: "pytest.TempPathFactory") -> None:
        # Build a graph.
        nsg = NeuralSemanticGraph(config=NSGConfig())
        nsg.add_document("doc_q", SAMPLE_TEXT)
        nsg.build_or_update_index()
        original_nodes = set(nsg.graph.nodes)
        original_edges = nsg.graph.number_of_edges()

        # Save.
        out_dir = tmp_path / "nsg_test"
        persistence.save(nsg, out_dir)

        # Load.
        loaded = persistence.load(out_dir)
        assert set(loaded.graph.nodes) == original_nodes
        assert loaded.graph.number_of_edges() == original_edges

    def test_loaded_graph_is_queryable(self, tmp_path: "pytest.TempPathFactory") -> None:
        nsg = NeuralSemanticGraph(config=NSGConfig())
        nsg.add_document("doc_q", SAMPLE_TEXT)
        nsg.build_or_update_index()

        out_dir = tmp_path / "nsg_test2"
        persistence.save(nsg, out_dir)

        loaded = persistence.load(out_dir)
        result = loaded.query_subgraph("quantum computing")
        assert len(result["nodes"]) > 0
