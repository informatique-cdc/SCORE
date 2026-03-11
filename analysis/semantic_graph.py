"""
Adapter between DocuScore and the Neural Semantic Graph (NSG) library.

Builds, persists, and loads a concept-level knowledge graph from project
documents and claims, using the project's configured LLM embedding provider.
"""
import json
import logging
from pathlib import Path

import numpy as np
from django.conf import settings

from analysis.models import Claim
from ingestion.models import Document, DocumentChunk
from llm.client import get_llm_client

from nsg.config import NSGConfig
from nsg.graph import NeuralSemanticGraph

logger = logging.getLogger(__name__)


def graph_dir(project_id: str) -> Path:
    return Path(settings.MEDIA_ROOT) / "graphs" / project_id


class ProjectGraphBuilder:
    """Build a NeuralSemanticGraph from a project's chunks and claims."""

    def __init__(self, tenant, analysis_job, project):
        self.tenant = tenant
        self.job = analysis_job
        self.project = project
        self.llm = get_llm_client()
        self.sg_config = settings.SEMANTIC_GRAPH_CONFIG

    def run(self):
        """Build the graph and persist it. Returns the NSG instance."""
        nsg_config = NSGConfig(
            chunk_max_chars=self.sg_config.get("chunk_max_chars", 800),
            top_k=self.sg_config.get("top_k", 12),
            hops=self.sg_config.get("hops", 2),
            max_nodes=self.sg_config.get("max_nodes", 80),
            evidence_cap=self.sg_config.get("evidence_cap", 5),
            spacy_model=self.sg_config.get("spacy_model", "fr_core_news_sm"),
        )

        def embed_fn(texts: list[str]) -> np.ndarray:
            vecs = self.llm.embed(texts)
            return np.asarray(vecs, dtype=np.float32)

        nsg = NeuralSemanticGraph(config=nsg_config, embed_fn=embed_fn, lazy_embed=True)

        # Feed document chunks
        docs = list(Document.objects.filter(
            project=self.project, status=Document.Status.READY,
        ))
        logger.info("[semantic_graph] Step 1/4: Feeding %d documents...", len(docs))
        for doc_idx, doc in enumerate(docs):
            if doc_idx % 50 == 0 and doc_idx > 0:
                logger.info("[semantic_graph] Fed %d/%d documents", doc_idx, len(docs))
            chunks = DocumentChunk.objects.filter(document=doc).order_by("chunk_index")
            full_text = "\n\n".join(c.content for c in chunks)
            if full_text.strip():
                nsg.add_document(str(doc.id), full_text)
        logger.info("[semantic_graph] Step 1/4 done: %d documents fed", len(docs))

        # Feed claims as additional concept sources
        claims = list(Claim.objects.filter(project=self.project))
        logger.info("[semantic_graph] Step 2/4: Feeding %d claims...", len(claims))
        for claim_idx, claim in enumerate(claims):
            if claim_idx % 200 == 0 and claim_idx > 0:
                logger.info("[semantic_graph] Fed %d/%d claims", claim_idx, len(claims))
            claim_text = claim.as_text
            if claim_text.strip():
                nsg.add_document(f"claim-{claim.id}", claim_text)
        logger.info("[semantic_graph] Step 2/4 done: %d claims fed", len(claims))

        # Batch-embed all concepts in one call
        logger.info("[semantic_graph] Step 3/4: Batch embedding concepts...")
        n_embedded = nsg.embed_all_missing()
        logger.info("[semantic_graph] Step 3/4 done: Embedded %d concepts in batch", n_embedded)

        logger.info("[semantic_graph] Step 4/4: Building index...")
        nsg.build_or_update_index()
        logger.info("[semantic_graph] Step 4/4 done")

        self._save_graph(nsg)
        logger.info(
            "Semantic graph built: %d nodes, %d edges for project %s",
            nsg.graph.number_of_nodes(),
            nsg.graph.number_of_edges(),
            self.project.id,
        )
        return nsg

    def _save_graph(self, nsg: "NeuralSemanticGraph") -> None:
        """Persist graph + embeddings to disk."""
        out_dir = graph_dir(str(self.project.id))
        out_dir.mkdir(parents=True, exist_ok=True)

        # Save graph as node-link JSON
        import networkx as nx

        data = nx.node_link_data(nsg.graph)
        with open(out_dir / "graph.json", "w") as f:
            json.dump(data, f)

        # Save embeddings as npz
        if nsg.embeddings:
            concepts = list(nsg.embeddings.keys())
            vectors = np.stack([nsg.embeddings[c] for c in concepts])
            np.savez_compressed(
                out_dir / "embeddings.npz",
                concepts=np.array(concepts, dtype=object),
                vectors=vectors,
            )


def load_graph(project_id: str):
    """Load a previously built graph from disk. Returns None if not found."""
    import networkx as nx

    gdir = graph_dir(project_id)
    graph_path = gdir / "graph.json"
    embed_path = gdir / "embeddings.npz"

    if not graph_path.exists():
        return None

    sg_config = settings.SEMANTIC_GRAPH_CONFIG
    nsg_config = NSGConfig(
        chunk_max_chars=sg_config.get("chunk_max_chars", 800),
        top_k=sg_config.get("top_k", 12),
        hops=sg_config.get("hops", 2),
        max_nodes=sg_config.get("max_nodes", 80),
        evidence_cap=sg_config.get("evidence_cap", 5),
        spacy_model=sg_config.get("spacy_model", "fr_core_news_sm"),
    )

    # Use LLM embeddings for query-time operations
    llm = get_llm_client()

    def embed_fn(texts: list[str]) -> np.ndarray:
        vecs = llm.embed(texts)
        return np.asarray(vecs, dtype=np.float32)

    nsg = NeuralSemanticGraph(config=nsg_config, embed_fn=embed_fn)

    with open(graph_path) as f:
        data = json.load(f)
    nsg.graph = nx.node_link_graph(data)

    if embed_path.exists():
        npz = np.load(embed_path, allow_pickle=True)
        concepts = npz["concepts"]
        vectors = npz["vectors"]
        for concept, vec in zip(concepts, vectors):
            nsg.embeddings[str(concept)] = vec

    nsg.build_or_update_index()
    return nsg
