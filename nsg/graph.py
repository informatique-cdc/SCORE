"""Core Neural Semantic Graph implementation."""

from __future__ import annotations

import heapq
import logging
from datetime import datetime, timezone
from itertools import combinations
from collections.abc import Callable
from typing import Any

import networkx as nx
import numpy as np

from nsg.concepts import chunk_text, extract_concepts
from nsg.config import NSGConfig
from nsg.index import BaseConceptIndex, make_concept_index

logger = logging.getLogger(__name__)


class NeuralSemanticGraph:
    """A concept-level knowledge graph with vector-powered retrieval.

    Each *node* is a normalised concept string.
    Each *edge* carries a ``relation_type``, ``weight``, ``doc_id`` provenance,
    and a capped list of ``evidence`` snippets.
    """

    def __init__(
        self,
        config: NSGConfig | None = None,
        embed_fn: Callable[[list[str]], np.ndarray] | None = None,
        lazy_embed: bool = False,
    ) -> None:
        self.config = config or NSGConfig()
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self.embeddings: dict[str, np.ndarray] = {}
        self._embed_fn = embed_fn
        self._lazy_embed = lazy_embed
        self._model = None
        self._index: BaseConceptIndex = make_concept_index()

    # -- lazy model loader -----------------------------------------

    @property
    def model(self):
        """Lazy-load sentence-transformers only when no external embed_fn is provided."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.config.embedding_model)
        return self._model

    # -- embedding helpers -----------------------------------------

    def _embed(self, texts: str | list[str]) -> np.ndarray:
        """Return L2-normalised embeddings for *texts*."""
        if isinstance(texts, str):
            texts = [texts]
        if self._embed_fn is not None:
            vecs = self._embed_fn(texts)
            return np.asarray(vecs, dtype=np.float32)
        vecs = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)

    def _embed_concept(self, concept: str) -> np.ndarray:
        """Return (possibly cached) embedding for a single concept."""
        if concept not in self.embeddings:
            self.embeddings[concept] = self._embed(concept)[0]
        return self.embeddings[concept]

    def embed_all_missing(self) -> int:
        """Batch-embed all graph nodes that don't have an embedding yet."""
        missing = [n for n in self.graph.nodes() if n not in self.embeddings]
        if not missing:
            return 0
        vecs = self._embed(missing)
        for concept, vec in zip(missing, vecs):
            self.embeddings[concept] = vec
        return len(missing)

    # -- public API ------------------------------------------------

    def add_document(self, doc_id: str, text: str) -> None:
        """Ingest a document: extract concepts, build a subgraph, merge."""
        subgraph = self.build_document_subgraph(doc_id, text)
        self.merge_subgraph(subgraph)

    def build_document_subgraph(self, doc_id: str, text: str) -> nx.MultiDiGraph:
        """Create an isolated subgraph for one document."""
        sub = nx.MultiDiGraph()
        chunks = chunk_text(text, max_chars=self.config.chunk_max_chars)

        for chunk in chunks:
            concepts = extract_concepts(chunk, spacy_model=self.config.spacy_model)
            if not concepts:
                continue

            # Ensure nodes exist in the subgraph.
            for c in concepts:
                if sub.has_node(c):
                    sub.nodes[c]["frequency"] += 1
                else:
                    sub.add_node(
                        c,
                        concept=c,
                        frequency=1,
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )

            # Add co-occurrence edges for every pair within the chunk.
            for src, dst in combinations(concepts, 2):
                _add_or_update_edge(sub, src, dst, "co_occurs", doc_id, chunk, self.config.evidence_cap)

        return sub

    def merge_subgraph(self, subgraph: nx.MultiDiGraph) -> None:
        """Merge *subgraph* into the global graph.

        * Existing nodes get their ``frequency`` incremented.
        * Existing edges (same src, dst, relation_type) get their ``weight``
          bumped and evidence appended (up to the cap).
        """
        for node, data in subgraph.nodes(data=True):
            if self.graph.has_node(node):
                self.graph.nodes[node]["frequency"] += data.get("frequency", 1)
            else:
                self.graph.add_node(node, **data)

            # Ensure we have an embedding for every node.
            if not self._lazy_embed:
                self._embed_concept(node)

        for src, dst, edata in subgraph.edges(data=True):
            _merge_edge(self.graph, src, dst, edata, self.config.evidence_cap)

    def build_or_update_index(self) -> None:
        """(Re)build the FAISS / brute-force vector index from current embeddings."""
        self._index = make_concept_index()
        for concept, vec in self.embeddings.items():
            self._index.add(concept, vec)
        self._index.build()

    # -- query -----------------------------------------------------

    def query_subgraph(
        self,
        query: str,
        top_k: int | None = None,
        hops: int | None = None,
        max_nodes: int | None = None,
    ) -> dict[str, Any]:
        """Retrieve an induced subgraph relevant to *query*.

        Returns a JSON-serialisable dict with ``query``, ``seeds``,
        ``nodes``, and ``edges``.
        """
        top_k = top_k or self.config.top_k
        hops = hops or self.config.hops
        max_nodes = max_nodes or self.config.max_nodes

        # Rebuild index to be sure it's current.
        self.build_or_update_index()

        query_vec = self._embed(query)[0]
        seed_results = self._index.search(query_vec, top_k=top_k)

        # Only keep seeds that are actually in the graph.
        seeds: list[tuple[str, float]] = [
            (c, s) for c, s in seed_results if self.graph.has_node(c)
        ]

        # Weighted BFS expansion.
        visited: set[str] = set()
        # Priority queue: (-weight, node) so highest-weight nodes expand first.
        heap: list[tuple[float, str]] = []

        for concept, score in seeds:
            visited.add(concept)
            heapq.heappush(heap, (-score, concept))

        for _ in range(hops):
            next_frontier: list[tuple[float, str]] = []
            while heap:
                neg_w, node = heapq.heappop(heap)
                for _, nbr, edata in self.graph.edges(node, data=True):
                    if nbr in visited:
                        continue
                    visited.add(nbr)
                    w = edata.get("weight", 1.0)
                    next_frontier.append((-w, nbr))
                    if len(visited) >= max_nodes:
                        break
                if len(visited) >= max_nodes:
                    break
            for item in next_frontier:
                heapq.heappush(heap, item)
            if len(visited) >= max_nodes:
                break

        # Build the induced subgraph.
        sg = self.graph.subgraph(visited).copy()

        # Serialise.
        nodes_out: list[dict[str, Any]] = []
        for n, d in sg.nodes(data=True):
            nodes_out.append({"id": n, "frequency": d.get("frequency", 1)})

        edges_out: list[dict[str, Any]] = []
        for s, t, d in sg.edges(data=True):
            edges_out.append({
                "source": s,
                "target": t,
                "relation_type": d.get("relation_type", "co_occurs"),
                "weight": d.get("weight", 1.0),
                "doc_id": d.get("doc_id", ""),
                "evidence": d.get("evidence", []),
            })

        return {
            "query": query,
            "seeds": [{"concept": c, "score": round(s, 4)} for c, s in seeds],
            "nodes": nodes_out,
            "edges": edges_out,
        }


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _add_or_update_edge(
    g: nx.MultiDiGraph,
    src: str,
    dst: str,
    relation_type: str,
    doc_id: str,
    evidence_snippet: str,
    evidence_cap: int,
) -> None:
    """Add an edge or increment weight / append evidence on an existing one."""
    # Check if an edge with this relation_type already exists.
    for key, edata in g.get_edge_data(src, dst, default={}).items():
        if edata.get("relation_type") == relation_type:
            edata["weight"] += 1.0
            ev = edata.setdefault("evidence", [])
            if len(ev) < evidence_cap:
                ev.append(evidence_snippet)
            return

    g.add_edge(
        src,
        dst,
        relation_type=relation_type,
        weight=1.0,
        doc_id=doc_id,
        evidence=[evidence_snippet],
    )


def _merge_edge(
    g: nx.MultiDiGraph,
    src: str,
    dst: str,
    new_data: dict[str, Any],
    evidence_cap: int,
) -> None:
    """Merge a single edge into *g*, updating weight & evidence if it exists."""
    rel = new_data.get("relation_type", "co_occurs")
    for key, edata in g.get_edge_data(src, dst, default={}).items():
        if edata.get("relation_type") == rel:
            edata["weight"] += new_data.get("weight", 1.0)
            existing_ev = edata.setdefault("evidence", [])
            for snippet in new_data.get("evidence", []):
                if len(existing_ev) < evidence_cap:
                    existing_ev.append(snippet)
            return

    g.add_edge(src, dst, **new_data)
