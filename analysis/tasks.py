"""
Celery tasks for the unified analysis + audit pipeline.

Orchestrates: duplicates → claims → clustering → gaps → tree → contradictions
              → audit (hygiene → structure → coverage → coherence → retrievability → governance)
Each phase updates the AnalysisJob progress for the dashboard.

Supports checkpoint/resume: if a pipeline fails mid-run, the saved
``current_phase`` acts as a checkpoint.  On retry the pipeline skips
phases that completed successfully and re-runs from the failed phase.
"""
import logging
import shutil

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# Progress mapping for unified pipeline
UNIFIED_PROGRESS = {
    "duplicates": 0,
    "claims": 10,
    "semantic_graph": 16,
    "clustering": 22,
    "gaps": 32,
    "tree": 40,
    "contradictions": 48,
    "hallucination": 55,
    "audit_hygiene": 60,
    "audit_structure": 67,
    "audit_coverage": 73,
    "audit_coherence": 80,
    "audit_retrievability": 87,
    "audit_governance": 93,
    "done": 100,
}


# Ordered phase values for resume logic
ANALYSIS_PHASE_ORDER = [
    "duplicates",
    "claims",
    "semantic_graph",
    "clustering",
    "gaps",
    "tree",
    "contradictions",
    "hallucination",
]

AUDIT_PHASE_ORDER = [
    "audit_hygiene",
    "audit_structure",
    "audit_coverage",
    "audit_coherence",
    "audit_retrievability",
    "audit_governance",
]


def _build_effective_config(job):
    """Deep-copy ANALYSIS_CONFIG and shallow-merge per-job overrides."""
    import copy

    from django.conf import settings

    base = copy.deepcopy(settings.ANALYSIS_CONFIG)
    for key, val in (job.config_overrides or {}).items():
        if isinstance(val, dict) and key in base and isinstance(base[key], dict):
            base[key].update(val)
        else:
            base[key] = val
    return base


def _cleanup_phase(job, phase_value):
    """Delete partial results for a phase so it can be re-run cleanly."""
    from analysis.models import (
        ContradictionPair,
        DuplicateGroup,
        GapReport,
        TopicCluster,
        TreeNode,
    )

    if phase_value == "duplicates":
        # DuplicatePair cascades via DuplicateGroup FK
        deleted, _ = DuplicateGroup.objects.filter(analysis_job=job).delete()
        logger.info("Cleanup duplicates: deleted %d objects", deleted)

    elif phase_value == "claims":
        pass  # claims extraction is idempotent (skips existing)

    elif phase_value == "semantic_graph":
        from analysis.semantic_graph import graph_dir as _get_graph_dir
        gdir = _get_graph_dir(str(job.project_id))
        if gdir.exists():
            shutil.rmtree(gdir)
            logger.info("Cleanup semantic_graph: removed %s", gdir)

    elif phase_value == "clustering":
        # ClusterMembership cascades via TopicCluster FK
        deleted, _ = TreeNode.objects.filter(analysis_job=job).delete()
        deleted2, _ = TopicCluster.objects.filter(analysis_job=job).delete()
        logger.info("Cleanup clustering: deleted %d tree nodes + %d clusters", deleted, deleted2)

    elif phase_value == "gaps":
        deleted, _ = GapReport.objects.filter(analysis_job=job).delete()
        logger.info("Cleanup gaps: deleted %d gap reports", deleted)

    elif phase_value == "tree":
        deleted, _ = TreeNode.objects.filter(analysis_job=job).delete()
        logger.info("Cleanup tree: deleted %d tree nodes", deleted)

    elif phase_value == "contradictions":
        deleted, _ = ContradictionPair.objects.filter(analysis_job=job).delete()
        logger.info("Cleanup contradictions: deleted %d contradiction pairs", deleted)

    elif phase_value == "hallucination":
        from analysis.models import HallucinationReport
        deleted, _ = HallucinationReport.objects.filter(analysis_job=job).delete()
        logger.info("Cleanup hallucination: deleted %d hallucination reports", deleted)


def _collect_existing_stats(job):
    """Query DB for result counts from a previously completed analysis run."""
    from analysis.models import (
        Claim,
        ContradictionPair,
        DuplicateGroup,
        GapReport,
        HallucinationReport,
        TopicCluster,
    )

    return {
        "dup_groups": DuplicateGroup.objects.filter(analysis_job=job).count(),
        "claims": Claim.objects.filter(project=job.project).count(),
        "contradictions": ContradictionPair.objects.filter(analysis_job=job).count(),
        "clusters": TopicCluster.objects.filter(analysis_job=job).count(),
        "gaps": GapReport.objects.filter(analysis_job=job).count(),
        "hallucinations": HallucinationReport.objects.filter(analysis_job=job).count(),
    }


def _update_phase(job, phase: str, progress: int):
    """Update job phase and progress, clearing sub-step detail."""

    job.current_phase = phase
    job.progress_pct = progress
    job.phase_detail = {}
    job.save(update_fields=["current_phase", "progress_pct", "phase_detail"])


def _make_progress_cb(job_pk, step_label):
    """Return a rate-limited callback that writes sub-step progress to DB.

    Writes at most once every 2 seconds (except the first and last call)
    using atomic ``filter().update()`` to avoid stale ORM references.
    Stores a wall-clock ``started_at`` on first call and computes
    ``eta_seconds`` from the observed throughput.
    """
    import time as _time

    from analysis.models import AnalysisJob

    _last_write = [0.0]  # mutable container for closure
    _started_at = [0.0]
    _MIN_INTERVAL = 2.0

    def _on_progress(done, total):
        now = _time.monotonic()
        is_first = _last_write[0] == 0.0
        is_last = done >= total

        if is_first:
            _started_at[0] = now

        if not is_first and not is_last and (now - _last_write[0]) < _MIN_INTERVAL:
            return
        _last_write[0] = now

        # Estimate remaining seconds from observed throughput
        elapsed = now - _started_at[0]
        eta_seconds = None
        if done > 0 and elapsed > 0 and not is_last:
            rate = done / elapsed  # items per second
            eta_seconds = round((total - done) / rate)

        detail = {"step": step_label, "done": done, "total": total}
        if eta_seconds is not None:
            detail["eta_seconds"] = eta_seconds

        AnalysisJob.objects.filter(pk=job_pk).update(phase_detail=detail)

    return _on_progress




def _audit_grade(score):
    """Backward-compatible alias — delegates to shared grade()."""
    from score.scoring import grade
    return grade(score)


@shared_task(bind=True, max_retries=1, default_retry_delay=120)
def run_unified_pipeline(self, job_id: str):
    """
    Run the unified analysis + audit pipeline.
    Phases: LLM analysis (7 phases) → RAG audit (6 axes)

    Supports checkpoint/resume: reads ``job.current_phase`` to determine
    where to restart from.  A fresh job (``current_phase=duplicates``)
    runs everything; a retried job skips completed phases.
    """
    from analysis.models import AnalysisJob, PipelineTrace
    from analysis.trace import TraceCollector
    from llm.client import get_llm_client
    from vectorstore.store import get_vector_store

    try:
        job = AnalysisJob.objects.select_related("tenant", "project").get(id=job_id)
    except AnalysisJob.DoesNotExist:
        logger.error("AnalysisJob %s not found", job_id)
        return

    # Guard against duplicate execution: if another task has already taken
    # over this job (e.g. recovery handler dispatched a replacement while the
    # original message was still in the broker), bail out.
    if (
        job.celery_task_id
        and self.request.id
        and job.celery_task_id != self.request.id
        and job.status == AnalysisJob.Status.RUNNING
    ):
        logger.warning(
            "Skipping duplicate task %s for job %s (active task: %s)",
            self.request.id, job_id, job.celery_task_id,
        )
        return

    # Determine resume checkpoint
    checkpoint = job.current_phase
    is_fresh = checkpoint == AnalysisJob.Phase.DUPLICATES
    is_audit_resume = checkpoint in AUDIT_PHASE_ORDER
    is_analysis_resume = not is_fresh and checkpoint in ANALYSIS_PHASE_ORDER

    if not is_fresh:
        logger.info("Resuming pipeline for job %s from phase %s", job_id, checkpoint)

    job.status = AnalysisJob.Status.RUNNING
    job.started_at = timezone.now()
    job.celery_task_id = self.request.id or ""
    job.save()

    # Delete old trace — a new one is created for each run attempt
    try:
        job.trace.delete()
    except PipelineTrace.DoesNotExist:
        pass

    # Set up pipeline tracing
    pipeline_trace = PipelineTrace.objects.create(
        tenant=job.tenant,
        project=job.project,
        analysis_job=job,
        started_at=timezone.now(),
    )
    collector = TraceCollector(pipeline_trace)

    llm_client = get_llm_client()
    vec_store = get_vector_store()
    llm_client.set_trace(collector)
    vec_store.set_trace(collector)

    try:
        import time as _pipeline_time
        _pipeline_t0 = _pipeline_time.monotonic()

        from analysis.pipeline import run_analysis_phases, run_audit_phases

        if is_audit_resume:
            # All analysis phases completed — just collect existing stats
            stats = _collect_existing_stats(job)
            # Resume audit from the failed axis
            if job.includes_audit:
                logger.info("=== Starting audit phases (resume from %s) ===", checkpoint)
                run_audit_phases(job, collector, resume_from=checkpoint)
        else:
            # Run analysis phases (fresh or resume from an analysis phase)
            resume_from = checkpoint if is_analysis_resume else None
            logger.info("=== Starting analysis phases ===")
            _analysis_t0 = _pipeline_time.monotonic()
            stats = run_analysis_phases(job, collector, resume_from=resume_from)
            logger.info("=== Analysis phases completed in %.1fs ===", _pipeline_time.monotonic() - _analysis_t0)

            # Audit phases (60-93%)
            if job.includes_audit:
                logger.info("=== Starting audit phases ===")
                _audit_t0 = _pipeline_time.monotonic()
                run_audit_phases(job, collector)
                logger.info("=== Audit phases completed in %.1fs ===", _pipeline_time.monotonic() - _audit_t0)

        # Done (100%)
        _update_phase(job, AnalysisJob.Phase.DONE, UNIFIED_PROGRESS["done"])
        job.status = AnalysisJob.Status.COMPLETED
        job.completed_at = timezone.now()
        job.save()

        _total_duration = _pipeline_time.monotonic() - _pipeline_t0
        logger.info(
            "=== Pipeline complete in %.1fs for tenant=%s: %d dup groups, %d claims, "
            "%d contradictions, %d clusters, %d gaps, %d hallucination risks ===",
            _total_duration, job.tenant.slug,
            stats["dup_groups"], stats["claims"],
            stats["contradictions"], stats["clusters"], stats["gaps"],
            stats.get("hallucinations", 0),
        )

    except Exception as exc:
        logger.exception("Unified pipeline failed for job %s: %s", job_id, exc)
        job.status = AnalysisJob.Status.FAILED
        job.error_message = str(exc)[:2000]
        job.phase_detail = {}
        job.completed_at = timezone.now()
        job.save()
        raise self.retry(exc=exc)

    finally:
        collector.finalize()
        llm_client.clear_trace()
        vec_store.clear_trace()


@shared_task(bind=True, max_retries=1, default_retry_delay=120)
def run_analysis(self, job_id: str):
    """
    Backward-compatible: run only the LLM analysis phases (no audit).
    Supports checkpoint/resume via ``job.current_phase``.
    """
    from analysis.models import AnalysisJob, PipelineTrace
    from analysis.trace import TraceCollector
    from llm.client import get_llm_client
    from vectorstore.store import get_vector_store

    try:
        job = AnalysisJob.objects.select_related("tenant", "project").get(id=job_id)
    except AnalysisJob.DoesNotExist:
        logger.error("AnalysisJob %s not found", job_id)
        return

    # Guard against duplicate execution (see run_unified_pipeline).
    if (
        job.celery_task_id
        and self.request.id
        and job.celery_task_id != self.request.id
        and job.status == AnalysisJob.Status.RUNNING
    ):
        logger.warning(
            "Skipping duplicate task %s for job %s (active task: %s)",
            self.request.id, job_id, job.celery_task_id,
        )
        return

    checkpoint = job.current_phase
    is_fresh = checkpoint == AnalysisJob.Phase.DUPLICATES
    resume_from = checkpoint if not is_fresh and checkpoint in ANALYSIS_PHASE_ORDER else None

    if resume_from:
        logger.info("Resuming analysis for job %s from phase %s", job_id, resume_from)

    job.status = AnalysisJob.Status.RUNNING
    job.started_at = timezone.now()
    job.celery_task_id = self.request.id or ""
    job.save()

    # Delete old trace
    try:
        job.trace.delete()
    except PipelineTrace.DoesNotExist:
        pass

    # Set up pipeline tracing
    pipeline_trace = PipelineTrace.objects.create(
        tenant=job.tenant,
        project=job.project,
        analysis_job=job,
        started_at=timezone.now(),
    )
    collector = TraceCollector(pipeline_trace)

    llm_client = get_llm_client()
    vec_store = get_vector_store()
    llm_client.set_trace(collector)
    vec_store.set_trace(collector)

    try:
        from analysis.pipeline import run_analysis_phases
        run_analysis_phases(job, collector, resume_from=resume_from)

        _update_phase(job, AnalysisJob.Phase.DONE, 100)
        job.status = AnalysisJob.Status.COMPLETED
        job.completed_at = timezone.now()
        job.save()

    except Exception as exc:
        logger.exception("Analysis pipeline failed for job %s: %s", job_id, exc)
        job.status = AnalysisJob.Status.FAILED
        job.error_message = str(exc)[:2000]
        job.phase_detail = {}
        job.completed_at = timezone.now()
        job.save()
        raise self.retry(exc=exc)

    finally:
        collector.finalize()
        llm_client.clear_trace()
        vec_store.clear_trace()
