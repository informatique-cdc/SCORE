"""
Graph metrics and layout, computed once at build time.

Positions are stored on the entity rather than simulated in the browser: the
page renders in one frame with ECharts ``layout: "none"``, and the same corpus
always draws the same map, which matters when exploring across sessions.

Communities come from TopicCluster instead of Louvain — they are already
computed from embeddings and carry an LLM-written label, so the legend reads
"Couverture santé mutuelle IPSEC" rather than "community 7".
"""

import logging
import math

import networkx as nx

logger = logging.getLogger(__name__)


def build_graph(entities, relations) -> nx.Graph:
    """Undirected view used for metrics and layout."""
    graph = nx.Graph()
    graph.add_nodes_from(entities)
    for subject, obj, weight in relations:
        if subject == obj:
            continue
        if graph.has_edge(subject, obj):
            graph[subject][obj]["weight"] += weight
        else:
            graph.add_edge(subject, obj, weight=weight)
    return graph


def centrality(graph: nx.Graph, sample: int = 500) -> dict[str, float]:
    """Normalised betweenness. Sampled above *sample* nodes — it is O(n·m)."""
    n = graph.number_of_nodes()
    if n < 3:
        return dict.fromkeys(graph.nodes(), 0.0)
    k = min(sample, n) if n > sample else None
    scores = nx.betweenness_centrality(graph, k=k, seed=42, normalized=True)
    peak = max(scores.values(), default=0.0)
    if peak > 0:
        return {node: value / peak for node, value in scores.items()}
    return scores


def positions(graph: nx.Graph, node_clusters: dict[str, str]) -> dict[str, tuple[float, float]]:
    """Spring layout seeded by cluster barycentres.

    Seeding matters: entities of one cluster land together, and bridge entities
    settle on the boundaries where they belong, instead of the whole graph
    collapsing into an undifferentiated ball.
    """
    if graph.number_of_nodes() == 0:
        return {}
    if graph.number_of_nodes() == 1:
        return {next(iter(graph.nodes())): (0.0, 0.0)}

    clusters = sorted({c for c in node_clusters.values() if c})
    anchors = {}
    for index, cluster_id in enumerate(clusters):
        angle = 2 * math.pi * index / max(len(clusters), 1)
        anchors[cluster_id] = (math.cos(angle), math.sin(angle))

    initial = {}
    for i, node in enumerate(graph.nodes()):
        anchor = anchors.get(node_clusters.get(node))
        if anchor is None:
            angle = 2 * math.pi * i / graph.number_of_nodes()
            initial[node] = (0.35 * math.cos(angle), 0.35 * math.sin(angle))
        else:
            # Deterministic jitter so co-clustered nodes do not start superposed.
            offset = ((i % 17) / 17 - 0.5) * 0.25
            initial[node] = (anchor[0] + offset, anchor[1] - offset)

    raw = nx.spring_layout(
        graph,
        pos=initial,
        k=1.4 / math.sqrt(graph.number_of_nodes()),
        iterations=60,
        seed=42,
    )
    return _normalize(raw)


def _normalize(raw):
    xs = [p[0] for p in raw.values()]
    ys = [p[1] for p in raw.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = (max_x - min_x) or 1.0
    span_y = (max_y - min_y) or 1.0
    return {
        node: (
            round(2 * (p[0] - min_x) / span_x - 1, 4),
            round(2 * (p[1] - min_y) / span_y - 1, 4),
        )
        for node, p in raw.items()
    }


def summarize(graph: nx.Graph) -> dict:
    """Connectivity indicators — the numbers that decide whether this is usable."""
    n = graph.number_of_nodes()
    if n == 0:
        return {"nodes": 0, "edges": 0, "avg_degree": 0.0, "components": 0, "largest_pct": 0.0}
    components = sorted(nx.connected_components(graph), key=len, reverse=True)
    return {
        "nodes": n,
        "edges": graph.number_of_edges(),
        "avg_degree": round(2 * graph.number_of_edges() / n, 2),
        "components": len(components),
        "largest_pct": round(100 * len(components[0]) / n, 1),
    }
