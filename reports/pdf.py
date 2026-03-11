"""PDF report generation using xhtml2pdf."""

import logging
import math

from django.template.loader import render_to_string

from analysis.models import (
    AuditAxisResult,
    AuditJob,
    ContradictionPair,
    DuplicateGroup,
    DuplicatePair,
    GapReport,
    HallucinationReport,
)
from connectors.models import ConnectorConfig
from score.scoring import compute_score, compute_score_detail

logger = logging.getLogger(__name__)


# ── SVG chart helpers (pure geometry, no I/O) ──────────────────────────


def _radar_points(dimensions, cx=140, cy=140, r=110):
    """Return SVG polygon *points* string for a radar chart."""
    n = len(dimensions)
    if n == 0:
        return ""
    pts = []
    for i, dim in enumerate(dimensions):
        score = dim.get("score") or 0
        angle = math.radians(-90 + i * 360 / n)
        pts.append(
            f"{cx + r * (score / 100) * math.cos(angle):.1f},"
            f"{cy + r * (score / 100) * math.sin(angle):.1f}"
        )
    return " ".join(pts)


def _radar_axes(dimensions, cx=140, cy=140, r=110):
    """Return list of dicts with axis line endpoints and label positions."""
    n = len(dimensions)
    axes = []
    for i, dim in enumerate(dimensions):
        angle = math.radians(-90 + i * 360 / n)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        # Determine text-anchor based on x position
        lx = cx + (r + 20) * cos_a
        if lx < cx - 5:
            anchor = "end"
        elif lx > cx + 5:
            anchor = "start"
        else:
            anchor = "middle"
        axes.append(
            {
                "x": round(cx + r * cos_a, 1),
                "y": round(cy + r * sin_a, 1),
                "lx": round(lx, 1),
                "ly": round(cy + (r + 20) * sin_a + 4, 1),
                "name": dim.get("name", ""),
                "score": dim.get("score"),
                "anchor": anchor,
            }
        )
    return axes


def _radar_grid_rings(cx=140, cy=140, r=110, steps=4):
    """Return list of (radius, label) for concentric grid circles."""
    return [
        {"r": round(r * (i + 1) / steps, 1), "label": (i + 1) * 100 // steps} for i in range(steps)
    ]


def _donut_data(score, radius=54, stroke=14):
    """Return dict with SVG stroke-dasharray params for a donut chart."""
    circ = 2 * math.pi * radius
    filled = circ * (score or 0) / 100
    return {
        "radius": radius,
        "stroke": stroke,
        "viewbox": 2 * (radius + stroke),
        "center": radius + stroke,
        "circumference": round(circ, 1),
        "filled": round(filled, 1),
    }


def _findings_summary(dup_groups, contradictions, gaps, hallucinations=None):
    """Build chart-friendly findings summary dicts."""
    hallucinations = hallucinations or []

    # Dup actions
    action_counts = {}
    for g in dup_groups:
        action_counts[g.recommended_action] = action_counts.get(g.recommended_action, 0) + 1

    # Contradiction severity
    contra_sev = {"high": 0, "medium": 0, "low": 0}
    for c in contradictions:
        if c.severity in contra_sev:
            contra_sev[c.severity] += 1

    # Gap type + severity
    gap_types = {}
    gap_sev = {"high": 0, "medium": 0, "low": 0}
    for g in gaps:
        gap_types[g.gap_type] = gap_types.get(g.gap_type, 0) + 1
        if g.severity in gap_sev:
            gap_sev[g.severity] += 1

    # Hallucination type + severity
    hallu_types = {}
    hallu_sev = {"high": 0, "medium": 0, "low": 0}
    for h in hallucinations:
        hallu_types[h.risk_type] = hallu_types.get(h.risk_type, 0) + 1
        if h.severity in hallu_sev:
            hallu_sev[h.severity] += 1

    total_findings = len(dup_groups) + len(contradictions) + len(gaps) + len(hallucinations)

    return {
        "total": total_findings,
        "dup_actions": action_counts,
        "dup_total": len(dup_groups),
        "contra_sev": contra_sev,
        "contra_total": len(contradictions),
        "gap_types": gap_types,
        "gap_sev": gap_sev,
        "gap_total": len(gaps),
        "hallu_types": hallu_types,
        "hallu_sev": hallu_sev,
        "hallu_total": len(hallucinations),
    }


def gather_pdf_context(job):
    """Collect all data needed for the PDF report in optimised queries."""
    project = job.project

    # --- Connectors ---
    connectors = list(ConnectorConfig.objects.filter(project=project).order_by("name"))

    # --- SCORE ---
    ds = compute_score(project)
    score_detail = compute_score_detail(project)

    # --- Duplicates ---
    dup_groups = list(DuplicateGroup.objects.filter(analysis_job=job).order_by("-created_at"))
    # Fetch all pairs with connector info in one query
    dup_pairs = list(
        DuplicatePair.objects.filter(group__analysis_job=job)
        .select_related(
            "doc_a__connector",
            "doc_b__connector",
            "group",
        )
        .order_by("-combined_score")
    )
    # Attach pairs to their group for template iteration
    pairs_by_group_id = {}
    for pair in dup_pairs:
        pairs_by_group_id.setdefault(pair.group_id, []).append(pair)
    for group in dup_groups:
        group.pdf_pairs = pairs_by_group_id.get(group.id, [])

    # --- Contradictions ---
    contradictions = list(
        ContradictionPair.objects.filter(analysis_job=job)
        .select_related(
            "claim_a__document__connector",
            "claim_b__document__connector",
        )
        .order_by("severity", "-confidence")
    )
    # Partition by severity for display
    contradictions_high = [c for c in contradictions if c.severity == "high"]
    contradictions_medium = [c for c in contradictions if c.severity == "medium"]
    contradictions_low = [c for c in contradictions if c.severity == "low"]

    # --- Gaps ---
    gaps = list(GapReport.objects.filter(analysis_job=job).order_by("coverage_score"))

    # --- Hallucination risks ---
    hallucinations = list(
        HallucinationReport.objects.filter(analysis_job=job).order_by("-risk_score")
    )
    hallucinations_high = [h for h in hallucinations if h.severity == "high"]
    hallucinations_medium = [h for h in hallucinations if h.severity == "medium"]
    hallucinations_low = [h for h in hallucinations if h.severity == "low"]

    # --- Audit axes ---
    linked_audit = job.audit_jobs.filter(status="completed").first()
    audit_axes = {}
    if linked_audit:
        for result in AuditAxisResult.objects.filter(audit_job=linked_audit):
            audit_axes[result.axis] = result

    # Build dimension bar data for the PDF chart
    dimensions = score_detail.get("dimensions", [])

    # ── SVG chart data ──
    radar_points = _radar_points(dimensions)
    radar_axes = _radar_axes(dimensions)
    radar_grid = _radar_grid_rings()
    donut = _donut_data(ds.get("score", 0))
    findings = _findings_summary(dup_groups, contradictions, gaps, hallucinations)

    return {
        "job": job,
        "project": project,
        "connectors": connectors,
        "ds": ds,
        "score_detail": score_detail,
        "dimensions": dimensions,
        "top_recommendations": score_detail.get("top_recommendations", []),
        "dup_groups": dup_groups,
        "dup_count": len(dup_pairs),
        "contradictions": contradictions,
        "contradictions_high": contradictions_high,
        "contradictions_medium": contradictions_medium,
        "contradictions_low": contradictions_low,
        "gaps": gaps,
        "hallucinations": hallucinations,
        "hallucinations_high": hallucinations_high,
        "hallucinations_medium": hallucinations_medium,
        "hallucinations_low": hallucinations_low,
        "audit_axes": audit_axes,
        "linked_audit": linked_audit,
        "audit_axis_labels": dict(AuditJob.Axis.choices),
        # Chart data
        "radar_points": radar_points,
        "radar_axes": radar_axes,
        "radar_grid": radar_grid,
        "donut": donut,
        "findings": findings,
    }


def render_pdf(context):
    """Render the PDF report template to bytes via xhtml2pdf."""
    import io

    from xhtml2pdf import pisa

    html_string = render_to_string("reports/pdf_report.html", context)
    buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(html_string, dest=buffer)
    if pisa_status.err:
        logger.error("PDF generation failed with %d error(s)", pisa_status.err)
        raise RuntimeError("PDF generation failed")
    return buffer.getvalue()
