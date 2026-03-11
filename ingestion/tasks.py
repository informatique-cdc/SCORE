"""Celery tasks for document ingestion."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def run_ingestion(self, job_id: str):
    """
    Run the ingestion pipeline for a given IngestionJob.
    This task is queued by the connector views / scheduled sync.
    """
    from ingestion.models import IngestionJob
    from ingestion.pipeline import IngestionPipeline

    try:
        job = IngestionJob.objects.select_related("connector", "tenant", "project").get(id=job_id)
    except IngestionJob.DoesNotExist:
        logger.error("IngestionJob %s not found", job_id)
        return

    job.celery_task_id = self.request.id or ""
    job.save()

    try:
        pipeline = IngestionPipeline(job)
        pipeline.run()
    except Exception as exc:
        logger.exception("Ingestion task failed for job %s", job_id)
        raise self.retry(exc=exc)
