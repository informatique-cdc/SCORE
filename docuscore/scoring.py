"""
DocuScore — Nutri-Score-style quality grade for a knowledge base.

Computes a 0-100 score from the latest completed analysis, then maps to
a letter grade A through E.  Seven dimensions are evaluated:

  Unicité         (max penalty 15)  LLM duplicate groups
  Cohérence       (max penalty 15)  LLM contradictions weighted by severity
  Couverture      (max penalty 20)  LLM gaps (12) + audit coverage (8)
  Structure       (max penalty 15)  LLM clusters (9) + audit structure (6)
  Santé           (max penalty 10)  Document pipeline readiness
  Retrievability  (max penalty 15)  Audit retrievability (9) + audit hygiene (6)
  Gouvernance     (max penalty 10)  Audit governance (6) + audit coherence (4)
"""
from django.db.models import Avg
from django.utils.translation import gettext as _

from analysis.models import (
    AnalysisJob,
    AuditAxisResult,
    AuditJob,
    ClusterMembership,
    ContradictionPair,
    DuplicateGroup,
    GapReport,
    TopicCluster,
)
from ingestion.models import Document

# --- Scoring constants ---
# Maximum penalty per dimension (must sum to 100)
MAX_UNIQUENESS_PENALTY = 15
MAX_CONSISTENCY_PENALTY = 15
MAX_COVERAGE_PENALTY = 20
MAX_STRUCTURE_PENALTY = 15
MAX_HEALTH_PENALTY = 10
MAX_RETRIEVABILITY_PENALTY = 15
MAX_GOVERNANCE_PENALTY = 10

# Coverage sub-allocation: LLM gaps vs audit
COVERAGE_LLM_GAPS_MAX = 12
COVERAGE_LLM_DEPTH_MAX = 4
COVERAGE_AUDIT_MAX = 8

# Structure sub-allocation: LLM clusters vs audit
STRUCTURE_LLM_MAX = 9
STRUCTURE_AUDIT_MAX = 6

# Retrievability sub-allocation: retrievability vs hygiene
RETRIEVABILITY_AUDIT_MAX = 9
HYGIENE_AUDIT_MAX = 6

# Governance sub-allocation: governance vs coherence
GOVERNANCE_AUDIT_MAX = 6
COHERENCE_AUDIT_MAX = 4

# Ratio thresholds that normalize penalties
DUP_RATIO_THRESHOLD = 0.30
CONTRA_RATIO_THRESHOLD = 4.0
GAP_RATIO_THRESHOLD = 0.50

# Grade boundaries
GRADE_A_MIN = 80
GRADE_B_MIN = 60
GRADE_C_MIN = 40
GRADE_D_MIN = 20


def _get_linked_audit(latest_job):
    """Get the audit linked to an analysis job, or None."""
    if not latest_job:
        return None
    return latest_job.audit_jobs.filter(status=AuditJob.Status.COMPLETED).first()


def _get_audit_axis_score(audit_job, axis_key):
    """Get the score for a specific audit axis, or None."""
    if not audit_job:
        return None
    try:
        result = AuditAxisResult.objects.get(audit_job=audit_job, axis=axis_key)
        return result.score
    except AuditAxisResult.DoesNotExist:
        return None


def compute_docuscore(project):
    """Return a dict with grade, score (0-100), breakdown, and metadata."""
    docs_qs = Document.objects.filter(project=project).exclude(
        status=Document.Status.DELETED
    )
    total_docs = docs_qs.count()

    if total_docs == 0:
        return _empty_result(has_docs=False, has_analysis=False)

    ready_docs = docs_qs.filter(status=Document.Status.READY).count()
    error_docs = docs_qs.filter(status=Document.Status.ERROR).count()

    latest = (
        AnalysisJob.objects.filter(project=project)
        .filter(status=AnalysisJob.Status.COMPLETED)
        .order_by("-created_at")
        .first()
    )

    if not latest:
        health = health_score(ready_docs, error_docs, total_docs)
        score = max(0, round(health * 0.10))  # 10% weight only
        breakdown = {
            "uniqueness": None,
            "consistency": None,
            "coverage": None,
            "structure": None,
            "health": health,
            "retrievability": None,
            "governance": None,
        }
        return {
            "grade": _grade(score),
            "score": score,
            "breakdown": breakdown,
            "has_docs": True,
            "has_analysis": False,
        }

    # --- Full scoring (7 dimensions) ---
    linked_audit = _get_linked_audit(latest)

    # Gather raw metrics
    dup_count = (
        DuplicateGroup.objects.filter(analysis_job=latest)
        .exclude(recommended_action=DuplicateGroup.Action.KEEP)
        .count()
    )

    contras = ContradictionPair.objects.filter(
        analysis_job=latest,
        classification__in=["contradiction", "outdated"],
    ).exclude(resolution="resolved")
    weighted_c = (
        contras.filter(severity="high").count() * 3
        + contras.filter(severity="medium").count() * 2
        + contras.filter(severity="low").count()
    )

    gaps = GapReport.objects.filter(analysis_job=latest).exclude(resolution="resolved")
    weighted_g = (
        gaps.filter(severity="high").count() * 3
        + gaps.filter(severity="medium").count() * 2
        + gaps.filter(severity="low").count()
    )
    avg_coverage = gaps.aggregate(avg=Avg("coverage_score"))["avg"]

    avg_cohesion = ClusterMembership.objects.filter(
        cluster__analysis_job=latest,
    ).aggregate(avg=Avg("similarity_to_centroid"))["avg"]
    cluster_count = TopicCluster.objects.filter(analysis_job=latest).count()

    health = health_score(ready_docs, error_docs, total_docs)

    score, breakdown = compute_penalty_score(
        total_docs=total_docs,
        dup_count=dup_count,
        weighted_contra=weighted_c,
        weighted_gaps=weighted_g,
        avg_coverage=avg_coverage,
        avg_cohesion=avg_cohesion,
        cluster_count=cluster_count,
        health=health,
        audit_coverage=_get_audit_axis_score(linked_audit, "coverage"),
        audit_structure=_get_audit_axis_score(linked_audit, "structure"),
        audit_retrievability=_get_audit_axis_score(linked_audit, "retrievability"),
        audit_hygiene=_get_audit_axis_score(linked_audit, "hygiene"),
        audit_governance=_get_audit_axis_score(linked_audit, "governance"),
        audit_coherence=_get_audit_axis_score(linked_audit, "coherence"),
    )

    return {
        "grade": grade(score),
        "score": score,
        "breakdown": breakdown,
        "has_docs": True,
        "has_analysis": True,
    }


def compute_docuscore_for_job(job):
    """Return grade + score for a specific AnalysisJob (lightweight)."""
    if job.status != AnalysisJob.Status.COMPLETED:
        return None

    project = job.project
    docs_qs = Document.objects.filter(project=project).exclude(
        status=Document.Status.DELETED
    )
    total_docs = docs_qs.count()
    if total_docs == 0:
        return {"grade": "E", "score": 0}

    ready_docs = docs_qs.filter(status=Document.Status.READY).count()
    error_docs = docs_qs.filter(status=Document.Status.ERROR).count()
    linked_audit = _get_linked_audit(job)

    dup_count = (
        DuplicateGroup.objects.filter(analysis_job=job)
        .exclude(recommended_action=DuplicateGroup.Action.KEEP)
        .count()
    )

    contras = ContradictionPair.objects.filter(
        analysis_job=job,
        classification__in=["contradiction", "outdated"],
    ).exclude(resolution="resolved")
    weighted_c = (
        contras.filter(severity="high").count() * 3
        + contras.filter(severity="medium").count() * 2
        + contras.filter(severity="low").count()
    )

    gaps = GapReport.objects.filter(analysis_job=job).exclude(resolution="resolved")
    weighted_g = (
        gaps.filter(severity="high").count() * 3
        + gaps.filter(severity="medium").count() * 2
        + gaps.filter(severity="low").count()
    )
    avg_cov = gaps.aggregate(avg=Avg("coverage_score"))["avg"]

    avg_cohesion = ClusterMembership.objects.filter(
        cluster__analysis_job=job,
    ).aggregate(avg=Avg("similarity_to_centroid"))["avg"]
    cluster_count = TopicCluster.objects.filter(analysis_job=job).count()

    health = health_score(ready_docs, error_docs, total_docs)

    score, _breakdown = compute_penalty_score(
        total_docs=total_docs,
        dup_count=dup_count,
        weighted_contra=weighted_c,
        weighted_gaps=weighted_g,
        avg_coverage=avg_cov,
        avg_cohesion=avg_cohesion,
        cluster_count=cluster_count,
        health=health,
        audit_coverage=_get_audit_axis_score(linked_audit, "coverage"),
        audit_structure=_get_audit_axis_score(linked_audit, "structure"),
        audit_retrievability=_get_audit_axis_score(linked_audit, "retrievability"),
        audit_hygiene=_get_audit_axis_score(linked_audit, "hygiene"),
        audit_governance=_get_audit_axis_score(linked_audit, "governance"),
        audit_coherence=_get_audit_axis_score(linked_audit, "coherence"),
    )
    return {"grade": grade(score), "score": score}


def compute_docuscore_detail(project):
    """Return the full score with per-dimension explanations and recommendations."""
    docs_qs = Document.objects.filter(project=project).exclude(
        status=Document.Status.DELETED
    )
    total_docs = docs_qs.count()

    if total_docs == 0:
        return {
            "score": 0,
            "grade": "E",
            "summary": _("Your knowledge base is empty. Add documents and connectors to get started."),
            "dimensions": [],
            "top_recommendations": [
                {"icon": "plus-circle", "text": _("Configure a connector and ingest your first documents.")},
            ],
        }

    ready_docs = docs_qs.filter(status=Document.Status.READY).count()
    error_docs = docs_qs.filter(status=Document.Status.ERROR).count()

    latest = (
        AnalysisJob.objects.filter(project=project)
        .filter(status=AnalysisJob.Status.COMPLETED)
        .order_by("-created_at")
        .first()
    )

    if not latest:
        health = health_score(ready_docs, error_docs, total_docs)
        score = max(0, round(health * 0.10))
        no_analysis_dims = [
            _dim(_("Health"), health, _("Document pipeline readiness status."),
                 _health_details(ready_docs, error_docs, total_docs),
                 _health_recs(ready_docs, error_docs, total_docs)),
        ]
        no_analysis_recs = [
            {"icon": "play-circle", "text": _("Run your first analysis to evaluate uniqueness, consistency, coverage, and structure.")},
        ]
        return {
            "score": score,
            "grade": _grade(score),
            "summary": _("You have %(count)d documents but no completed analysis. Run an analysis to get your full score.") % {"count": total_docs},
            "dimensions": no_analysis_dims,
            "top_recommendations": no_analysis_recs,
        }

    # --- Full detail (7 dimensions) ---
    linked_audit = _get_linked_audit(latest)
    dims = []
    top_recs = []

    # 1. Unicité
    dup_total = DuplicateGroup.objects.filter(analysis_job=latest).count()
    dup_actionable = (
        DuplicateGroup.objects.filter(analysis_job=latest)
        .exclude(recommended_action=DuplicateGroup.Action.KEEP)
        .count()
    )
    dup_ratio = dup_actionable / total_docs
    uniqueness_penalty = min(15, dup_ratio / 0.30 * 15)
    uniqueness_score = round(100 - uniqueness_penalty / 15 * 100)

    dup_details = _("%(actionable)d actionable duplicate groups out of %(total)d total (%(ratio)s of documents).") % {"actionable": dup_actionable, "total": dup_total, "ratio": f"{dup_ratio:.0%}"}
    dup_recs = []
    if dup_actionable > 0:
        merge_count = DuplicateGroup.objects.filter(
            analysis_job=latest, recommended_action=DuplicateGroup.Action.MERGE
        ).count()
        delete_count = DuplicateGroup.objects.filter(
            analysis_job=latest, recommended_action=DuplicateGroup.Action.DELETE_OLDER
        ).count()
        review_count = DuplicateGroup.objects.filter(
            analysis_job=latest, recommended_action=DuplicateGroup.Action.REVIEW
        ).count()
        if merge_count:
            dup_recs.append(_("Merge %(count)d duplicate group(s) where documents are identical.") % {"count": merge_count})
        if delete_count:
            dup_recs.append(_("Delete older versions in %(count)d group(s) where newer documents replace them.") % {"count": delete_count})
        if review_count:
            dup_recs.append(_("Manually review %(count)d group(s) flagged for inspection.") % {"count": review_count})
        top_recs.append({"icon": "copy", "text": _("Resolve %(count)d duplicate groups to improve uniqueness.") % {"count": dup_actionable}})
    else:
        dup_details = _("No actionable duplicates detected. Your content is unique.")

    dims.append(_dim(_("Uniqueness"), uniqueness_score,
                      _("Measures the absence of duplicated content in the repository."),
                      dup_details, dup_recs))

    # 2. Cohérence
    contras = ContradictionPair.objects.filter(
        analysis_job=latest,
        classification__in=["contradiction", "outdated"],
    ).exclude(resolution="resolved")
    high_c = contras.filter(severity="high").count()
    med_c = contras.filter(severity="medium").count()
    low_c = contras.filter(severity="low").count()
    total_c = high_c + med_c + low_c
    weighted_c = high_c * 3 + med_c * 2 + low_c
    contra_ratio = weighted_c / total_docs
    consistency_penalty = min(15, contra_ratio / 4.0 * 15)
    consistency_score = round(100 - consistency_penalty / 15 * 100)

    contra_recs = []
    if total_c > 0:
        parts = []
        if high_c:
            parts.append(_("%(count)d high") % {"count": high_c})
        if med_c:
            parts.append(_("%(count)d medium") % {"count": med_c})
        if low_c:
            parts.append(_("%(count)d low") % {"count": low_c})
        contra_details = _("%(total)d contradictions found (severity: %(parts)s).") % {"total": total_c, "parts": ", ".join(parts)}
        if high_c:
            contra_recs.append(_("Prioritize resolving %(count)d high-severity contradiction(s) — these are direct factual conflicts.") % {"count": high_c})
        outdated = contras.filter(classification="outdated").count()
        if outdated:
            contra_recs.append(_("Update or remove %(count)d outdated statement(s) superseded by newer information.") % {"count": outdated})
        if med_c + low_c > 0:
            contra_recs.append(_("Review the remaining %(count)d lower-severity contradictions for possible clarifications.") % {"count": med_c + low_c})
        top_recs.append({"icon": "alert-triangle", "text": _("Fix %(total)d contradictions (%(high)d high-severity) to improve consistency.") % {"total": total_c, "high": high_c}})
    else:
        contra_details = _("No contradictions or outdated statements detected. Your content is consistent.")

    dims.append(_dim(_("Consistency"), consistency_score,
                      _("Measures the absence of contradictory or outdated information in the repository."),
                      contra_details, contra_recs))

    # 3. Couverture
    gap_qs = GapReport.objects.filter(analysis_job=latest).exclude(resolution="resolved")
    high_g = gap_qs.filter(severity="high").count()
    med_g = gap_qs.filter(severity="medium").count()
    low_g = gap_qs.filter(severity="low").count()
    total_g = high_g + med_g + low_g
    weighted_g = high_g * 3 + med_g * 2 + low_g
    gap_ratio = weighted_g / total_docs
    llm_gap_penalty = min(12, gap_ratio / 0.50 * 12)

    avg_coverage = gap_qs.aggregate(avg=Avg("coverage_score"))["avg"]
    if avg_coverage is not None:
        llm_gap_penalty += min(4, (1 - avg_coverage) * 4)

    audit_coverage_score_val = _get_audit_axis_score(linked_audit, "coverage")
    audit_cov_penalty = (100 - audit_coverage_score_val) / 100 * 8 if audit_coverage_score_val is not None else 4
    total_coverage_penalty = min(20, llm_gap_penalty + audit_cov_penalty)
    coverage_score = round(100 - total_coverage_penalty / 20 * 100)

    gap_recs = []
    if total_g > 0:
        parts = []
        if high_g:
            parts.append(_("%(count)d high") % {"count": high_g})
        if med_g:
            parts.append(_("%(count)d medium") % {"count": med_g})
        if low_g:
            parts.append(_("%(count)d low") % {"count": low_g})
        avg_str = _(" Average coverage depth: %(avg)s.") % {"avg": f"{avg_coverage:.0%}"} if avg_coverage is not None else ""
        gap_details = _("%(total)d coverage gaps detected (severity: %(parts)s).%(avg)s") % {"total": total_g, "parts": ", ".join(parts), "avg": avg_str}

        missing = gap_qs.filter(gap_type="missing_topic").count()
        stale = gap_qs.filter(gap_type="stale_area").count()
        low_cov = gap_qs.filter(gap_type="low_coverage").count()
        orphan = gap_qs.filter(gap_type="orphan_topic").count()

        if missing:
            gap_recs.append(_("Create documentation for %(count)d missing topic(s) identified by the analysis.") % {"count": missing})
        if stale:
            gap_recs.append(_("Refresh %(count)d stale area(s) that have not been updated recently.") % {"count": stale})
        if low_cov:
            gap_recs.append(_("Expand %(count)d topic(s) with low coverage depth.") % {"count": low_cov})
        if orphan:
            gap_recs.append(_("Integrate %(count)d orphan topic(s) — isolated content not linked to other themes.") % {"count": orphan})
        if high_g:
            top_recs.append({"icon": "target", "text": _("Address %(count)d high-severity coverage gap(s) to improve completeness.") % {"count": high_g}})
    else:
        gap_details = _("No coverage gaps detected. Your knowledge base covers topics well.")
    if audit_coverage_score_val is not None:
        gap_details += _(" Audit coverage score: %(score).0f/100.") % {"score": audit_coverage_score_val}

    dims.append(_dim(_("Coverage"), coverage_score,
                      _("Measures knowledge base completeness (LLM + audit)."),
                      gap_details, gap_recs))

    # 4. Structure
    avg_cohesion = ClusterMembership.objects.filter(
        cluster__analysis_job=latest,
    ).aggregate(avg=Avg("similarity_to_centroid"))["avg"]

    cluster_count = TopicCluster.objects.filter(analysis_job=latest).count()
    llm_sp = 0.0
    if avg_cohesion is not None:
        llm_sp += max(0, (1 - avg_cohesion)) * 6
    else:
        llm_sp += 5
    if cluster_count == 0:
        llm_sp += 3
    llm_sp = min(9, llm_sp)

    audit_struct_score = _get_audit_axis_score(linked_audit, "structure")
    audit_sp = (100 - audit_struct_score) / 100 * 6 if audit_struct_score is not None else 3
    total_sp = min(15, llm_sp + audit_sp)
    structure_score = round(100 - total_sp / 15 * 100)

    struct_recs = []
    if cluster_count > 0 and avg_cohesion is not None:
        struct_details = _("%(count)d topic clusters detected with average cohesion of %(cohesion)s.") % {"count": cluster_count, "cohesion": f"{avg_cohesion:.0%}"}
        if avg_cohesion < 0.6:
            struct_recs.append(_("Improve document organization — many documents do not fit clearly into a single theme. Consider splitting overly broad documents."))
            top_recs.append({"icon": "layers", "text": _("Restructure poorly organized content to improve thematic cohesion.")})
        if avg_cohesion < 0.8 and avg_cohesion >= 0.6:
            struct_recs.append(_("Some topic clusters overlap moderately. Review cluster boundaries and consider reorganizing ambiguous documents."))
    elif cluster_count == 0:
        struct_details = _("No topic clusters detected. The analysis may need more documents to identify structure.")
        struct_recs.append(_("Ingest more documents so the clustering algorithm can identify meaningful topic groups."))
    else:
        struct_details = _("Topic clusters exist but cohesion data is not yet available.")
    if audit_struct_score is not None:
        struct_details += _(" Audit structure score: %(score).0f/100.") % {"score": audit_struct_score}

    dims.append(_dim(_("Structure"), structure_score,
                      _("Measures content organization quality (LLM clusters + audit structure)."),
                      struct_details, struct_recs))

    # 5. Santé
    health = health_score(ready_docs, error_docs, total_docs)
    health_details_str = _health_details(ready_docs, error_docs, total_docs)
    health_recs = _health_recs(ready_docs, error_docs, total_docs)
    if health_recs:
        top_recs.append({"icon": "heart", "text": health_recs[0]})

    dims.append(_dim(_("Health"), health,
                      _("Document ingestion pipeline operational status."),
                      health_details_str, health_recs))

    # 6. Retrievability
    retriev_val = _get_audit_axis_score(linked_audit, "retrievability")
    hygiene_val = _get_audit_axis_score(linked_audit, "hygiene")

    if retriev_val is not None or hygiene_val is not None:
        rp = (100 - retriev_val) / 100 * 9 if retriev_val is not None else 4.5
        hp = (100 - hygiene_val) / 100 * 6 if hygiene_val is not None else 3
        total_rp = min(15, rp + hp)
        retriev_score = round(100 - total_rp / 15 * 100)

        retriev_parts = []
        if retriev_val is not None:
            retriev_parts.append(_("Retrievability: %(score).0f/100") % {"score": retriev_val})
        if hygiene_val is not None:
            retriev_parts.append(_("Hygiene: %(score).0f/100") % {"score": hygiene_val})
        retriev_details = _("Audit scores: %(parts)s.") % {"parts": ", ".join(retriev_parts)}
        retriev_recs = []
        if retriev_val is not None and retriev_val < 60:
            retriev_recs.append(_("Improve chunk retrievability — queries are not finding enough relevant results."))
        if hygiene_val is not None and hygiene_val < 60:
            retriev_recs.append(_("Improve corpus hygiene — chunk format or size issues were detected."))
        if (retriev_val is not None and retriev_val < 60) or (hygiene_val is not None and hygiene_val < 60):
            top_recs.append({"icon": "target", "text": _("Improve retrievability and corpus hygiene.")})
    else:
        retriev_score = None
        retriev_details = _("No completed audit. Run a full analysis to evaluate retrievability.")
        retriev_recs = [_("Run a full analysis including RAG audit to evaluate retrievability.")]

    dims.append(_dim(_("Retrievability"), retriev_score,
                      _("System's ability to find relevant information (retrievability + hygiene)."),
                      retriev_details, retriev_recs))

    # 7. Gouvernance
    gov_val = _get_audit_axis_score(linked_audit, "governance")
    coh_val = _get_audit_axis_score(linked_audit, "coherence")

    if gov_val is not None or coh_val is not None:
        gp = (100 - gov_val) / 100 * 6 if gov_val is not None else 3
        cp = (100 - coh_val) / 100 * 4 if coh_val is not None else 2
        total_gp = min(10, gp + cp)
        gov_score_val = round(100 - total_gp / 10 * 100)

        gov_parts = []
        if gov_val is not None:
            gov_parts.append(_("Governance: %(score).0f/100") % {"score": gov_val})
        if coh_val is not None:
            gov_parts.append(_("Internal coherence: %(score).0f/100") % {"score": coh_val})
        gov_details = _("Audit scores: %(parts)s.") % {"parts": ", ".join(gov_parts)}
        gov_recs = []
        if gov_val is not None and gov_val < 60:
            gov_recs.append(_("Improve document metadata and governance (authors, dates, classifications)."))
        if coh_val is not None and coh_val < 60:
            gov_recs.append(_("Improve internal corpus coherence — format or structure inconsistencies were detected."))
        if (gov_val is not None and gov_val < 60) or (coh_val is not None and coh_val < 60):
            top_recs.append({"icon": "target", "text": _("Improve governance and internal corpus coherence.")})
    else:
        gov_score_val = None
        gov_details = _("No completed audit. Run a full analysis to evaluate governance.")
        gov_recs = [_("Run a full analysis including RAG audit to evaluate governance.")]

    dims.append(_dim(_("Governance"), gov_score_val,
                      _("Document governance quality (metadata, classification, internal coherence)."),
                      gov_details, gov_recs))

    # Final
    score_result = compute_docuscore(project)
    grade = score_result["grade"]
    score = score_result["score"]

    grade_labels = {"A": _("Excellent"), "B": _("Good"), "C": _("Acceptable"), "D": _("Poor"), "E": _("Critical")}
    summary = _("Your knowledge base scores %(score)d/100 (Grade %(grade)s — %(label)s). ") % {"score": score, "grade": grade, "label": grade_labels[grade]}
    scored_dims = [(d["name"], d["score"]) for d in dims if d["score"] is not None]
    scored_dims.sort(key=lambda x: x[1])
    if scored_dims and scored_dims[0][1] < 70:
        summary += _("The main area for improvement is %(dim)s (%(score)d/100).") % {"dim": scored_dims[0][0], "score": scored_dims[0][1]}
    else:
        summary += _("All dimensions are performing well.")

    return {
        "score": score,
        "grade": grade,
        "summary": summary,
        "dimensions": dims,
        "top_recommendations": top_recs[:5],
    }


def _dim(name, score, description, details, recommendations):
    return {
        "name": name,
        "score": score,
        "description": description,
        "details": details,
        "recommendations": recommendations,
    }


def _health_details(ready, errors, total):
    ready_pct = round(ready / total * 100) if total else 0
    error_pct = round(errors / total * 100) if total else 0
    pending = total - ready - errors
    return _("%(ready)d documents ready out of %(total)d (%(ready_pct)d%%), %(errors)d errors (%(error_pct)d%%), %(pending)d pending.") % {
        "ready": ready, "total": total, "ready_pct": ready_pct,
        "errors": errors, "error_pct": error_pct, "pending": pending,
    }


def _health_recs(ready, errors, total):
    recs = []
    if errors > 0:
        recs.append(_("Investigate and fix %(count)d document(s) stuck in error state.") % {"count": errors})
    if total > 0:
        pending = total - ready - errors
        if pending > 0:
            recs.append(_("Complete ingestion of %(count)d pending document(s).") % {"count": pending})
        ready_ratio = ready / total
        if ready_ratio < 0.8 and errors == 0:
            recs.append(_("Run a sync on your connectors to finish processing pending documents."))
    return recs


def health_score(ready, errors, total):
    """Return 0-100 health score from document readiness."""
    if total == 0:
        return 0
    error_penalty = min(50, (errors / total) * 500)  # 10% errors = -50
    ready_bonus = (ready / total) * 100
    return max(0, min(100, round(ready_bonus - error_penalty)))


def grade(score):
    """Map a 0-100 score to a letter grade A through E."""
    if score >= GRADE_A_MIN:
        return "A"
    if score >= GRADE_B_MIN:
        return "B"
    if score >= GRADE_C_MIN:
        return "C"
    if score >= GRADE_D_MIN:
        return "D"
    return "E"


# Keep backward-compatible alias
_grade = grade


# Radar chart axis keys in display order
_RADAR_AXES = [
    ("uniqueness", "Unicité"),
    ("consistency", "Cohérence"),
    ("coverage", "Couverture"),
    ("structure", "Structure"),
    ("health", "Santé"),
    ("retrievability", "Retrievability"),
    ("governance", "Gouvernance"),
]


def build_breakdown_json(breakdown):
    """Serialize a DocuScore breakdown dict to JSON for the radar chart."""
    import json
    return json.dumps([
        {"axis": str(_(label)), "score": breakdown.get(key) or 0}
        for key, label in _RADAR_AXES
    ])


def compute_penalty_score(
    total_docs,
    dup_count,
    weighted_contra,
    weighted_gaps,
    avg_coverage,
    avg_cohesion,
    cluster_count,
    health,
    audit_coverage=None,
    audit_structure=None,
    audit_retrievability=None,
    audit_hygiene=None,
    audit_governance=None,
    audit_coherence=None,
):
    """Pure penalty-based scoring — single source of truth for the 7-dimension formula.

    Returns (score, breakdown) where score is 0-100 and breakdown maps
    dimension names to 0-100 sub-scores (or None when data is unavailable).
    """
    score = 100.0
    breakdown = {}

    # 1. Uniqueness
    dup_ratio = dup_count / total_docs
    uniqueness_penalty = min(MAX_UNIQUENESS_PENALTY, dup_ratio / DUP_RATIO_THRESHOLD * MAX_UNIQUENESS_PENALTY)
    score -= uniqueness_penalty
    breakdown["uniqueness"] = round(100 - uniqueness_penalty / MAX_UNIQUENESS_PENALTY * 100)

    # 2. Consistency
    contra_ratio = weighted_contra / total_docs
    consistency_penalty = min(MAX_CONSISTENCY_PENALTY, contra_ratio / CONTRA_RATIO_THRESHOLD * MAX_CONSISTENCY_PENALTY)
    score -= consistency_penalty
    breakdown["consistency"] = round(100 - consistency_penalty / MAX_CONSISTENCY_PENALTY * 100)

    # 3. Coverage — LLM gaps + depth + audit
    gap_ratio = weighted_gaps / total_docs
    llm_gap_penalty = min(COVERAGE_LLM_GAPS_MAX, gap_ratio / GAP_RATIO_THRESHOLD * COVERAGE_LLM_GAPS_MAX)
    if avg_coverage is not None:
        llm_gap_penalty += min(COVERAGE_LLM_DEPTH_MAX, (1 - avg_coverage) * COVERAGE_LLM_DEPTH_MAX)
    audit_cov_penalty = (100 - audit_coverage) / 100 * COVERAGE_AUDIT_MAX if audit_coverage is not None else COVERAGE_AUDIT_MAX / 2
    total_coverage_penalty = min(MAX_COVERAGE_PENALTY, llm_gap_penalty + audit_cov_penalty)
    score -= total_coverage_penalty
    breakdown["coverage"] = round(100 - total_coverage_penalty / MAX_COVERAGE_PENALTY * 100)

    # 4. Structure — LLM clusters + audit
    llm_struct_penalty = 0.0
    if avg_cohesion is not None:
        llm_struct_penalty += max(0, (1 - avg_cohesion)) * 6
    else:
        llm_struct_penalty += 5
    if cluster_count == 0:
        llm_struct_penalty += 3
    llm_struct_penalty = min(STRUCTURE_LLM_MAX, llm_struct_penalty)
    audit_struct_penalty = (100 - audit_structure) / 100 * STRUCTURE_AUDIT_MAX if audit_structure is not None else STRUCTURE_AUDIT_MAX / 2
    total_struct_penalty = min(MAX_STRUCTURE_PENALTY, llm_struct_penalty + audit_struct_penalty)
    score -= total_struct_penalty
    breakdown["structure"] = round(100 - total_struct_penalty / MAX_STRUCTURE_PENALTY * 100)

    # 5. Health
    health_penalty = (100 - health) / 100 * MAX_HEALTH_PENALTY
    score -= health_penalty
    breakdown["health"] = health

    # 6. Retrievability — audit retrievability + hygiene
    ret_penalty = (100 - audit_retrievability) / 100 * RETRIEVABILITY_AUDIT_MAX if audit_retrievability is not None else RETRIEVABILITY_AUDIT_MAX / 2
    hyg_penalty = (100 - audit_hygiene) / 100 * HYGIENE_AUDIT_MAX if audit_hygiene is not None else HYGIENE_AUDIT_MAX / 2
    total_ret_penalty = min(MAX_RETRIEVABILITY_PENALTY, ret_penalty + hyg_penalty)
    score -= total_ret_penalty
    if audit_retrievability is not None or audit_hygiene is not None:
        breakdown["retrievability"] = round(100 - total_ret_penalty / MAX_RETRIEVABILITY_PENALTY * 100)
    else:
        breakdown["retrievability"] = None

    # 7. Governance — audit governance + coherence
    gov_penalty = (100 - audit_governance) / 100 * GOVERNANCE_AUDIT_MAX if audit_governance is not None else GOVERNANCE_AUDIT_MAX / 2
    coh_penalty = (100 - audit_coherence) / 100 * COHERENCE_AUDIT_MAX if audit_coherence is not None else COHERENCE_AUDIT_MAX / 2
    total_gov_penalty = min(MAX_GOVERNANCE_PENALTY, gov_penalty + coh_penalty)
    score -= total_gov_penalty
    if audit_governance is not None or audit_coherence is not None:
        breakdown["governance"] = round(100 - total_gov_penalty / MAX_GOVERNANCE_PENALTY * 100)
    else:
        breakdown["governance"] = None

    score = max(0, min(100, round(score)))
    return score, breakdown


def _empty_result(has_docs, has_analysis):
    return {
        "grade": "E",
        "score": 0,
        "breakdown": {
            "uniqueness": None,
            "consistency": None,
            "coverage": None,
            "structure": None,
            "health": 0,
            "retrievability": None,
            "governance": None,
        },
        "has_docs": has_docs,
        "has_analysis": has_analysis,
    }
