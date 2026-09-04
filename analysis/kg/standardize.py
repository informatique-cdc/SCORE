"""
Entity standardisation.

Ported from robert-mcdermott/ai-knowledge-graph (Apache 2.0),
src/knowledge_graph/entity_standardization.py, with three departures:

  - French stopwords, from nsg.stopwords, instead of the English list.
  - The canonical form stays lowercase for matching, but the display label keeps
    the most frequent original casing. The reference implementation lowercases
    everything including proper nouns, which is unreadable on a French corpus.
  - Embedding-based merging, which the reference project has no vectors for.
    The lot 0 measurement made this structural rather than cosmetic: only 358
    entities out of 1016 recurred, and recurrence is what drives graph density.

Grouping runs inside a cluster first — vocabulary is homogeneous there, so
matches are trustworthy — then canonical forms are merged across clusters.
"""

import collections
import logging

import numpy as np

from analysis.kg.extraction import normalize

logger = logging.getLogger(__name__)


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def standardize(triples, llm, config, on_progress=None) -> dict[str, str]:
    """Return a mapping raw surface form -> canonical key.

    Entities whose canonical key collides are merged; the caller uses the
    mapping to build KGEntity rows and rewrite relation endpoints.
    """
    std_cfg = config.get("standardization", {})
    if not std_cfg.get("enabled", True):
        return {t: normalize(t) for t in _surface_forms(triples)}

    # Pass 1 — lexical normalisation, global.
    mapping = {surface: normalize(surface) for surface in _surface_forms(triples)}
    mapping = {s: c for s, c in mapping.items() if c}

    uf = _UnionFind()
    for canonical in set(mapping.values()):
        uf.find(canonical)

    # Pass 2 — root-word containment ("comité de déontologie" ⊂ "président du
    # comité de déontologie"). Only merge when the shorter form is itself a
    # frequent standalone entity, otherwise every substring swallows its host.
    freq = collections.Counter(mapping[s] for s in _surface_forms(triples) if s in mapping)
    canonicals = sorted(freq, key=lambda c: (len(c.split()), c))
    frequent = {c for c, n in freq.items() if n >= 2}
    for i, short in enumerate(canonicals):
        if short not in frequent or len(short.split()) < 2:
            continue
        for long in canonicals[i + 1 :]:
            if len(long) > len(short) and f" {short} " in f" {long} ":
                uf.union(short, long)

    # Pass 3 — embeddings, per cluster.
    if std_cfg.get("use_embeddings", True):
        _merge_by_embedding(triples, mapping, uf, llm, std_cfg, on_progress)

    # Collapse: every canonical points at its group representative, chosen as
    # the most frequent member (ties broken by brevity).
    groups = collections.defaultdict(list)
    for canonical in set(mapping.values()):
        groups[uf.find(canonical)].append(canonical)

    representative = {}
    for members in groups.values():
        best = sorted(members, key=lambda c: (-freq.get(c, 0), len(c)))[0]
        for member in members:
            representative[member] = best

    merged = sum(1 for c, r in representative.items() if c != r)
    logger.info(
        "[kg] Standardisation: %d surface forms -> %d canonical entities (%d merged)",
        len(mapping),
        len(set(representative.values())),
        merged,
    )
    return {surface: representative[c] for surface, c in mapping.items()}


def _surface_forms(triples):
    forms = set()
    for t in triples:
        forms.add(t.subject)
        forms.add(t.object)
    return forms


def _merge_by_embedding(triples, mapping, uf, llm, std_cfg, on_progress):
    """Merge near-synonymous entities within each cluster, via cosine similarity.

    Scoped per cluster to keep the similarity matrix bounded: a project-wide
    matrix over ~20k entities would be gigabytes.
    """
    threshold = std_cfg.get("embedding_threshold", 0.88)

    per_cluster = collections.defaultdict(set)
    for t in triples:
        for surface in (t.subject, t.object):
            canonical = mapping.get(surface)
            if canonical:
                per_cluster[t.cluster_id].add(canonical)

    total = len(per_cluster)
    for done, (cluster_id, canonicals) in enumerate(per_cluster.items(), start=1):
        items = sorted(canonicals)
        if len(items) < 2:
            continue
        try:
            vectors = np.asarray(llm.embed(items), dtype=np.float32)
        except Exception as exc:
            logger.warning("[kg] Embedding merge skipped for cluster %s: %s", cluster_id, exc)
            continue

        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        similarity = (vectors / norms) @ (vectors / norms).T
        np.fill_diagonal(similarity, 0.0)

        for i, j in zip(*np.where(similarity >= threshold)):
            if i < j:
                uf.union(items[int(i)], items[int(j)])

        if on_progress:
            on_progress(done, total)
