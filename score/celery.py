"""
Celery application for SCORE.

Supports two modes:
  - Redis broker (production)
  - Django DB broker (dev mode, no Redis required)

Set CELERY_BROKER_BACKEND=database in .env for dev mode.
"""

import logging
import os

from celery import Celery
from celery.signals import worker_ready

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "score.settings")

logger = logging.getLogger(__name__)

app = Celery("score")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_ready.connect
def recover_stale_analysis_jobs(sender, **kwargs):
    """Re-queue analysis jobs that were interrupted by a worker crash.

    On startup, any AnalysisJob still in RUNNING or QUEUED status was
    left behind by a previous worker that died.  CANCELLED jobs are
    explicitly excluded — those were stopped by the user.
    """
    try:
        from analysis.models import AnalysisJob
        from analysis.tasks import run_unified_pipeline

        stale_jobs = list(
            AnalysisJob.objects.filter(
                status__in=[AnalysisJob.Status.RUNNING, AnalysisJob.Status.QUEUED],
            )
        )

        for job in stale_jobs:
            logger.info(
                "Recovering stale analysis job=%s (status=%s, phase=%s)",
                job.pk,
                job.status,
                job.current_phase,
            )
            # Revoke the old Celery task to avoid duplicates if the
            # original message is still sitting in the broker queue.
            if job.celery_task_id:
                app.control.revoke(job.celery_task_id, terminate=True)

            job.status = AnalysisJob.Status.QUEUED
            job.error_message = ""
            job.save(update_fields=["status", "error_message"])
            task = run_unified_pipeline.delay(str(job.pk))
            job.celery_task_id = task.id
            job.save(update_fields=["celery_task_id"])

        if stale_jobs:
            logger.info("Recovered %d stale analysis job(s)", len(stale_jobs))
    except Exception:
        logger.exception("Failed to recover stale analysis jobs on startup")


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
