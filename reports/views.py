"""Report views: generate and export reports."""
import csv
import io
import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from analysis.models import (
    AnalysisJob,
    AuditAxisResult,
    ContradictionPair,
    DuplicateGroup,
    DuplicatePair,
    GapReport,
)
from reports.models import Report
from reports.pdf import gather_pdf_context, render_pdf


@login_required
def report_list(request):
    if not request.tenant:
        return redirect("tenant-select")
    reports = Report.objects.filter(project=request.project).order_by("-created_at")[:20]
    analyses = AnalysisJob.objects.filter(project=request.project).filter(
        status=AnalysisJob.Status.COMPLETED
    ).order_by("-created_at")[:10]
    return render(request, "reports/list.html", {"reports": reports, "analyses": analyses})


@login_required
def export_duplicates_csv(request, job_pk):
    """Export duplicates report as CSV."""
    job = get_object_or_404(AnalysisJob, pk=job_pk, project=request.project)
    pairs = DuplicatePair.objects.filter(
        group__analysis_job=job
    ).select_related("doc_a", "doc_b", "group")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="duplicates_{str(job.id)[:8]}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Group Action", "Doc A Title", "Doc A URL", "Doc B Title", "Doc B URL",
        "Semantic Score", "Lexical Score", "Metadata Score", "Combined Score",
        "Verification", "Confidence", "Evidence",
    ])

    for pair in pairs:
        writer.writerow([
            pair.group.recommended_action,
            pair.doc_a.title, pair.doc_a.source_url,
            pair.doc_b.title, pair.doc_b.source_url,
            f"{pair.semantic_score:.3f}",
            f"{pair.lexical_score:.3f}",
            f"{pair.metadata_score:.3f}",
            f"{pair.combined_score:.3f}",
            pair.verification_result,
            f"{pair.verification_confidence:.3f}" if pair.verification_confidence else "",
            pair.verification_evidence[:500],
        ])

    return response


@login_required
def export_contradictions_csv(request, job_pk):
    """Export contradictions report as CSV."""
    job = get_object_or_404(AnalysisJob, pk=job_pk, project=request.project)
    contradictions = ContradictionPair.objects.filter(
        analysis_job=job
    ).select_related("claim_a__document", "claim_b__document")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="contradictions_{str(job.id)[:8]}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Classification", "Severity", "Confidence",
        "Claim A", "Doc A", "Claim B", "Doc B", "Evidence", "Resolution",
    ])

    for c in contradictions:
        writer.writerow([
            c.classification, c.severity, f"{c.confidence:.3f}",
            c.claim_a.as_text, c.claim_a.document.title,
            c.claim_b.as_text, c.claim_b.document.title,
            c.evidence[:500],
            c.resolution,
        ])

    return response


@login_required
def export_gaps_csv(request, job_pk):
    """Export gaps report as CSV."""
    job = get_object_or_404(AnalysisJob, pk=job_pk, project=request.project)
    gaps = GapReport.objects.filter(analysis_job=job).select_related("related_cluster")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="gaps_{str(job.id)[:8]}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Type", "Severity", "Title", "Description",
        "Coverage Score", "Related Cluster", "Resolution",
    ])

    for g in gaps:
        writer.writerow([
            g.gap_type, g.severity, g.title,
            g.description[:500],
            f"{g.coverage_score:.2f}" if g.coverage_score is not None else "",
            g.related_cluster.label if g.related_cluster else "",
            g.resolution,
        ])

    return response


@login_required
def export_hallucinations_csv(request, job_pk):
    """Export hallucination risks report as CSV."""
    job = get_object_or_404(AnalysisJob, pk=job_pk, project=request.project)
    from analysis.models import HallucinationReport
    reports = HallucinationReport.objects.filter(analysis_job=job).order_by("-risk_score")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="hallucinations_{str(job.id)[:8]}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Risk Type", "Severity", "Title", "Term",
        "Description", "Risk Score", "Doc Count", "Resolution",
    ])

    for h in reports:
        writer.writerow([
            h.risk_type, h.severity, h.title, h.term,
            h.description[:500],
            f"{h.risk_score:.3f}",
            h.doc_count,
            h.resolution,
        ])

    return response


@login_required
def export_report_json(request, job_pk):
    """Export full analysis report as JSON."""
    job = get_object_or_404(AnalysisJob, pk=job_pk, project=request.project)

    data = {
        "analysis_job": str(job.id),
        "status": job.status,
        "created_at": str(job.created_at),
        "duplicates": [],
        "contradictions": [],
        "gaps": [],
        "hallucinations": [],
    }

    for group in DuplicateGroup.objects.filter(analysis_job=job):
        group_data = {
            "action": group.recommended_action,
            "rationale": group.rationale,
            "pairs": [],
        }
        for pair in DuplicatePair.objects.filter(group=group).select_related("doc_a", "doc_b"):
            group_data["pairs"].append({
                "doc_a": {"title": pair.doc_a.title, "url": pair.doc_a.source_url},
                "doc_b": {"title": pair.doc_b.title, "url": pair.doc_b.source_url},
                "scores": {
                    "semantic": pair.semantic_score,
                    "lexical": pair.lexical_score,
                    "metadata": pair.metadata_score,
                    "combined": pair.combined_score,
                },
                "verification": pair.verification_result,
                "evidence": pair.verification_evidence,
            })
        data["duplicates"].append(group_data)

    for c in ContradictionPair.objects.filter(analysis_job=job).select_related(
        "claim_a__document", "claim_b__document"
    ):
        data["contradictions"].append({
            "classification": c.classification,
            "severity": c.severity,
            "confidence": c.confidence,
            "claim_a": {"text": c.claim_a.as_text, "doc": c.claim_a.document.title},
            "claim_b": {"text": c.claim_b.as_text, "doc": c.claim_b.document.title},
            "evidence": c.evidence,
            "resolution": c.resolution,
        })

    for g in GapReport.objects.filter(analysis_job=job):
        data["gaps"].append({
            "type": g.gap_type,
            "title": g.title,
            "description": g.description,
            "severity": g.severity,
            "coverage_score": g.coverage_score,
            "resolution": g.resolution,
        })

    from analysis.models import HallucinationReport
    for h in HallucinationReport.objects.filter(analysis_job=job):
        data["hallucinations"].append({
            "risk_type": h.risk_type,
            "title": h.title,
            "term": h.term,
            "description": h.description,
            "severity": h.severity,
            "risk_score": h.risk_score,
            "doc_count": h.doc_count,
            "expansions": h.expansions,
            "resolution": h.resolution,
        })

    # Include linked audit data if available
    linked_audit = job.audit_jobs.filter(status="completed").first()
    if linked_audit:
        audit_data = {
            "audit_job": str(linked_audit.id),
            "overall_score": linked_audit.overall_score,
            "overall_grade": linked_audit.overall_grade,
            "axes": {},
        }
        for result in AuditAxisResult.objects.filter(audit_job=linked_audit):
            audit_data["axes"][result.axis] = {
                "score": result.score,
                "metrics": result.metrics,
                "duration_seconds": result.duration_seconds,
            }
        data["audit"] = audit_data

    return JsonResponse(data, json_dumps_params={"indent": 2})


@login_required
def export_report_pdf(request, job_pk):
    """Export full analysis report as PDF."""
    job = get_object_or_404(AnalysisJob, pk=job_pk, project=request.project)
    context = gather_pdf_context(job)
    pdf_bytes = render_pdf(context)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="docuscore_report_{str(job.id)[:8]}.pdf"'
    )
    return response
