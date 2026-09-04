"""
Relationship inference.

Ported from robert-mcdermott/ai-knowledge-graph (Apache 2.0),
src/knowledge_graph/entity_standardization.py, with four departures:

  - Cluster boundaries replace Louvain communities. Clusters are semantic and
    carry a label, so the LLM is told what each side is *about* rather than
    being handed two anonymous entity lists.
  - The prompt permits an empty answer. The reference prompt asks the model to
    "infer 2-3 plausible relationships", which forces invention whenever no
    relation exists — its own README shows the result: a dominant "related to"
    predicate and edges like "advances via Artificial Intelligence".
  - The two mechanical rules — transitive and lexical similarity — are off by
    default. Measured on the reference corpus, transitive inference doubled the
    average degree (2.74 to 5.35) while leaving the component count unchanged at
    39: it thickens the graph under a single vague predicate without adding any
    structure. Kept because another corpus may behave differently, gated because
    this one does not.
  - Every inferred edge is tagged with its inference_kind so the UI can hide it,
    and inferred edges never overwrite an observed one.

What does work here is the LLM pass: 80 edges, but they cut the graph from 44 to
39 components and lift the largest one from 84% to 87% of nodes.
"""

import collections
import json
import logging

import networkx as nx

from analysis.kg.extraction import limit_predicate

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.7


def infer(entities, edges, clusters, llm, config, on_progress=None) -> list[dict]:
    """Return new inferred edges. Never mutates *edges*."""
    cfg = config.get("inference", {})
    if not cfg.get("enabled", True):
        return []

    max_words = config.get("max_predicate_words", 3)
    known = {(e["subject"], e["object"]) for e in edges}
    known |= {(e["object"], e["subject"]) for e in edges}

    graph = nx.Graph()
    graph.add_nodes_from(entities)
    graph.add_edges_from((e["subject"], e["object"]) for e in edges if e["subject"] != e["object"])

    inferred: list[dict] = []

    if cfg.get("transitive", False):
        found = _transitive(graph, known, max_words)
        inferred += found
        logger.info("[kg] Inference: %d transitive relations", len(found))

    if cfg.get("cross_cluster", True):
        found = _cross_cluster(entities, edges, clusters, known, llm, max_words, on_progress)
        inferred += found
        logger.info("[kg] Inference: %d cross-cluster relations", len(found))

    if cfg.get("within_cluster", True):
        found = _within_cluster(entities, edges, graph, clusters, known, llm, max_words)
        inferred += found
        logger.info("[kg] Inference: %d within-cluster relations", len(found))

    if cfg.get("lexical_similarity", False):
        found = _lexical(entities, known, max_words)
        inferred += found
        logger.info("[kg] Inference: %d lexical relations", len(found))

    return _dedupe(inferred)


# ----------------------------------------------------------------------------
# Rule-based
# ----------------------------------------------------------------------------


def _transitive(graph, known, max_words):
    """A—B, B—C and A,C unconnected: A and C share a documented intermediary.

    Only middles with 2 to 6 neighbours qualify. A hub like "Collaborateur"
    connects dozens of unrelated entities, and pairing them all would assert
    links that the corpus never supports.
    """
    out = []
    for middle in graph.nodes():
        neighbours = list(graph.neighbors(middle))
        if not 2 <= len(neighbours) <= 6:
            continue
        for i, a in enumerate(neighbours):
            for b in neighbours[i + 1 :]:
                if (a, b) in known or a == b:
                    continue
                known.add((a, b))
                known.add((b, a))
                out.append(_edge(a, b, limit_predicate("associé à", max_words), "transitive", 0.5))
    return out


def _lexical(entities, known, max_words):
    """Shared head word. Off by default: this is what floods the reference graph."""
    out = []
    by_token = collections.defaultdict(list)
    for entity in entities:
        tokens = entity.split()
        if len(tokens) > 1:
            by_token[tokens[-1]].append(entity)
    for token, members in by_token.items():
        if not 2 <= len(members) <= 8:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                if (a, b) in known:
                    continue
                known.add((a, b))
                known.add((b, a))
                out.append(_edge(a, b, limit_predicate(f"même {token}", max_words), "lexical", 0.4))
    return out


# ----------------------------------------------------------------------------
# LLM-based
# ----------------------------------------------------------------------------


def _cross_cluster(entities, edges, clusters, known, llm, max_words, on_progress):
    """Ask the LLM to bridge topic pairs that share no relation."""
    from llm.prompt_loader import get_prompt

    by_cluster = _entities_by_cluster(entities)
    labels = {str(c.id): c.label for c in clusters}
    present = [cid for cid in by_cluster if cid in labels]
    if len(present) < 2:
        return []

    # Topic pairs already bridged by an observed relation need no inference.
    linked = set()
    for edge in edges:
        cluster_a = entities.get(edge["subject"], {}).get("main_cluster")
        cluster_b = entities.get(edge["object"], {}).get("main_cluster")
        if cluster_a and cluster_b and cluster_a != cluster_b:
            linked.add(tuple(sorted((cluster_a, cluster_b))))

    template = get_prompt("KG_CROSS_CLUSTER_INFERENCE")
    prompts, pairs = [], []
    for i, a in enumerate(present):
        for b in present[i + 1 :]:
            if tuple(sorted((a, b))) in linked:
                continue
            top_a = _top_entities(by_cluster[a])
            top_b = _top_entities(by_cluster[b])
            if not top_a or not top_b:
                continue
            prompts.append(
                template.format(
                    cluster_a_label=labels[a],
                    entities_a=", ".join(top_a),
                    cluster_b_label=labels[b],
                    entities_b=", ".join(top_b),
                    existing_relations=_relations_sample(edges, set(top_a) | set(top_b)),
                    max_relations=3,
                )
            )
            pairs.append((set(top_a), set(top_b)))

    if not prompts:
        return []

    responses = llm.chat_batch_or_concurrent(prompts, json_mode=True, on_progress=on_progress)
    out = []
    for (allowed_a, allowed_b), response in zip(pairs, responses):
        out += _parse(response, allowed_a, allowed_b, known, "cross_cluster", max_words)
    return out


def _within_cluster(entities, edges, graph, clusters, known, llm, max_words):
    """Connect same-topic entities that the text never linked explicitly."""
    from llm.prompt_loader import get_prompt

    by_cluster = _entities_by_cluster(entities)
    labels = {str(c.id): c.label for c in clusters}

    template = get_prompt("KG_WITHIN_CLUSTER_INFERENCE")
    prompts, allowed_sets = [], []
    for cluster_id, members in by_cluster.items():
        if cluster_id not in labels:
            continue
        top = _top_entities(members, limit=25)
        candidates = [
            (a, b)
            for i, a in enumerate(top)
            for b in top[i + 1 :]
            if (a, b) not in known and not graph.has_edge(a, b)
        ][:20]
        if not candidates:
            continue
        prompts.append(
            template.format(
                cluster_label=labels[cluster_id],
                pairs="\n".join(f"- {a} / {b}" for a, b in candidates),
                existing_relations=_relations_sample(edges, set(top)),
            )
        )
        allowed_sets.append(set(top))

    if not prompts:
        return []

    responses = llm.chat_batch_or_concurrent(prompts, json_mode=True)
    out = []
    for allowed, response in zip(allowed_sets, responses):
        out += _parse(response, allowed, allowed, known, "within_cluster", max_words)
    return out


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _entities_by_cluster(entities):
    grouped = collections.defaultdict(list)
    for key, data in entities.items():
        cluster_id = data.get("main_cluster")
        if cluster_id:
            grouped[cluster_id].append((key, data.get("frequency", 0)))
    return grouped


def _top_entities(members, limit=20):
    return [key for key, _ in sorted(members, key=lambda kv: -kv[1])[:limit]]


def _relations_sample(edges, allowed, limit=12):
    lines = [
        f"- {e['subject']} {e['predicate']} {e['object']}"
        for e in edges
        if e["subject"] in allowed or e["object"] in allowed
    ]
    return "\n".join(lines[:limit]) or "(aucune)"


def _parse(response, allowed_subjects, allowed_objects, known, kind, max_words):
    """Keep only relations the model was allowed to produce."""
    if not response:
        return []
    try:
        items = json.loads(response.content).get("relations", [])
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []

    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or "").strip()
        obj = str(item.get("object") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        # The model must reuse the entities it was given, not invent new ones.
        if subject not in allowed_subjects or obj not in allowed_objects:
            continue
        if not predicate or subject == obj:
            continue
        if confidence < MIN_CONFIDENCE:
            continue
        if (subject, obj) in known:
            continue

        known.add((subject, obj))
        known.add((obj, subject))
        out.append(_edge(subject, obj, limit_predicate(predicate, max_words), kind, confidence))
    return out


def _edge(subject, obj, predicate, kind, confidence):
    return {
        "subject": subject,
        "object": obj,
        "predicate": predicate,
        "weight": 1.0,
        "confidence": round(confidence, 2),
        "cluster_id": None,
        "documents": set(),
        "evidence": [],
        "inferred": True,
        "inference_kind": kind,
    }


def _dedupe(inferred):
    seen = set()
    out = []
    for edge in inferred:
        key = tuple(sorted((edge["subject"], edge["object"])))
        if key in seen:
            continue
        seen.add(key)
        out.append(edge)
    return out
