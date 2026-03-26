import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from analysis.models import AnalysisJob
from analysis.tasks import run_unified_pipeline
from api.auth import require_api_token
from score.scoring import compute_score


@require_api_token
@require_http_methods(["GET"])
def score_view(request):
    """GET /api/v1/score/ — current quality score."""
    result = compute_score(request.api_project)
    breakdown = result.get("breakdown", {})
    return JsonResponse({
        "score": result.get("score", 0),
        "grade": result.get("grade", "E"),
        "dimensions": breakdown,
        "has_analysis": result.get("has_analysis", False),
        "has_docs": result.get("has_docs", False),
    })


@csrf_exempt
@require_api_token
@require_http_methods(["POST"])
def analysis_trigger(request):
    """POST /api/v1/analysis/ — trigger a new analysis."""
    job = AnalysisJob.objects.create(
        tenant=request.api_tenant,
        project=request.api_project,
        status=AnalysisJob.Status.QUEUED,
    )
    task = run_unified_pipeline.delay(str(job.id))
    job.celery_task_id = task.id
    job.status = AnalysisJob.Status.RUNNING
    job.save(update_fields=["celery_task_id", "status"])

    return JsonResponse({"job_id": str(job.id), "status": "running"}, status=202)


@require_api_token
@require_http_methods(["GET"])
def analysis_detail_view(request, job_id):
    """GET /api/v1/analysis/{job_id}/ — poll analysis status."""
    try:
        job = AnalysisJob.objects.get(
            id=job_id, tenant=request.api_tenant, project=request.api_project
        )
    except AnalysisJob.DoesNotExist:
        return JsonResponse({"error": "Analysis job not found", "code": "NOT_FOUND"}, status=404)

    result = {
        "job_id": str(job.id),
        "status": job.status,
        "current_phase": job.current_phase,
        "progress_pct": job.progress_pct,
        "error_message": job.error_message or None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }

    if job.status == AnalysisJob.Status.COMPLETED:
        score_result = compute_score(request.api_project)
        result["score"] = score_result.get("score", 0)
        result["grade"] = score_result.get("grade", "E")
        result["dimensions"] = score_result.get("breakdown", {})

    return JsonResponse(result)
