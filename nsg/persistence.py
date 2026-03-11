"""Save / load the graph, embeddings, and FAISS index to disk."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx
import numpy as np

if TYPE_CHECKING:
    from nsg.graph import NeuralSemanticGraph

logger = logging.getLogger(__name__)


def save(nsg: "NeuralSemanticGraph", base_path: str | Path) -> None:
    """Persist a :class:`NeuralSemanticGraph` to a directory.

    Outputs:
        ``<base>/graph.json``   — networkx graph as node-link JSON
        ``<base>/vectors.npy``  — stacked embedding matrix
        ``<base>/concepts.json``— ordered concept list matching the rows
        ``<base>/faiss.index``  — FAISS index (if available)
    """
    base = Path(base_path)
    base.mkdir(parents=True, exist_ok=True)

    # 1. Graph (networkx → node-link JSON, safe serialization)
    graph_data = nx.node_link_data(nsg.graph)
    with open(base / "graph.json", "w") as f:
        json.dump(graph_data, f, default=str)

    # 2. Embeddings
    concepts = list(nsg.embeddings.keys())
    if concepts:
        matrix = np.vstack([nsg.embeddings[c] for c in concepts])
        np.save(base / "vectors.npy", matrix)
    else:
        np.save(base / "vectors.npy", np.empty((0,)))

    with open(base / "concepts.json", "w") as f:
        json.dump(concepts, f)

    # 3. FAISS index (best-effort)
    try:
        import faiss as _faiss

        idx = getattr(nsg._index, "_index", None)
        if idx is not None:
            _faiss.write_index(idx, str(base / "faiss.index"))
    except (ImportError, AttributeError, Exception) as exc:
        logger.debug("Skipping FAISS index save: %s", exc)

    # 4. Config
    with open(base / "config.json", "w") as f:
        json.dump(
            {
                "embedding_model": nsg.config.embedding_model,
                "chunk_max_chars": nsg.config.chunk_max_chars,
                "top_k": nsg.config.top_k,
                "hops": nsg.config.hops,
                "max_nodes": nsg.config.max_nodes,
                "evidence_cap": nsg.config.evidence_cap,
                "synonym_threshold": nsg.config.synonym_threshold,
                "spacy_model": nsg.config.spacy_model,
            },
            f,
            indent=2,
        )

    logger.info(
        "Saved NSG to %s (%d nodes, %d edges)",
        base,
        nsg.graph.number_of_nodes(),
        nsg.graph.number_of_edges(),
    )


def load(base_path: str | Path) -> "NeuralSemanticGraph":
    """Reconstruct a :class:`NeuralSemanticGraph` from files on disk."""
    from nsg.config import NSGConfig
    from nsg.graph import NeuralSemanticGraph

    base = Path(base_path)

    # Config
    config_path = base / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            cfg_dict = json.load(f)
        config = NSGConfig(**cfg_dict)
    else:
        config = NSGConfig()

    nsg = NeuralSemanticGraph(config=config)

    # Graph (node-link JSON → networkx, with legacy pickle fallback)
    json_path = base / "graph.json"
    pkl_path = base / "graph.pkl"
    if json_path.exists():
        with open(json_path) as f:
            graph_data = json.load(f)
        nsg.graph = nx.node_link_graph(graph_data, directed=True, multigraph=True)
    elif pkl_path.exists():
        import pickle

        logger.warning(
            "Loading legacy pickle graph from %s — re-save to migrate to JSON.",
            pkl_path,
        )
        with open(pkl_path, "rb") as f:
            nsg.graph = pickle.load(f)  # noqa: S301 — legacy migration only
    else:
        raise FileNotFoundError(f"No graph file found in {base}")

    # Embeddings
    concepts: list[str] = []
    concepts_path = base / "concepts.json"
    if concepts_path.exists():
        with open(concepts_path) as f:
            concepts = json.load(f)

    vectors_path = base / "vectors.npy"
    if vectors_path.exists() and concepts:
        matrix = np.load(vectors_path)
        for concept, vec in zip(concepts, matrix):
            nsg.embeddings[concept] = vec

    # Rebuild the vector index from stored embeddings.
    nsg.build_or_update_index()

    logger.info(
        "Loaded NSG from %s (%d nodes, %d edges)",
        base,
        nsg.graph.number_of_nodes(),
        nsg.graph.number_of_edges(),
    )
    return nsg
