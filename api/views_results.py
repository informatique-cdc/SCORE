from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from analysis.models import (
    AnalysisJob,
    ContradictionPair,
    DuplicateGroup,
    GapReport,
    TopicCluster,
)
from api.auth import require_api_token


def _get_job_or_404(request, job_id):
    """Return AnalysisJob or a 404 JsonResponse."""
    try:
        return AnalysisJob.objects.get(
            id=job_id, tenant=request.api_tenant, project=request.api_project
        )
    except AnalysisJob.DoesNotExist:
        return None


@require_api_token
@require_http_methods(["GET"])
def duplicates_view(request, job_id):
    """GET /api/v1/analysis/{job_id}/duplicates/ — list duplicate groups with pairs."""
    job = _get_job_or_404(request, job_id)
    if job is None:
        return JsonResponse(
            {"error": "Analysis job not found", "code": "NOT_FOUND"}, status=404
        )

    groups = DuplicateGroup.objects.filter(
        analysis_job=job
    ).prefetch_related("pairs", "pairs__doc_a", "pairs__doc_b")

    results = []
    for group in groups:
        pairs = []
        for pair in group.pairs.all():
            pairs.append({
                "id": str(pair.id),
                "doc_a": {"id": str(pair.doc_a_id), "title": pair.doc_a.title},
                "doc_b": {"id": str(pair.doc_b_id), "title": pair.doc_b.title},
                "semantic_score": pair.semantic_score,
                "lexical_score": pair.lexical_score,
                "metadata_score": pair.metadata_score,
                "combined_score": pair.combined_score,
                "verified": pair.verified,
                "verification_result": pair.verification_result or None,
            })
        results.append({
            "id": str(group.id),
            "recommended_action": group.recommended_action,
            "rationale": group.rationale,
            "pairs": pairs,
        })

    return JsonResponse({"total": len(results), "groups": results})


@require_api_token
@require_http_methods(["GET"])
def contradictions_view(request, job_id):
    """GET /api/v1/analysis/{job_id}/contradictions/ — list contradiction pairs."""
    job = _get_job_or_404(request, job_id)
    if job is None:
        return JsonResponse(
            {"error": "Analysis job not found", "code": "NOT_FOUND"}, status=404
        )

    contradictions = ContradictionPair.objects.filter(
        analysis_job=job
    ).select_related("claim_a", "claim_b")

    results = []
    for c in contradictions:
        results.append({
            "id": str(c.id),
            "claim_a": {
                "id": str(c.claim_a_id),
                "text": c.claim_a.as_text,
                "document_id": str(c.claim_a.document_id),
            },
            "claim_b": {
                "id": str(c.claim_b_id),
                "text": c.claim_b.as_text,
                "document_id": str(c.claim_b.document_id),
            },
            "classification": c.classification,
            "severity": c.severity,
            "confidence": c.confidence,
            "evidence": c.evidence,
        })

    return JsonResponse({"total": len(results), "contradictions": results})


@require_api_token
@require_http_methods(["GET"])
def clusters_view(request, job_id):
    """GET /api/v1/analysis/{job_id}/clusters/ — list top-level clusters."""
    job = _get_job_or_404(request, job_id)
    if job is None:
        return JsonResponse(
            {"error": "Analysis job not found", "code": "NOT_FOUND"}, status=404
        )

    clusters = TopicCluster.objects.filter(analysis_job=job, parent__isnull=True)

    results = []
    for cluster in clusters:
        results.append({
            "id": str(cluster.id),
            "label": cluster.label,
            "summary": cluster.summary,
            "doc_count": cluster.doc_count,
            "chunk_count": cluster.chunk_count,
            "level": cluster.level,
            "key_concepts": cluster.key_concepts,
        })

    return JsonResponse({"total": len(results), "clusters": results})


@require_api_token
@require_http_methods(["GET"])
def gaps_view(request, job_id):
    """GET /api/v1/analysis/{job_id}/gaps/ — list gap reports."""
    job = _get_job_or_404(request, job_id)
    if job is None:
        return JsonResponse(
            {"error": "Analysis job not found", "code": "NOT_FOUND"}, status=404
        )

    gaps = GapReport.objects.filter(analysis_job=job)

    results = []
    for gap in gaps:
        results.append({
            "id": str(gap.id),
            "gap_type": gap.gap_type,
            "title": gap.title,
            "description": gap.description,
            "severity": gap.severity,
            "coverage_score": gap.coverage_score,
            "evidence": gap.evidence,
        })

    return JsonResponse({"total": len(results), "gaps": results})
