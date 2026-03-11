"""Shared issue/suggestion builders used by dashboard and analysis views."""
from django.utils.translation import gettext as _

from analysis.models import (
    ContradictionPair,
    DuplicateGroup,
    GapReport,
    HallucinationReport,
)
from ingestion.models import Document


def build_analysis_issues(job, *, exclude_resolved=False, include_hallucinations=False):
    """Build priority issues for a completed analysis job.

    Parameters
    ----------
    job : AnalysisJob
        A completed analysis job.
    exclude_resolved : bool
        If True, exclude items whose resolution is "resolved".
    include_hallucinations : bool
        If True, include hallucination risk issues.

    Returns a list of issue dicts with keys:
        severity, title, detail, action_label, action_url_name, action_pk
    """
    issues = []

    # High-severity contradictions
    contra_qs = ContradictionPair.objects.filter(
        analysis_job=job, severity="high",
        classification__in=["contradiction", "outdated"],
    )
    if exclude_resolved:
        contra_qs = contra_qs.exclude(resolution="resolved")
    high_contras = contra_qs.count()
    if high_contras:
        issues.append({
            "severity": "high",
            "title": _("%(count)s contradiction(s) critique(s)") % {"count": high_contras},
            "detail": _("Des affirmations contradictoires de haute sévérité ont été détectées."),
            "action_label": _("Voir les contradictions"),
            "action_url_name": "analysis-contradictions",
            "action_pk": str(job.pk),
        })

    # Actionable duplicates
    dup_actionable = (
        DuplicateGroup.objects.filter(analysis_job=job)
        .exclude(recommended_action=DuplicateGroup.Action.KEEP)
        .count()
    )
    if dup_actionable:
        issues.append({
            "severity": "medium",
            "title": _("%(count)s groupe(s) de doublons à traiter") % {"count": dup_actionable},
            "detail": _("Des documents redondants alourdissent votre base et peuvent fausser les résultats RAG."),
            "action_label": _("Voir les doublons"),
            "action_url_name": "analysis-duplicates",
            "action_pk": str(job.pk),
        })

    # High-severity gaps
    gap_qs = GapReport.objects.filter(analysis_job=job, severity="high")
    if exclude_resolved:
        gap_qs = gap_qs.exclude(resolution="resolved")
    high_gaps = gap_qs.count()
    if high_gaps:
        issues.append({
            "severity": "medium",
            "title": _("%(count)s lacune(s) de couverture critique(s)") % {"count": high_gaps},
            "detail": _("Des sujets importants ne sont pas couverts par votre documentation."),
            "action_label": _("Voir les lacunes"),
            "action_url_name": "analysis-gaps",
            "action_pk": str(job.pk),
        })

    # Hallucination risks
    if include_hallucinations:
        high_hallu = HallucinationReport.objects.filter(
            analysis_job=job, severity="high",
        ).exclude(resolution="resolved").count()
        if high_hallu:
            issues.append({
                "severity": "high",
                "title": _("%(count)s risque(s) d'hallucination critique(s)") % {"count": high_hallu},
                "detail": _("Des éléments du corpus (acronymes, jargon) peuvent provoquer des hallucinations RAG."),
                "action_label": _("Voir les risques"),
                "action_url_name": "analysis-hallucinations",
                "action_pk": str(job.pk),
            })

    # Error documents
    error_docs = Document.objects.filter(
        project=job.project, status=Document.Status.ERROR,
    ).count()
    if error_docs:
        issues.append({
            "severity": "low",
            "title": _("%(count)s document(s) en erreur") % {"count": error_docs},
            "detail": _("Certains documents n'ont pas pu être ingérés correctement."),
            "action_label": _("Gérer les connecteurs"),
            "action_url_name": "connector-list",
            "action_pk": None,
        })

    return issues
