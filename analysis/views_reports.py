"""Analysis sub-report views: duplicates, contradictions, clusters, gaps, tree, trace, knowledge map."""
import json
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from analysis.models import (
    AnalysisJob,
    ContradictionPair,
    DuplicateGroup,
    GapReport,
    HallucinationReport,
    PhaseTrace,
    PipelineTrace,
    TopicCluster,
    TraceEvent,
)
from analysis.semantic_graph import graph_dir

ITEMS_PER_PAGE = 20


@login_required
def duplicates_report(request, pk):
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    groups = DuplicateGroup.objects.filter(analysis_job=job).prefetch_related(
        "pairs__doc_a__connector", "pairs__doc_b__connector"
    )
    return render(request, "analysis/duplicates.html", {"job": job, "groups": groups})


def _filter_querystring(type_filter, resolution_filter):
    """Build a query string preserving active filters."""
    params = {}
    if type_filter:
        params["type"] = type_filter
    if resolution_filter:
        params["resolution"] = resolution_filter
    if not params:
        return ""
    return "?" + urlencode(params)


def _contra_filter_querystring(type_filter, resolution_filter):
    """Build a query string preserving active filters."""
    params = {}
    if type_filter:
        params["type"] = type_filter
    if resolution_filter:
        params["resolution"] = resolution_filter
    if not params:
        return ""
    return "?" + urlencode(params)


def _build_next_page_url(url_name, pk, page_obj, **filters):
    """Build the URL for the next page, preserving active filters."""
    if not page_obj.has_next():
        return ""
    params = {"page": page_obj.next_page_number()}
    params.update({k: v for k, v in filters.items() if v})
    return reverse(url_name, kwargs={"pk": pk}) + "?" + urlencode(params)


@login_required
def contradictions_report(request, pk):
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    qs = ContradictionPair.objects.filter(
        analysis_job=job
    ).select_related(
        "claim_a__document__connector", "claim_b__document__connector"
    ).order_by("-confidence")

    type_filter = request.GET.get("type", "")
    if type_filter in ("contradiction", "outdated"):
        qs = qs.filter(classification=type_filter)

    resolution_filter = request.GET.get("resolution", "")
    if resolution_filter in ("resolved", "kept"):
        qs = qs.filter(resolution=resolution_filter)
    elif resolution_filter == "unresolved":
        qs = qs.filter(resolution="")

    can_edit = bool(
        request.project_membership and request.project_membership.can_edit
    )

    # Pre-build query strings so the template can combine both filters in links
    filter_qs = _contra_filter_querystring(type_filter, resolution_filter)

    paginator = Paginator(qs, ITEMS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    ctx = {
        "job": job,
        "contradictions": page_obj,
        "page_obj": page_obj,
        "type_filter": type_filter,
        "resolution_filter": resolution_filter,
        "filter_qs": filter_qs,
        "can_edit": can_edit,
        "next_page_url": _build_next_page_url(
            "analysis-contradictions", pk, page_obj,
            type=type_filter, resolution=resolution_filter,
        ),
    }

    if request.headers.get("Turbo-Frame"):
        return render(request, "analysis/_contradictions_page.html", ctx)

    return render(request, "analysis/contradictions.html", ctx)


@login_required
@require_POST
def contradiction_resolve(request, pk, contra_pk):
    """Set the resolution status of a ContradictionPair."""
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("analysis-contradictions", pk=pk)

    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    contra = get_object_or_404(ContradictionPair, pk=contra_pk, analysis_job=job)

    resolution = request.POST.get("resolution", "")
    if resolution in ("resolved", "kept", ""):
        contra.resolution = resolution
        contra.save(update_fields=["resolution"])

    # Preserve active filters in redirect
    type_filter = request.POST.get("type_filter", "")
    resolution_filter = request.POST.get("resolution_filter", "")
    qs = _contra_filter_querystring(type_filter, resolution_filter)
    from django.urls import reverse
    return redirect(reverse("analysis-contradictions", kwargs={"pk": pk}) + qs)


@login_required
@require_POST
def contradiction_batch_resolve(request, pk):
    """Set the resolution status of multiple ContradictionPairs at once."""
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("analysis-contradictions", pk=pk)

    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)

    ids = request.POST.getlist("selected")
    resolution = request.POST.get("resolution", "")
    if resolution in ("resolved", "kept", "") and ids:
        ContradictionPair.objects.filter(
            pk__in=ids, analysis_job=job,
        ).update(resolution=resolution)

    # Preserve active filters in redirect
    type_filter = request.POST.get("type_filter", "")
    resolution_filter = request.POST.get("resolution_filter", "")
    qs = _contra_filter_querystring(type_filter, resolution_filter)
    from django.urls import reverse
    return redirect(reverse("analysis-contradictions", kwargs={"pk": pk}) + qs)


@login_required
def clusters_view(request, pk):
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    top_clusters = list(TopicCluster.objects.filter(analysis_job=job, level=0).order_by("label"))
    subclusters = list(TopicCluster.objects.filter(analysis_job=job, level__gt=0).order_by("label"))
    sub_by_parent: dict[str, list] = {}
    for sc in subclusters:
        sub_by_parent.setdefault(str(sc.parent_id), []).append(sc)

    ordered_clusters = []
    for c in top_clusters:
        ordered_clusters.append(c)
        ordered_clusters.extend(sub_by_parent.get(str(c.id), []))

    return render(request, "analysis/clusters.html", {"job": job, "clusters": ordered_clusters})


@login_required
def gaps_report(request, pk):
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    qs = GapReport.objects.filter(analysis_job=job).select_related("related_cluster").order_by("coverage_score")

    type_filter = request.GET.get("type", "")
    valid_gap_types = {c[0] for c in GapReport.GapType.choices}
    if type_filter in valid_gap_types:
        qs = qs.filter(gap_type=type_filter)

    resolution_filter = request.GET.get("resolution", "")
    if resolution_filter in ("resolved", "kept"):
        qs = qs.filter(resolution=resolution_filter)
    elif resolution_filter == "unresolved":
        qs = qs.filter(resolution="")

    can_edit = bool(
        request.project_membership and request.project_membership.can_edit
    )

    paginator = Paginator(qs, ITEMS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    ctx = {
        "job": job,
        "gaps": page_obj,
        "page_obj": page_obj,
        "type_filter": type_filter,
        "resolution_filter": resolution_filter,
        "can_edit": can_edit,
        "next_page_url": _build_next_page_url(
            "analysis-gaps", pk, page_obj,
            type=type_filter, resolution=resolution_filter,
        ),
    }

    if request.headers.get("Turbo-Frame"):
        return render(request, "analysis/_gaps_page.html", ctx)

    return render(request, "analysis/gaps.html", ctx)


@login_required
@require_POST
def gap_resolve(request, pk, gap_pk):
    """Set the resolution status of a GapReport."""
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("analysis-gaps", pk=pk)

    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    gap = get_object_or_404(GapReport, pk=gap_pk, analysis_job=job)

    resolution = request.POST.get("resolution", "")
    if resolution in ("resolved", "kept", ""):
        gap.resolution = resolution
        gap.save(update_fields=["resolution"])

    type_filter = request.POST.get("type_filter", "")
    resolution_filter = request.POST.get("resolution_filter", "")
    qs = _filter_querystring(type_filter, resolution_filter)
    from django.urls import reverse
    return redirect(reverse("analysis-gaps", kwargs={"pk": pk}) + qs)


@login_required
@require_POST
def gap_batch_resolve(request, pk):
    """Set the resolution status of multiple GapReports at once."""
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("analysis-gaps", pk=pk)

    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)

    ids = request.POST.getlist("selected")
    resolution = request.POST.get("resolution", "")
    if resolution in ("resolved", "kept", "") and ids:
        GapReport.objects.filter(
            pk__in=ids, analysis_job=job,
        ).update(resolution=resolution)

    type_filter = request.POST.get("type_filter", "")
    resolution_filter = request.POST.get("resolution_filter", "")
    qs = _filter_querystring(type_filter, resolution_filter)
    from django.urls import reverse
    return redirect(reverse("analysis-gaps", kwargs={"pk": pk}) + qs)


@login_required
def hallucination_report(request, pk):
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    qs = HallucinationReport.objects.filter(analysis_job=job).order_by("-risk_score")

    type_filter = request.GET.get("type", "")
    valid_types = {c[0] for c in HallucinationReport.RiskType.choices}
    if type_filter in valid_types:
        qs = qs.filter(risk_type=type_filter)

    resolution_filter = request.GET.get("resolution", "")
    if resolution_filter in ("resolved", "kept"):
        qs = qs.filter(resolution=resolution_filter)
    elif resolution_filter == "unresolved":
        qs = qs.filter(resolution="")

    can_edit = bool(
        request.project_membership and request.project_membership.can_edit
    )

    paginator = Paginator(qs, ITEMS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    ctx = {
        "job": job,
        "reports": page_obj,
        "page_obj": page_obj,
        "type_filter": type_filter,
        "resolution_filter": resolution_filter,
        "can_edit": can_edit,
        "next_page_url": _build_next_page_url(
            "analysis-hallucinations", pk, page_obj,
            type=type_filter, resolution=resolution_filter,
        ),
    }

    if request.headers.get("Turbo-Frame"):
        return render(request, "analysis/_hallucinations_page.html", ctx)

    return render(request, "analysis/hallucinations.html", ctx)


@login_required
@require_POST
def hallucination_resolve(request, pk, hallu_pk):
    """Set the resolution status of a HallucinationReport."""
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("analysis-hallucinations", pk=pk)

    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    report = get_object_or_404(HallucinationReport, pk=hallu_pk, analysis_job=job)

    resolution = request.POST.get("resolution", "")
    if resolution in ("resolved", "kept", ""):
        report.resolution = resolution
        report.save(update_fields=["resolution"])

    type_filter = request.POST.get("type_filter", "")
    resolution_filter = request.POST.get("resolution_filter", "")
    qs = _filter_querystring(type_filter, resolution_filter)
    return redirect(reverse("analysis-hallucinations", kwargs={"pk": pk}) + qs)


@login_required
@require_POST
def hallucination_batch_resolve(request, pk):
    """Set the resolution status of multiple HallucinationReports at once."""
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("analysis-hallucinations", pk=pk)

    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)

    ids = request.POST.getlist("selected")
    resolution = request.POST.get("resolution", "")
    if resolution in ("resolved", "kept", "") and ids:
        HallucinationReport.objects.filter(
            pk__in=ids, analysis_job=job,
        ).update(resolution=resolution)

    type_filter = request.POST.get("type_filter", "")
    resolution_filter = request.POST.get("resolution_filter", "")
    qs = _filter_querystring(type_filter, resolution_filter)
    return redirect(reverse("analysis-hallucinations", kwargs={"pk": pk}) + qs)


@login_required
def tree_view(request, pk):
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    return render(request, "analysis/tree.html", {"job": job})


@login_required
def trace_view(request, pk):
    """Pipeline trace: tokens, timings, and event log."""
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    context = {"job": job}

    try:
        trace = job.trace
    except PipelineTrace.DoesNotExist:
        trace = None

    context["trace"] = trace

    if trace:
        phases = list(PhaseTrace.objects.filter(pipeline_trace=trace).order_by("sort_order"))
        context["phases"] = phases

        context["phase_timeline_json"] = json.dumps([
            {
                "label": p.phase_label,
                "duration": round(p.duration_seconds, 2),
                "status": p.status,
            }
            for p in phases
            if p.status != "running"
        ])

        context["token_dist_json"] = json.dumps([
            {
                "label": p.phase_label,
                "prompt": p.prompt_tokens,
                "completion": p.completion_tokens,
            }
            for p in phases
            if p.prompt_tokens or p.completion_tokens
        ])

        events = list(
            TraceEvent.objects.filter(phase_trace__pipeline_trace=trace)
            .select_related("phase_trace")
            .order_by("timestamp")
        )
        context["events"] = events
        event_types = sorted(set(e.event_type for e in events))
        context["event_types"] = event_types
        context["event_type_labels"] = dict(TraceEvent.EventType.choices)

    return render(request, "analysis/trace.html", context)


@login_required
def knowledge_map_view(request, pk):
    """HTML page for the concept graph (knowledge map)."""
    job = get_object_or_404(AnalysisJob, pk=pk, project=request.project)
    graph_path = graph_dir(str(job.project_id)) / "graph.json"
    return render(request, "analysis/knowledge_map.html", {
        "job": job,
        "has_graph": graph_path.exists(),
    })
