"""
Triple extraction, one TopicCluster at a time.

Measured on the reference corpus, extracting inside a thematically homogeneous
cluster raises the largest connected component from 46% to 73% of nodes versus
sampling the whole project: a cluster shares vocabulary, so entities recur.

All clusters are batched into a single LLM call so concurrency is maximal and
progress is reported as one stream — same approach as ClaimsExtractor.
"""

import json
import logging
import re
from dataclasses import dataclass, field

from nsg.stopwords import STOPWORDS_ALL

logger = logging.getLogger(__name__)


@dataclass
class Triple:
    subject: str
    predicate: str
    object: str
    cluster_id: str
    chunk_id: str
    document_id: str
    document_title: str = ""
    claim_id: str | None = None
    qualifiers: dict = field(default_factory=dict)


def limit_predicate(predicate: str, max_words: int = 3) -> str:
    """Cap a predicate at *max_words*, dropping a trailing stopword."""
    words = predicate.split()
    if len(words) <= max_words:
        return predicate.strip()
    kept = words[:max_words]
    if len(kept) > 1 and kept[-1].lower() in STOPWORDS_ALL:
        kept = kept[:-1]
    return " ".join(kept)


def normalize(term: str) -> str:
    """Canonical form used to match entity variants."""
    words = [w for w in re.findall(r"\w+", term.lower(), re.UNICODE) if w not in STOPWORDS_ALL]
    return " ".join(words)


def _clean(value, max_len: int) -> str:
    return str(value or "").strip()[:max_len]


def extract_triples(llm, clusters, config, on_progress=None) -> list[Triple]:
    """Extract triples for every cluster. Returns a flat list tagged by cluster."""
    source = config.get("source", "extract")
    max_words = config.get("max_predicate_words", 3)

    if source == "claims":
        triples = _from_claims(clusters, max_words)
    else:
        triples = _from_llm(llm, clusters, config, max_words, on_progress)
        if source == "hybrid":
            triples += _from_claims(clusters, max_words)

    logger.info("[kg] %d triples extracted from %d clusters", len(triples), len(clusters))
    return triples


def _cluster_chunks(cluster, limit=None):
    from analysis.models import ClusterMembership
    from ingestion.models import DocumentChunk

    chunk_ids = ClusterMembership.objects.filter(cluster=cluster).values_list("chunk_id", flat=True)
    qs = (
        DocumentChunk.objects.filter(id__in=list(chunk_ids))
        .select_related("document")
        .order_by("document_id", "chunk_index")
    )
    return list(qs[:limit] if limit else qs)


def _from_llm(llm, clusters, config, max_words, on_progress) -> list[Triple]:
    from llm.prompt_loader import get_prompt

    extraction_cfg = config.get("extraction", {})
    max_chars = extraction_cfg.get("chunk_max_chars", 1200)
    max_triples = extraction_cfg.get("max_triples_per_chunk", 20)
    per_cluster = extraction_cfg.get("max_chunks_per_cluster")

    template = get_prompt("KG_TRIPLE_EXTRACTION")

    prompts: list[str] = []
    origins: list[tuple] = []
    for cluster in clusters:
        for chunk in _cluster_chunks(cluster, per_cluster):
            prompts.append(template.format(text=chunk.content[:max_chars], max_triples=max_triples))
            origins.append((cluster, chunk))

    if not prompts:
        return []

    logger.info("[kg] Extracting triples from %d chunks (LLM batch)...", len(prompts))
    responses = llm.chat_batch_or_concurrent(prompts, json_mode=True, on_progress=on_progress)

    triples: list[Triple] = []
    failed = 0
    for (cluster, chunk), resp in zip(origins, responses):
        if not resp:
            failed += 1
            continue
        try:
            raw = json.loads(resp.content).get("triples", [])
        except (json.JSONDecodeError, AttributeError, TypeError):
            failed += 1
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            subject = _clean(item.get("subject"), 300)
            predicate = _clean(item.get("predicate"), 120)
            obj = _clean(item.get("object"), 300)
            if not (subject and predicate and obj):
                continue
            triples.append(
                Triple(
                    subject=subject,
                    predicate=limit_predicate(predicate, max_words),
                    object=obj,
                    cluster_id=str(cluster.id),
                    chunk_id=str(chunk.id),
                    document_id=str(chunk.document_id),
                    document_title=chunk.document.title or "",
                )
            )

    if failed:
        logger.warning("[kg] %d/%d chunks yielded no usable triples", failed, len(prompts))
    return triples


def _from_claims(clusters, max_words) -> list[Triple]:
    """Reuse existing Claim rows. Sparse on its own — see the plan's D1."""
    from analysis.models import Claim, ClusterMembership

    triples: list[Triple] = []
    for cluster in clusters:
        chunk_ids = set(
            ClusterMembership.objects.filter(cluster=cluster).values_list("chunk_id", flat=True)
        )
        if not chunk_ids:
            continue
        claims = Claim.objects.filter(chunk_id__in=chunk_ids).select_related("document")
        for claim in claims:
            subject = _clean(claim.subject, 300)
            predicate = _clean(claim.predicate, 120)
            obj = _clean(claim.object_value, 300)
            if not (subject and predicate and obj):
                continue
            triples.append(
                Triple(
                    subject=subject,
                    predicate=limit_predicate(predicate, max_words),
                    object=obj,
                    cluster_id=str(cluster.id),
                    chunk_id=str(claim.chunk_id),
                    document_id=str(claim.document_id),
                    document_title=claim.document.title or "",
                    claim_id=str(claim.id),
                    qualifiers=claim.qualifiers or {},
                )
            )
    return triples
