"""Analysis JSON API views for D3.js visualizations."""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from score.utils import parse_json_body

from analysis.models import (
    AnalysisJob,
    ClusterMembership,
    GapReport,
    TopicCluster,
    TreeNode,
)
from analysis.semantic_graph import load_graph


@login_required
def clusters_json(request, pk):
    """JSON endpoint for cluster/graph visualization."""
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    clusters = TopicCluster.objects.filter(analysis_job=job)

    nodes = []
    for c in clusters:
        nodes.append(
            {
                "id": str(c.id),
                "label": c.label,
                "summary": c.summary[:200] if c.summary else "",
                "key_concepts": c.key_concepts or [],
                "content_purpose": c.content_purpose or "",
                "x": c.centroid_x,
                "y": c.centroid_y,
                "doc_count": c.doc_count,
                "chunk_count": c.chunk_count,
                "level": c.level,
                "parent_id": str(c.parent_id) if c.parent_id else None,
            }
        )

    edges = []
    cluster_docs = {}
    top_level = [c for c in clusters if c.level == 0]
    top_ids = [c.id for c in top_level]
    if top_ids:
        memberships = ClusterMembership.objects.filter(cluster_id__in=top_ids).values_list(
            "cluster_id", "document_id"
        )
        for cluster_id, doc_id in memberships:
            cluster_docs.setdefault(str(cluster_id), set()).add(doc_id)

    cluster_ids = list(cluster_docs.keys())
    for i in range(len(cluster_ids)):
        for j in range(i + 1, len(cluster_ids)):
            shared = cluster_docs[cluster_ids[i]] & cluster_docs[cluster_ids[j]]
            if shared:
                edges.append(
                    {
                        "source": cluster_ids[i],
                        "target": cluster_ids[j],
                        "weight": len(shared),
                    }
                )

    for c in clusters:
        if c.parent_id:
            edges.append(
                {
                    "source": str(c.parent_id),
                    "target": str(c.id),
                    "weight": 1,
                    "type": "hierarchy",
                }
            )

    gaps = GapReport.objects.filter(analysis_job=job)
    gap_nodes = []
    for g in gaps:
        gap_nodes.append(
            {
                "id": f"gap-{g.id}",
                "label": g.title[:100],
                "type": g.gap_type,
                "severity": g.severity,
                "coverage_score": g.coverage_score,
                "related_cluster": str(g.related_cluster_id) if g.related_cluster_id else None,
            }
        )

    return JsonResponse(
        {
            "nodes": nodes,
            "edges": edges,
            "gaps": gap_nodes,
        }
    )


@login_required
def tree_json(request, pk):
    """JSON endpoint for tree visualization."""
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)

    def build_tree(parent=None):
        nodes = (
            TreeNode.objects.filter(analysis_job=job, parent=parent)
            .select_related("cluster", "document")
            .order_by("sort_order")
        )

        children = []
        for node in nodes:
            child = {
                "id": str(node.id),
                "name": node.label,
                "type": node.node_type,
                "children": build_tree(parent=node),
            }
            if node.document:
                child["doc_id"] = str(node.document_id)
                child["doc_url"] = node.document.source_url
            if node.cluster:
                child["content_purpose"] = node.cluster.content_purpose or ""
                child["key_concepts"] = node.cluster.key_concepts or []
            children.append(child)
        return children

    tree = {
        "id": "root",
        "name": request.project.name if request.project else request.tenant.name,
        "type": "root",
        "children": build_tree(parent=None),
    }

    return JsonResponse(tree)


@login_required
def concept_graph_json(request, pk):
    """GET — return top-150 nodes overview of the concept graph."""
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    nsg = load_graph(str(job.project_id))
    if nsg is None:
        return JsonResponse({"error": "Graph not found"}, status=404)

    G = nsg.graph
    total_nodes = G.number_of_nodes()
    total_edges = G.number_of_edges()

    degree_list = sorted(G.degree(), key=lambda x: x[1], reverse=True)
    top_nodes = [n for n, _ in degree_list[:150]]
    sub = G.subgraph(top_nodes)

    nodes = []
    for n, d in sub.nodes(data=True):
        nodes.append(
            {
                "id": n,
                "frequency": d.get("frequency", 1),
                "degree": sub.degree(n),
            }
        )

    edge_agg = {}
    for s, t, d in sub.edges(data=True):
        key = (s, t) if s <= t else (t, s)
        if key not in edge_agg:
            edge_agg[key] = {"weight": 0, "evidence": []}
        edge_agg[key]["weight"] += d.get("weight", 1.0)
        ev = d.get("evidence", [])
        if ev and len(edge_agg[key]["evidence"]) < 3:
            edge_agg[key]["evidence"].extend(ev[:2])

    edges = []
    for (s, t), agg in edge_agg.items():
        edges.append(
            {
                "source": s,
                "target": t,
                "weight": round(agg["weight"], 2),
                "evidence": agg["evidence"][:3],
            }
        )

    return JsonResponse(
        {
            "nodes": nodes,
            "edges": edges,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
        }
    )


@login_required
def concept_graph_query(request, pk):
    """POST {"query": "..."} — return neighborhood subgraph."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    nsg = load_graph(str(job.project_id))
    if nsg is None:
        return JsonResponse({"error": "Graph not found"}, status=404)

    body, err = parse_json_body(request)
    if err:
        return err

    query = (body.get("query") or "").strip()
    if not query:
        return JsonResponse({"error": "query is required"}, status=400)

    top_k = min(int(body.get("top_k", 5)), 10)
    hops = min(int(body.get("hops", 1)), 3)
    max_nodes = min(int(body.get("max_nodes", 40)), 80)

    result = nsg.query_subgraph(query, top_k=top_k, hops=hops, max_nodes=max_nodes)
    return JsonResponse(result)
