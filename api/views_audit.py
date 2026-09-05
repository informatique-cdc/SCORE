from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from analysis.models import AnalysisJob, AuditJob, AuditAxisResult
from analysis.tasks import run_unified_pipeline
from api.auth import require_api_token


@csrf_exempt
@require_api_token
@require_http_methods(["POST"])
def audit_trigger(request):
    """POST /api/v1/audit/ -- trigger a new analysis with audit."""
    job = AnalysisJob.objects.create(
        tenant=request.api_tenant,
        project=request.api_project,
        status=AnalysisJob.Status.QUEUED,
        includes_audit=True,
    )
    task = run_unified_pipeline.delay(str(job.id))
    job.celery_task_id = task.id
    job.status = AnalysisJob.Status.RUNNING
    job.save(update_fields=["celery_task_id", "status"])

    return JsonResponse({"job_id": str(job.id), "status": "running"}, status=202)


@require_api_token
@require_http_methods(["GET"])
def audit_detail(request, job_id):
    """GET /api/v1/audit/{job_id}/ -- retrieve audit results with axis scores."""
    try:
        audit_job = AuditJob.objects.get(
            id=job_id, tenant=request.api_tenant, project=request.api_project
        )
    except AuditJob.DoesNotExist:
        return JsonResponse(
            {"error": "Audit job not found", "code": "NOT_FOUND"}, status=404
        )

    axes = []
    for result in audit_job.axis_results.all():
        axes.append({
            "axis": result.axis,
            "score": result.score,
            "metrics": result.metrics,
            "chart_data": result.chart_data,
            "details": result.details,
        })

    return JsonResponse({
        "job_id": str(audit_job.id),
        "status": audit_job.status,
        "overall_score": audit_job.overall_score,
        "overall_grade": audit_job.overall_grade or None,
        "axes": axes,
        "created_at": audit_job.created_at.isoformat(),
        "completed_at": audit_job.completed_at.isoformat() if audit_job.completed_at else None,
    })
