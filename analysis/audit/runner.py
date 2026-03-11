"""Celery task: run all 6 audit axes sequentially."""
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

AXIS_ORDER = [
    ("hygiene", "analysis.audit.hygiene", "HygieneAxis"),
    ("structure", "analysis.audit.structure_rag", "StructureAxis"),
    ("coverage", "analysis.audit.coverage", "CoverageAxis"),
    ("coherence", "analysis.audit.coherence", "CoherenceAxis"),
    ("retrievability", "analysis.audit.retrievability", "RetrievabilityAxis"),
    ("governance", "analysis.audit.governance", "GovernanceAxis"),
]

PROGRESS_MAP = {
    "hygiene": (0, 15),
    "structure": (15, 30),
    "coverage": (30, 50),
    "coherence": (50, 65),
    "retrievability": (65, 82),
    "governance": (82, 97),
}


def _grade(score):
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    if score >= 20:
        return "D"
    return "E"


@shared_task(bind=True, max_retries=1, default_retry_delay=120)
def run_audit(self, audit_job_id: str):
    """Run all 6 audit axes for the given AuditJob."""
    from importlib import import_module

    from analysis.models import AuditAxisResult, AuditJob

    try:
        job = AuditJob.objects.select_related("tenant", "project").get(id=audit_job_id)
    except AuditJob.DoesNotExist:
        logger.error("AuditJob %s not found", audit_job_id)
        return

    job.status = AuditJob.Status.RUNNING
    job.started_at = timezone.now()
    job.celery_task_id = self.request.id or ""
    job.save()

    project = job.project
    audit_cfg = settings.APP_CONFIG.get("audit", {})
    weights = audit_cfg.get("axis_weights", {})

    weighted_sum = 0.0
    total_weight = 0.0

    try:
        for axis_key, module_path, class_name in AXIS_ORDER:
            # Update progress
            job.current_axis = axis_key
            start_pct = PROGRESS_MAP[axis_key][0]
            job.progress_pct = start_pct
            job.save(update_fields=["current_axis", "progress_pct"])

            # Import and run axis
            mod = import_module(module_path)
            axis_cls = getattr(mod, class_name)
            axis = axis_cls(project, job, config=audit_cfg.get(axis_key, {}))
            score, metrics, chart_data, details, duration = axis.execute()

            # Save result
            AuditAxisResult.objects.update_or_create(
                audit_job=job,
                axis=axis_key,
                defaults={
                    "tenant": job.tenant,
                    "project": project,
                    "score": score,
                    "metrics": metrics,
                    "chart_data": chart_data,
                    "details": details,
                    "duration_seconds": duration,
                },
            )

            w = weights.get(axis_key, 1.0 / 6)
            weighted_sum += score * w
            total_weight += w

            end_pct = PROGRESS_MAP[axis_key][1]
            job.progress_pct = end_pct
            job.save(update_fields=["progress_pct"])

            logger.info(
                "Audit axis %s complete: score=%.1f duration=%.1fs",
                axis_key, score, duration,
            )

        # Compute overall
        overall = weighted_sum / total_weight if total_weight > 0 else 0
        job.overall_score = round(overall, 1)
        job.overall_grade = _grade(overall)
        job.current_axis = AuditJob.Axis.DONE
        job.progress_pct = 100
        job.status = AuditJob.Status.COMPLETED
        job.completed_at = timezone.now()
        job.save()

        logger.info(
            "Audit complete for project=%s: score=%.1f grade=%s",
            project, overall, job.overall_grade,
        )

    except Exception as exc:
        logger.exception("Audit pipeline failed for job %s: %s", audit_job_id, exc)
        job.status = AuditJob.Status.FAILED
        job.error_message = str(exc)[:2000]
        job.completed_at = timezone.now()
        job.save()
        raise self.retry(exc=exc)
