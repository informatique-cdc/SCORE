"""Analysis views: run jobs, view results, serve JSON for visualizations."""
import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy as _lazy
from django.urls import reverse
from django.db import models
from django.views.decorators.http import require_POST

from analysis.constants import AXIS_COLORS, AXIS_ICONS, AXIS_LABELS, SUB_COLORS, SUB_SCORE_LABELS
from docuscore.issues import build_analysis_issues
from docuscore.ratelimit import ratelimit
from analysis.models import (
    AnalysisJob,
    AuditAxisResult,
    AuditJob,
    ClusterMembership,
    ContradictionPair,
    DuplicateGroup,
    DuplicatePair,
    GapReport,
    HallucinationReport,
    PhaseTrace,
    PipelineTrace,
    TopicCluster,
    TraceEvent,
    TreeNode,
)
from analysis.presenters import contradiction_chart_data, gap_chart_data, hallucination_chart_data
from analysis.semantic_graph import graph_dir, load_graph
from analysis.tasks import UNIFIED_PROGRESS, run_unified_pipeline
from connectors.models import ConnectorConfig
from docuscore.scoring import build_breakdown_json, compute_docuscore, compute_docuscore_detail
from ingestion.models import Document
from tenants.models import AuditLog, log_audit

logger = logging.getLogger(__name__)


def can_run_analysis(project):
    """Return (can_run: bool, reason: str)."""
    # Block if already running
    if AnalysisJob.objects.filter(
        project=project,
        status__in=[AnalysisJob.Status.QUEUED, AnalysisJob.Status.RUNNING],
    ).exists():
        return False, "running"

    last_created_at = (
        AnalysisJob.objects.filter(project=project)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )

    # No previous analysis — allow if documents exist
    if last_created_at is None:
        has_docs = Document.objects.filter(project=project).exists()
        return has_docs, ("" if has_docs else "no_docs")

    # Check if any connector was added or synced since last analysis
    changed = ConnectorConfig.objects.filter(project=project).filter(
        models.Q(created_at__gt=last_created_at)
        | models.Q(last_sync_at__gt=last_created_at)
    ).exists()
    return changed, ("" if changed else "no_changes")


def analysis_number(analysis_job):
    """Return 1-based position of an AnalysisJob within its project (by date)."""
    return AnalysisJob.objects.filter(
        project=analysis_job.project,
        created_at__lte=analysis_job.created_at,
    ).count()


# Display constants imported from analysis.constants

AXIS_TOOLTIPS = {
    "hygiene": _lazy("Mesure la propreté du corpus : doublons exacts, quasi-doublons, boilerplate, homogénéité linguistique et données sensibles."),
    "structure": _lazy("Évalue la qualité du découpage en chunks : uniformité de taille, outliers, densité informationnelle et lisibilité."),
    "coverage": _lazy("Analyse la couverture sémantique : équilibre des topics, taux de couverture, outliers et cohérence intra-topic."),
    "coherence": _lazy("Détecte les incohérences internes : conflits clé-valeur, variations terminologiques et contradictions entre entités."),
    "retrievability": _lazy("Teste la capacité de recherche : MRR, recall@10, taux de résultats non-vides et diversité des résultats."),
    "governance": _lazy("Vérifie la gouvernance des métadonnées : complétude des champs, fraîcheur des documents, orphelins et connectivité."),
}

AXIS_ORDER = ["hygiene", "structure", "coverage", "coherence", "retrievability", "governance"]


def _build_job_issues(job):
    """Build a list of priority issues for a specific analysis job."""
    if job.status != AnalysisJob.Status.COMPLETED:
        return []
    return build_analysis_issues(
        job, exclude_resolved=True, include_hallucinations=True,
    )[:5]


def _analysis_jobs_context(project):
    """Build context dict for analysis jobs table partial."""
    from django.db.models import Avg, Count, Q

    jobs = list(AnalysisJob.objects.filter(project=project).order_by("-created_at")[:20])
    # Prefetch linked audit jobs for the table
    audit_map = {}
    audit_jobs = AuditJob.objects.filter(analysis_job__in=jobs).select_related("analysis_job")
    for aj in audit_jobs:
        audit_map[aj.analysis_job_id] = aj

    # Batch compute docuscores to avoid N+1 queries per job
    completed_jobs = [j for j in jobs if j.status == AnalysisJob.Status.COMPLETED]
    docuscores = {}
    if completed_jobs:
        docuscores = _batch_docuscores(project, completed_jobs, audit_map)

    for job in jobs:
        job.linked_audit = audit_map.get(job.id)
        job.docuscore = docuscores.get(job.id)

    should_poll = any(
        j.status in (AnalysisJob.Status.QUEUED, AnalysisJob.Status.RUNNING)
        for j in jobs
    )
    return {"jobs": jobs, "should_poll": should_poll}


def _batch_docuscores(project, completed_jobs, audit_map):
    """Compute docuscores for multiple jobs with batched queries.

    Mirror of compute_docuscore_for_job logic but uses aggregated queries
    instead of per-job queries (~8 queries total instead of ~18 × N).
    """
    from django.db.models import Avg, Count, Q
    from docuscore.scoring import grade, compute_penalty_score, health_score

    job_ids = [j.id for j in completed_jobs]

    # Document counts — shared across all jobs (same project)
    docs_qs = Document.objects.filter(project=project).exclude(
        status=Document.Status.DELETED
    )
    total_docs = docs_qs.count()
    if total_docs == 0:
        return {jid: {"grade": "E", "score": 0} for jid in job_ids}

    ready_docs = docs_qs.filter(status=Document.Status.READY).count()
    error_docs = docs_qs.filter(status=Document.Status.ERROR).count()
    health = health_score(ready_docs, error_docs, total_docs)

    # Batch: dup counts per job
    dup_counts = {}
    for row in (
        DuplicateGroup.objects.filter(analysis_job_id__in=job_ids)
        .exclude(recommended_action=DuplicateGroup.Action.KEEP)
        .values("analysis_job_id")
        .annotate(cnt=Count("id"))
    ):
        dup_counts[row["analysis_job_id"]] = row["cnt"]

    # Batch: contradiction weighted counts per job
    contra_data = {}
    for row in (
        ContradictionPair.objects.filter(
            analysis_job_id__in=job_ids,
            classification__in=["contradiction", "outdated"],
        )
        .exclude(resolution="resolved")
        .values("analysis_job_id")
        .annotate(
            high=Count("id", filter=Q(severity="high")),
            med=Count("id", filter=Q(severity="medium")),
            low=Count("id", filter=Q(severity="low")),
        )
    ):
        contra_data[row["analysis_job_id"]] = (
            row["high"] * 3 + row["med"] * 2 + row["low"]
        )

    # Batch: gap weighted counts + avg coverage per job
    gap_data = {}
    for row in (
        GapReport.objects.filter(analysis_job_id__in=job_ids)
        .exclude(resolution="resolved")
        .values("analysis_job_id")
        .annotate(
            high=Count("id", filter=Q(severity="high")),
            med=Count("id", filter=Q(severity="medium")),
            low=Count("id", filter=Q(severity="low")),
            avg_cov=Avg("coverage_score"),
        )
    ):
        gap_data[row["analysis_job_id"]] = {
            "weighted": row["high"] * 3 + row["med"] * 2 + row["low"],
            "avg_cov": row["avg_cov"],
        }

    # Batch: cluster counts per job
    cluster_counts = {}
    for row in (
        TopicCluster.objects.filter(analysis_job_id__in=job_ids)
        .values("analysis_job_id")
        .annotate(cnt=Count("id"))
    ):
        cluster_counts[row["analysis_job_id"]] = row["cnt"]

    # Batch: avg cohesion per job
    cohesion_data = {}
    for row in (
        ClusterMembership.objects.filter(cluster__analysis_job_id__in=job_ids)
        .values("cluster__analysis_job_id")
        .annotate(avg=Avg("similarity_to_centroid"))
    ):
        cohesion_data[row["cluster__analysis_job_id"]] = row["avg"]

    # Batch: audit axis scores for all linked audits
    audit_scores = {}  # {analysis_job_id: {axis: score}}
    completed_audit_ids = []
    audit_to_analysis = {}
    for jid in job_ids:
        aj = audit_map.get(jid)
        if aj and aj.status == AuditJob.Status.COMPLETED:
            completed_audit_ids.append(aj.id)
            audit_to_analysis[aj.id] = jid
    if completed_audit_ids:
        for row in (
            AuditAxisResult.objects.filter(audit_job_id__in=completed_audit_ids)
            .values("audit_job_id", "axis", "score")
        ):
            analysis_jid = audit_to_analysis[row["audit_job_id"]]
            audit_scores.setdefault(analysis_jid, {})[row["axis"]] = row["score"]

    # Compute scores per job using shared formula
    results = {}
    for jid in job_ids:
        axis = audit_scores.get(jid, {})
        gd = gap_data.get(jid, {"weighted": 0, "avg_cov": None})

        score, _breakdown = compute_penalty_score(
            total_docs=total_docs,
            dup_count=dup_counts.get(jid, 0),
            weighted_contra=contra_data.get(jid, 0),
            weighted_gaps=gd["weighted"],
            avg_coverage=gd["avg_cov"],
            avg_cohesion=cohesion_data.get(jid),
            cluster_count=cluster_counts.get(jid, 0),
            health=health,
            audit_coverage=axis.get("coverage"),
            audit_structure=axis.get("structure"),
            audit_retrievability=axis.get("retrievability"),
            audit_hygiene=axis.get("hygiene"),
            audit_governance=axis.get("governance"),
            audit_coherence=axis.get("coherence"),
        )
        results[jid] = {"grade": grade(score), "score": score}

    return results


def _format_eta(seconds):
    """Format seconds into a concise French duration string."""
    if seconds is None or seconds < 0:
        return None
    seconds = int(seconds)
    if seconds < 5:
        return "< 5 s"
    if seconds < 60:
        return f"~{seconds} s"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"~{minutes} min {secs:02d} s" if secs else f"~{minutes} min"
    hours = minutes // 60
    mins = minutes % 60
    return f"~{hours} h {mins:02d} min"


def _analysis_progress_context(job):
    """Build context dict for analysis progress partial."""
    should_poll = job.status in (AnalysisJob.Status.QUEUED, AnalysisJob.Status.RUNNING)

    # Compute overall pipeline ETA from elapsed time and progress %
    overall_eta = None
    if job.status == AnalysisJob.Status.RUNNING and job.started_at and job.progress_pct > 0:
        elapsed = (timezone.now() - job.started_at).total_seconds()
        if elapsed > 0 and job.progress_pct < 100:
            total_estimated = elapsed / (job.progress_pct / 100)
            overall_eta = _format_eta(total_estimated - elapsed)

    # Sub-step ETA from the phase_detail written by the callback
    sub_eta = None
    if job.phase_detail and job.phase_detail.get("eta_seconds") is not None:
        sub_eta = _format_eta(job.phase_detail["eta_seconds"])

    return {
        "job": job,
        "should_poll": should_poll,
        "overall_eta": overall_eta,
        "sub_eta": sub_eta,
    }


# Ordered pipeline phases for the progress stepper
_PIPELINE_PHASES = [
    ("duplicates", _lazy("Détection des doublons"), "M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2M9 2h6v4H9z"),
    ("claims", _lazy("Extraction des affirmations"), "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8"),
    ("semantic_graph", _lazy("Graphe sémantique"), "M5.5 4.5l3 3M18 13l-3-3M12 2v4M2 12h4M18 12h4M12 18v4M7.5 7.5a5 5 0 107 7 5 5 0 00-7-7z"),
    ("clustering", _lazy("Clustering thématique"), "M12 2a4 4 0 014 4 4 4 0 01-4 4 4 4 0 01-4-4 4 4 0 014-4zM4.93 15.5A8 8 0 0112 12a8 8 0 017.07 3.5"),
    ("gaps", _lazy("Détection des lacunes"), "M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.35-4.35"),
    ("tree", _lazy("Index arborescent"), "M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"),
    ("contradictions", _lazy("Détection des contradictions"), "M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0zM12 9v4M12 17h.01"),
    ("hallucination", _lazy("Risques d'hallucination"), "M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0zM9 9h.01M15 9h.01"),
]

_AUDIT_PHASES = [
    ("audit_hygiene", _lazy("Hygiène du corpus"), "M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"),
    ("audit_structure", _lazy("Structure RAG"), "M4 6h16M4 10h16M4 14h16M4 18h16"),
    ("audit_coverage", _lazy("Couverture sémantique"), "M12 2a10 10 0 100 20 10 10 0 000-20zM2 12h20"),
    ("audit_coherence", _lazy("Cohérence interne"), "M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"),
    ("audit_retrievability", _lazy("Retrievability"), "M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.35-4.35M11 8v6M8 11h6"),
    ("audit_governance", _lazy("Gouvernance"), "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"),
]


def _analysis_progress_page_context(job):
    """Build context for the dedicated progress page stepper."""
    current_progress = UNIFIED_PROGRESS.get(job.current_phase, 0)

    def build_phases(phase_list):
        result = []
        for key, label, icon_svg in phase_list:
            phase_progress = UNIFIED_PROGRESS.get(key, 0)
            if current_progress > phase_progress:
                status = "done"
            elif job.current_phase == key:
                status = "active"
            else:
                status = "pending"
            result.append({
                "key": key,
                "label": label,
                "icon_svg": icon_svg,
                "status": status,
                "progress_pct": phase_progress,
            })
        return result

    analysis_phases = build_phases(_PIPELINE_PHASES)
    audit_phases = build_phases(_AUDIT_PHASES)

    ctx = _analysis_progress_context(job)
    ctx["analysis_phases"] = analysis_phases
    ctx["audit_phases"] = audit_phases
    return ctx


def _analysis_results_context(job):
    """Build context dict for analysis results partial."""
    should_poll = job.status in (AnalysisJob.Status.QUEUED, AnalysisJob.Status.RUNNING)
    return {
        "job": job,
        "dup_count": DuplicateGroup.objects.filter(analysis_job=job).count(),
        "contra_count": ContradictionPair.objects.filter(analysis_job=job).exclude(resolution="resolved").count(),
        "cluster_count": TopicCluster.objects.filter(analysis_job=job).count(),
        "gap_count": GapReport.objects.filter(analysis_job=job).exclude(resolution="resolved").count(),
        "hallu_count": HallucinationReport.objects.filter(analysis_job=job).exclude(resolution="resolved").count(),
        "doc_count": Document.objects.filter(project=job.project).count(),
        "docuscore": compute_docuscore(job.project),
        "should_poll": should_poll,
    }


@login_required
def analysis_list(request):
    if not request.tenant:
        return redirect("tenant-select")
    context = _analysis_jobs_context(request.project)
    context["analysis_config_json"] = json.dumps(settings.ANALYSIS_CONFIG)
    can_run, block_reason = can_run_analysis(request.project)
    context["can_run_analysis"] = can_run
    context["analysis_block_reason"] = block_reason
    return render(request, "analysis/list.html", context)


_ALLOWED_CONFIG_SECTIONS = {
    "duplicate", "contradiction", "clustering", "gap_detection", "hallucination",
    "use_batch_api",
}


def _validate_config_overrides(overrides: dict) -> dict:
    """Whitelist allowed config sections and ensure only scalar values."""
    clean = {}
    for key, val in overrides.items():
        if key not in _ALLOWED_CONFIG_SECTIONS:
            continue
        if isinstance(val, dict):
            section = {}
            for k, v in val.items():
                if isinstance(v, (int, float, bool, str, type(None))):
                    section[k] = v
            if section:
                clean[key] = section
        elif isinstance(val, (int, float, bool, str, type(None))):
            clean[key] = val
    return clean


@login_required
@require_POST
@ratelimit(max_calls=5, period=60)
def analysis_run(request):
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("analysis-list")

    can_run, _ = can_run_analysis(request.project)
    if not can_run:
        return redirect("analysis-list")

    raw = request.POST.get("config_overrides", "").strip()
    config_overrides = {}
    if raw:
        try:
            config_overrides = _validate_config_overrides(json.loads(raw))
        except (json.JSONDecodeError, TypeError, AttributeError):
            config_overrides = {}

    job = AnalysisJob.objects.create(
        tenant=request.tenant,
        project=request.project,
        status=AnalysisJob.Status.QUEUED,
        includes_audit=True,
        config_overrides=config_overrides,
    )
    task = run_unified_pipeline.delay(str(job.id))
    job.celery_task_id = task.id
    job.save()

    return redirect("analysis-detail", pk=job.id)


@login_required
@require_POST
def analysis_retry(request, pk):
    """Re-queue a queued or failed analysis job.

    Preserves ``current_phase`` so the pipeline resumes from the failed
    phase instead of restarting from scratch.
    """
    from analysis.tasks import AUDIT_PHASE_ORDER

    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("analysis-list")

    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    if job.status not in (AnalysisJob.Status.QUEUED, AnalysisJob.Status.FAILED, AnalysisJob.Status.CANCELLED):
        return redirect("analysis-detail", pk=pk)

    # Only delete audit jobs if resuming from an analysis phase (audit hasn't
    # started yet).  When resuming from an audit phase we keep the AuditJob so
    # already-completed axes are preserved.
    if job.current_phase not in AUDIT_PHASE_ORDER:
        job.audit_jobs.all().delete()

    # Delete old trace — the task creates a fresh one on each attempt
    try:
        job.trace.delete()
    except PipelineTrace.DoesNotExist:
        pass

    # Do NOT reset current_phase or progress_pct — they act as checkpoints
    job.status = AnalysisJob.Status.QUEUED
    job.error_message = ""
    job.started_at = None
    job.completed_at = None
    job.save()

    task = run_unified_pipeline.delay(str(job.id))
    job.celery_task_id = task.id
    job.save()

    logger.info("Retried analysis job=%s (resume from %s)", pk, job.current_phase)
    return redirect("analysis-detail", pk=pk)


@login_required
@require_POST
def analysis_delete(request, pk):
    """Delete an analysis job and all its results."""
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("analysis-list")

    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    log_audit(
        tenant=request.tenant, user=request.user,
        action=AuditLog.Action.ANALYSIS_DELETED, target=job,
    )
    logger.info("Deleting analysis job=%s", pk)
    job.delete()

    return redirect("analysis-list")


@login_required
@require_POST
def analysis_cancel(request, pk):
    """Cancel a running or queued analysis job."""
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("analysis-list")

    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    if job.status not in (AnalysisJob.Status.QUEUED, AnalysisJob.Status.RUNNING):
        return redirect("analysis-detail", pk=pk)

    # Revoke the Celery task
    if job.celery_task_id:
        from docuscore.celery import app as celery_app
        celery_app.control.revoke(job.celery_task_id, terminate=True)

    job.status = AnalysisJob.Status.CANCELLED
    job.error_message = _("Annulé par l\u2019utilisateur.")
    job.completed_at = timezone.now()
    job.save()

    logger.info("Cancelled analysis job=%s", pk)
    return redirect("analysis-detail", pk=pk)


@login_required
def analysis_detail(request, pk):
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)

    # Dedicated progress page while job is running
    if job.status in (AnalysisJob.Status.QUEUED, AnalysisJob.Status.RUNNING):
        context = _analysis_progress_page_context(job)
        return render(request, "analysis/progress.html", context)

    context = {"job": job}
    context.update(_analysis_progress_context(job))
    context.update(_analysis_results_context(job))

    # --- Report summary charts data ---
    # Duplicates by recommended action
    dup_groups = DuplicateGroup.objects.filter(analysis_job=job)
    dup_by_action = {}
    for g in dup_groups:
        label = str(g.get_recommended_action_display())
        dup_by_action[label] = dup_by_action.get(label, 0) + 1
    context["dup_by_action_json"] = json.dumps(
        [{"name": k, "value": v} for k, v in dup_by_action.items()]
    )

    # Chart data (extracted to presenters)
    context.update(contradiction_chart_data(job))
    context.update(gap_chart_data(job))
    context.update(hallucination_chart_data(job))

    # Concept graph availability
    graph_path = graph_dir(str(job.project_id)) / "graph.json"
    context["has_graph"] = graph_path.exists()

    context["top_issues"] = _build_job_issues(job)

    if job.status == AnalysisJob.Status.COMPLETED:
        detail = compute_docuscore_detail(job.project)
        context["top_recommendations"] = detail.get("top_recommendations", [])

    # DocuScore widget data
    ds = compute_docuscore(job.project)
    context["docuscore"] = ds
    context["breakdown_json"] = build_breakdown_json(ds["breakdown"])

    return render(request, "analysis/detail.html", context)


@login_required
def analysis_audit_overview(request, pk):
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    linked_audit = job.audit_jobs.first()
    context = {"job": job, "linked_audit": linked_audit, "analysis_number": analysis_number(job)}

    if linked_audit and linked_audit.status == AuditJob.Status.COMPLETED:
        results = AuditAxisResult.objects.filter(audit_job=linked_audit)
        results_map = {r.axis: r for r in results}
        axes = []
        for key in AXIS_ORDER:
            r = results_map.get(key)
            sub_list = []
            if r and r.metrics.get("sub_scores"):
                subs = r.metrics["sub_scores"]
                sub_list = [
                    {
                        "key": k,
                        "label": SUB_SCORE_LABELS.get(k, k.replace("_", " ").title()),
                        "value": v,
                        "color": SUB_COLORS[i % len(SUB_COLORS)],
                    }
                    for i, (k, v) in enumerate(subs.items())
                ]
            axes.append({
                "key": key,
                "label": AXIS_LABELS[key],
                "icon": AXIS_ICONS[key],
                "color": AXIS_COLORS.get(key, "#6c717e"),
                "tooltip": AXIS_TOOLTIPS.get(key, ""),
                "result": r,
                "score": r.score if r else None,
                "url": reverse(f"audit-{key}", kwargs={"pk": linked_audit.id}) if r else None,
                "sub_scores": sub_list,
            })
        total_score = sum(a["score"] or 0 for a in axes) or 1
        for a in axes:
            a["score_pct"] = round((a["score"] or 0) / total_score * 100)
        context["audit_axes"] = axes
        context["axes_json"] = json.dumps([
            {"label": str(a["label"]), "score": a["score"] or 0, "color": a["color"]}
            for a in axes
        ])
        context["radar_data_json"] = json.dumps(
            [{"axis": str(a["label"]), "score": a["score"] or 0} for a in axes]
        )
        coverage_result = results_map.get("coverage")
        if coverage_result and coverage_result.chart_data:
            context["coverage_chart_data_json"] = json.dumps(coverage_result.chart_data)

    return render(request, "analysis/audit_overview.html", context)



# Report views moved to analysis/views_reports.py:
# duplicates_report, contradictions_report, clusters_view, gaps_report, tree_view,
# trace_view, knowledge_map_view
from analysis.views_reports import (  # noqa: F401
    clusters_view,
    contradiction_batch_resolve,
    contradiction_resolve,
    contradictions_report,
    duplicates_report,
    gap_batch_resolve,
    gap_resolve,
    gaps_report,
    hallucination_batch_resolve,
    hallucination_report,
    hallucination_resolve,
    knowledge_map_view,
    trace_view,
    tree_view,
)



# JSON API views moved to analysis/views_json.py:
# clusters_json, tree_json, concept_graph_json, concept_graph_query
from analysis.views_json import (  # noqa: F401
    clusters_json,
    concept_graph_json,
    concept_graph_query,
    tree_json,
)


@login_required
def analysis_jobs_partial(request):
    if not request.tenant:
        return redirect("tenant-select")
    context = _analysis_jobs_context(request.project)
    return render(request, "analysis/_jobs_table.html", context)


@login_required
def analysis_progress_partial(request, pk):
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    context = _analysis_progress_context(job)
    return render(request, "analysis/_progress.html", context)


@login_required
def analysis_progress_full_partial(request, pk):
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    context = _analysis_progress_page_context(job)
    return render(request, "analysis/_progress_full.html", context)


@login_required
def analysis_results_partial(request, pk):
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    context = _analysis_results_context(job)
    return render(request, "analysis/_results.html", context)


