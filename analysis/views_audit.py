"""Audit RAG views: detail, axis pages, progress partial, JSON API.

audit_list and audit_run now redirect to the unified analysis page.
Axis detail views remain accessible from the unified analysis detail page.
"""
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from analysis.constants import AXIS_ICONS, AXIS_LABELS, AXIS_ORDER
from analysis.models import AnalysisJob, AuditAxisResult, AuditJob
from analysis.views import analysis_number

logger = logging.getLogger(__name__)


@login_required
def audit_list(request):
    """Redirect to the unified analysis list."""
    return redirect("analysis-list")


@login_required
@require_POST
def audit_run(request):
    """Redirect to the unified analysis list (no standalone audit run)."""
    return redirect("analysis-list")


@login_required
def audit_detail(request, pk):
    job = get_object_or_404(AuditJob, pk=pk, project=request.project)
    results = AuditAxisResult.objects.filter(audit_job=job)
    results_map = {r.axis: r for r in results}
    axes = []
    for key in AXIS_ORDER:
        r = results_map.get(key)
        axes.append({
            "key": key,
            "label": AXIS_LABELS[key],
            "icon": AXIS_ICONS[key],
            "result": r,
            "score": r.score if r else None,
            "url": reverse(f"audit-{key}", kwargs={"pk": job.id}) if r else None,
        })
    should_poll = job.status in (AuditJob.Status.QUEUED, AuditJob.Status.RUNNING)
    # Link back to parent analysis if available
    parent_analysis = job.analysis_job
    return render(request, "analysis/audit/detail.html", {
        "job": job,
        "axes": axes,
        "should_poll": should_poll,
        "parent_analysis": parent_analysis,
        "analysis_number": analysis_number(parent_analysis),
    })


@login_required
@require_POST
def audit_retry(request, pk):
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("audit-list")
    job = get_object_or_404(AuditJob, pk=pk, project=request.project)
    if job.status not in (AuditJob.Status.QUEUED, AuditJob.Status.FAILED):
        return redirect("audit-detail", pk=pk)
    job.status = AuditJob.Status.QUEUED
    job.progress_pct = 0
    job.current_axis = AuditJob.Axis.HYGIENE
    job.error_message = ""
    job.started_at = None
    job.completed_at = None
    job.overall_score = None
    job.overall_grade = ""
    job.save()
    AuditAxisResult.objects.filter(audit_job=job).delete()
    from analysis.audit.runner import run_audit
    task = run_audit.delay(str(job.id))
    job.celery_task_id = task.id
    job.save()
    return redirect("audit-detail", pk=pk)


@login_required
@require_POST
def audit_delete(request, pk):
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("audit-list")
    job = get_object_or_404(AuditJob, pk=pk, project=request.project)
    job.delete()
    return redirect("analysis-list")


def _axis_detail_view(request, pk, axis_key):
    job = get_object_or_404(AuditJob, pk=pk, project=request.project)
    result = get_object_or_404(AuditAxisResult, audit_job=job, axis=axis_key)
    return render(request, f"analysis/audit/{axis_key}.html", {
        "job": job,
        "result": result,
        "axis_label": AXIS_LABELS[axis_key],
        "metrics": result.metrics,
        "chart_data": result.chart_data,
        "details": result.details,
        "analysis_number": analysis_number(job.analysis_job),
    })


@login_required
def audit_hygiene(request, pk):
    return _axis_detail_view(request, pk, "hygiene")


@login_required
def audit_structure(request, pk):
    return _axis_detail_view(request, pk, "structure")


@login_required
def audit_coverage(request, pk):
    return _axis_detail_view(request, pk, "coverage")


@login_required
def audit_coherence(request, pk):
    return _axis_detail_view(request, pk, "coherence")


@login_required
def audit_retrievability(request, pk):
    return _axis_detail_view(request, pk, "retrievability")


@login_required
def audit_governance(request, pk):
    return _axis_detail_view(request, pk, "governance")


@login_required
def audit_progress_partial(request, pk):
    job = get_object_or_404(AuditJob, pk=pk, project=request.project)
    should_poll = job.status in (AuditJob.Status.QUEUED, AuditJob.Status.RUNNING)
    return render(request, "analysis/audit/_progress.html", {
        "job": job,
        "should_poll": should_poll,
    })


@login_required
def api_audit_axis(request, pk, axis):
    """JSON endpoint for D3.js charts of a specific axis."""
    job = get_object_or_404(AuditJob, pk=pk, project=request.project)
    try:
        result = AuditAxisResult.objects.get(audit_job=job, axis=axis)
    except AuditAxisResult.DoesNotExist:
        return JsonResponse({"error": "Axe non disponible"}, status=404)
    return JsonResponse({
        "axis": axis,
        "score": result.score,
        "metrics": result.metrics,
        "chart_data": result.chart_data,
    })
