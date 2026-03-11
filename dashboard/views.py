"""Dashboard views: home, stats, navigation."""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from score.issues import build_analysis_issues
from score.utils import parse_json_body

from analysis.models import (
    AnalysisJob,
    AuditJob,
    ContradictionPair,
    DuplicateGroup,
    GapReport,
    TopicCluster,
)
from connectors.models import ConnectorConfig
from ingestion.models import Document, IngestionJob
from reports.models import Report

from .models import Feedback
from .scoring import build_breakdown_json, compute_score, compute_score_detail


def _has_active_jobs(project):
    """Return True if there are any running/queued ingestion, analysis, or audit jobs."""
    if IngestionJob.objects.filter(project=project).filter(
        status__in=[IngestionJob.Status.QUEUED, IngestionJob.Status.RUNNING]
    ).exists():
        return True
    if AnalysisJob.objects.filter(project=project).filter(
        status__in=[AnalysisJob.Status.QUEUED, AnalysisJob.Status.RUNNING]
    ).exists():
        return True
    if AuditJob.objects.filter(project=project).filter(
        status__in=[AuditJob.Status.QUEUED, AuditJob.Status.RUNNING]
    ).exists():
        return True
    return False


def _dashboard_stats_context(project):
    """Build context dict for dashboard stats partial."""
    latest_analysis = (
        AnalysisJob.objects.filter(project=project).order_by("-created_at").first()
    )
    doc_count = Document.objects.filter(project=project).exclude(status=Document.Status.DELETED).count()
    dup_count = 0
    contra_count = 0
    gap_count = 0
    if latest_analysis:
        dup_count = DuplicateGroup.objects.filter(analysis_job=latest_analysis).count()
        contra_count = ContradictionPair.objects.filter(
            analysis_job=latest_analysis,
            classification__in=["contradiction", "outdated"],
        ).count()
        gap_count = GapReport.objects.filter(analysis_job=latest_analysis).count()
    return {
        "doc_count": doc_count,
        "dup_count": dup_count,
        "contra_count": contra_count,
        "gap_count": gap_count,
        "should_poll": _has_active_jobs(project),
    }


def _dashboard_latest_analysis_context(project, membership):
    """Build context dict for latest analysis partial."""
    from analysis.views import can_run_analysis

    latest_analysis = (
        AnalysisJob.objects.filter(project=project).order_by("-created_at").first()
    )
    should_poll = latest_analysis is not None and latest_analysis.status in (
        AnalysisJob.Status.QUEUED, AnalysisJob.Status.RUNNING,
    )
    can_run, block_reason = can_run_analysis(project)
    return {
        "latest_analysis": latest_analysis,
        "membership": membership,
        "should_poll": should_poll,
        "can_run_analysis": can_run,
        "analysis_block_reason": block_reason,
    }


def _dashboard_recent_jobs_context(project):
    """Build context dict for recent jobs partial."""
    recent_ingestions = IngestionJob.objects.filter(project=project).order_by("-created_at")[:5]
    should_poll = any(
        j.status in (IngestionJob.Status.QUEUED, IngestionJob.Status.RUNNING)
        for j in recent_ingestions
    )
    return {
        "recent_ingestions": recent_ingestions,
        "should_poll": should_poll,
    }


def _build_activity_feed(project, limit=8):
    """Build a unified activity feed from recent ingestion + analysis jobs."""
    ingestions = IngestionJob.objects.filter(project=project).select_related("connector").order_by("-created_at")[:limit]
    analyses = AnalysisJob.objects.filter(project=project).order_by("-created_at")[:limit]

    events = []
    for job in ingestions:
        events.append({
            "type": "ingestion",
            "date": job.created_at,
            "status": job.status,
            "title": _("Ingestion : %(name)s") % {"name": job.connector.name} if job.connector else _("Ingestion"),
            "detail": _("%(processed)s/%(total)s documents") % {"processed": job.processed_documents, "total": job.total_documents},
            "icon": "link",
        })
    for job in analyses:
        phase_label = job.get_current_phase_display() if job.status == AnalysisJob.Status.RUNNING else ""
        events.append({
            "type": "analysis",
            "date": job.created_at,
            "status": job.status,
            "title": _("Analyse %(status)s") % {"status": job.get_status_display()},
            "detail": phase_label or (f"{job.progress_pct}%" if job.status == AnalysisJob.Status.RUNNING else ""),
            "icon": "search",
            "pk": str(job.pk),
        })

    events.sort(key=lambda e: e["date"], reverse=True)
    return events[:limit]


def _build_top_issues(project):
    """Build a list of top issues / smart suggestions from latest analysis."""
    latest = (
        AnalysisJob.objects.filter(project=project, status=AnalysisJob.Status.COMPLETED)
        .order_by("-created_at")
        .first()
    )
    issues = []

    doc_count = Document.objects.filter(project=project).exclude(
        status=Document.Status.DELETED
    ).count()
    connector_count = ConnectorConfig.objects.filter(project=project).count()

    # Setup alerts (before any analysis exists)
    if connector_count == 0:
        issues.append({
            "severity": "medium",
            "title": _("Aucun connecteur configuré"),
            "detail": _("Configurez un connecteur pour importer vos documents."),
            "action_label": _("Ajouter un connecteur"),
            "action_url_name": "connector-create",
            "action_pk": None,
        })

    if doc_count == 0 and connector_count > 0:
        issues.append({
            "severity": "medium",
            "title": _("Aucun document indexé"),
            "detail": _("Lancez une ingération depuis vos connecteurs pour indexer vos documents."),
            "action_label": _("Voir les connecteurs"),
            "action_url_name": "connector-list",
            "action_pk": None,
        })

    if doc_count > 0 and not latest:
        issues.append({
            "severity": "medium",
            "title": _("Aucune analyse effectuée"),
            "detail": _("Vous avez %(count)s document(s) indexé(s). Lancez une analyse pour détecter les problèmes.") % {"count": doc_count},
            "action_label": _("Lancer l\u2019analyse"),
            "action_url_name": "analysis-list",
            "action_pk": None,
        })

    if not latest:
        return issues[:4]

    # Delegate to shared issue builder for analysis results
    issues.extend(build_analysis_issues(latest))

    return issues[:4]


@login_required
def home(request):
    if not request.tenant:
        return redirect("tenant-select")

    project = request.project

    connector_count = ConnectorConfig.objects.filter(project=project).count()
    report_count = Report.objects.filter(project=project).count()
    cluster_count = 0
    latest_completed = (
        AnalysisJob.objects.filter(project=project, status=AnalysisJob.Status.COMPLETED)
        .order_by("-created_at")
        .first()
    )
    if latest_completed:
        cluster_count = TopicCluster.objects.filter(analysis_job=latest_completed, level=0).count()

    recent_analyses = AnalysisJob.objects.filter(project=project).order_by("-created_at")[:5]

    # SCORE breakdown as JSON for radar chart
    ds = compute_score(project)
    breakdown_json = build_breakdown_json(ds["breakdown"])

    context = {
        "connector_count": connector_count,
        "report_count": report_count,
        "cluster_count": cluster_count,
        "recent_analyses": recent_analyses,
        "ds": ds,
        "breakdown_json": breakdown_json,
        "activity_feed": _build_activity_feed(project),
        "top_issues": _build_top_issues(project),
    }
    context.update(_dashboard_stats_context(project))
    context.update(_dashboard_latest_analysis_context(project, request.membership))
    context.update(_dashboard_recent_jobs_context(project))

    return render(request, "dashboard/home.html", context)


@login_required
def stats_partial(request):
    if not request.tenant:
        return redirect("tenant-select")
    context = _dashboard_stats_context(request.project)
    return render(request, "dashboard/_stats.html", context)


@login_required
def latest_analysis_partial(request):
    if not request.tenant:
        return redirect("tenant-select")
    context = _dashboard_latest_analysis_context(request.project, request.membership)
    return render(request, "dashboard/_latest_analysis.html", context)


@login_required
def recent_jobs_partial(request):
    if not request.tenant:
        return redirect("tenant-select")
    context = _dashboard_recent_jobs_context(request.project)
    return render(request, "dashboard/_recent_jobs.html", context)


@login_required
def score_detail_json(request):
    if not request.tenant:
        return JsonResponse({"error": str(_("Aucun espace sélectionné"))}, status=400)
    data = compute_score_detail(request.project)
    return JsonResponse(data)


@login_required
@require_POST
def submit_feedback(request):
    data, err = parse_json_body(request)
    if err:
        return err
    Feedback.objects.create(
        tenant=getattr(request, "current_tenant", None),
        user=request.user,
        feedback_type=data["type"],
        area=data["area"],
        subject=data["subject"],
        description=data["description"],
    )
    return JsonResponse({"ok": True})
