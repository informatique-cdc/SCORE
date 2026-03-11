"""Connector management views."""
import logging
import mimetypes
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Count, Sum
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ingestion.models import Document, DocumentChunk, IngestionJob
from ingestion.tasks import run_ingestion
from vectorstore.store import get_vector_store

from tenants.models import AuditLog, log_audit

from .models import ConnectorConfig

logger = logging.getLogger(__name__)


def _has_active_ingestion_jobs(project):
    """Return True if there are any running/queued ingestion jobs for this project."""
    return IngestionJob.objects.filter(project=project).filter(
        status__in=[IngestionJob.Status.QUEUED, IngestionJob.Status.RUNNING]
    ).exists()


def _connector_jobs_context(connector):
    """Build context dict for the connector jobs table partial."""
    jobs = IngestionJob.objects.filter(connector=connector).order_by("-created_at")[:10]
    should_poll = any(
        j.status in (IngestionJob.Status.QUEUED, IngestionJob.Status.RUNNING)
        for j in jobs
    )
    return {
        "connector": connector,
        "jobs": jobs,
        "should_poll": should_poll,
    }


@login_required
def connector_list(request):
    if not request.project:
        return redirect("project-list")
    connectors = ConnectorConfig.objects.filter(project=request.project).annotate(
        doc_count=Count("documents", filter=~models.Q(documents__status=Document.Status.DELETED)),
    )
    return render(request, "connectors/list.html", {
        "connectors": connectors,
        "connector_types": ConnectorConfig.ConnectorType.choices,
        "should_poll": _has_active_ingestion_jobs(request.project),
    })


@login_required
def connector_create(request):
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("connector-list")

    if request.method == "POST":
        ConnectorConfig.objects.create(
            tenant=request.tenant,
            project=request.project,
            name=request.POST["name"],
            connector_type=request.POST["connector_type"],
            config={
                k.removeprefix("config_"): v
                for k, v in request.POST.items()
                if k.startswith("config_") and v
            },
            credential_ref=request.POST.get("credential_ref", ""),
        )
        return redirect("connector-list")

    return render(request, "connectors/create.html", {
        "connector_types": ConnectorConfig.ConnectorType.choices,
    })


def _connector_source_path(connector):
    """Extract a human-readable source path from the connector config."""
    cfg = connector.config or {}
    # Try common path keys in order of specificity
    for key in ("base_path", "site_url", "space_key"):
        if cfg.get(key):
            return cfg[key]
    # Fallback: return first non-empty config value that looks path-like
    for v in cfg.values():
        if isinstance(v, str) and v:
            return v
    return None


def _connector_doc_stats(connector):
    """Compute document statistics for a connector."""
    docs_qs = Document.objects.filter(connector=connector).exclude(
        status=Document.Status.DELETED
    )
    status_counts = dict(
        docs_qs.values_list("status").annotate(n=Count("id")).values_list("status", "n")
    )
    aggregates = docs_qs.aggregate(
        total_words=Sum("word_count"),
        total_chunks=Sum("chunk_count"),
    )
    return {
        "doc_total": docs_qs.count(),
        "doc_ready": status_counts.get(Document.Status.READY, 0),
        "doc_pending": status_counts.get(Document.Status.PENDING, 0),
        "doc_error": status_counts.get(Document.Status.ERROR, 0),
        "total_words": aggregates["total_words"] or 0,
        "total_chunks": aggregates["total_chunks"] or 0,
    }


@login_required
def connector_detail(request, pk):
    connector = get_object_or_404(ConnectorConfig, pk=pk, project=request.project)

    # Documents (paginated)
    docs_qs = Document.objects.filter(connector=connector).exclude(
        status=Document.Status.DELETED
    ).order_by("-source_modified_at")
    paginator = Paginator(docs_qs, 25)
    page = paginator.get_page(request.GET.get("page"))

    context = {
        "connector": connector,
        "source_path": _connector_source_path(connector),
        "documents": page,
    }
    context.update(_connector_doc_stats(connector))
    context.update(_connector_jobs_context(connector))
    return render(request, "connectors/detail.html", context)


@login_required
@require_POST
def connector_sync(request, pk):
    """Trigger an ingestion sync for a connector."""
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("connector-list")

    connector = get_object_or_404(ConnectorConfig, pk=pk, project=request.project)

    job = IngestionJob.objects.create(
        tenant=request.tenant,
        project=request.project,
        connector=connector,
        status=IngestionJob.Status.QUEUED,
    )

    task = run_ingestion.delay(str(job.id))
    job.celery_task_id = task.id
    job.save()

    logger.info("Queued ingestion job=%s for connector=%s", job.id, connector.name)
    return redirect("connector-detail", pk=pk)


@login_required
@require_POST
def connector_delete(request, pk):
    """Delete a connector and all its documents, chunks, and vectors."""
    if not request.project_membership or not request.project_membership.can_edit:
        return redirect("connector-list")

    connector = get_object_or_404(ConnectorConfig, pk=pk, project=request.project)

    # Clean up vectors (not managed by Django ORM)
    doc_ids = list(
        Document.objects.filter(connector=connector).values_list("id", flat=True)
    )
    if doc_ids:
        store = get_vector_store()
        store.delete_by_documents([str(d) for d in doc_ids])

    log_audit(
        tenant=request.tenant, user=request.user,
        action=AuditLog.Action.CONNECTOR_DELETED, target=connector,
        detail={"doc_count": len(doc_ids)},
    )
    logger.info("Deleting connector=%s (%s) with %d documents", connector.name, pk, len(doc_ids))
    connector.delete()

    return redirect("connector-list")


@login_required
def connector_cards_partial(request):
    if not request.project:
        return redirect("project-list")
    connectors = ConnectorConfig.objects.filter(project=request.project).annotate(
        doc_count=Count("documents", filter=~models.Q(documents__status=Document.Status.DELETED)),
    )
    return render(request, "connectors/_connector_cards.html", {
        "connectors": connectors,
        "connector_types": ConnectorConfig.ConnectorType.choices,
        "should_poll": _has_active_ingestion_jobs(request.project),
    })


@login_required
def connector_jobs_partial(request, pk):
    connector = get_object_or_404(ConnectorConfig, pk=pk, project=request.project)
    context = _connector_jobs_context(connector)
    return render(request, "connectors/_jobs_table.html", context)


@login_required
def connector_detail_live_partial(request, pk):
    """Partial view returning the live-updating section of the detail page."""
    connector = get_object_or_404(ConnectorConfig, pk=pk, project=request.project)
    context = {"connector": connector}
    context.update(_connector_doc_stats(connector))
    context.update(_connector_jobs_context(connector))
    return render(request, "connectors/_detail_live.html", context)


@login_required
def document_content(request, pk, doc_pk):
    """Return document content assembled from chunks as JSON."""
    connector = get_object_or_404(ConnectorConfig, pk=pk, project=request.project)
    doc = get_object_or_404(Document, pk=doc_pk, connector=connector)
    chunks = DocumentChunk.objects.filter(document=doc).order_by("chunk_index")

    # Build HTML content from chunks, grouped by heading
    parts = []
    current_heading = None
    for chunk in chunks:
        if chunk.heading_path and chunk.heading_path != current_heading:
            current_heading = chunk.heading_path
            # Use the last segment of the heading path as the visible heading
            heading_text = current_heading.rsplit(" > ", 1)[-1]
            parts.append(f"<h5 class='mt-3 mb-2'>{escape(heading_text)}</h5>")
        parts.append(f"<p data-chunk-index='{chunk.chunk_index}'>{escape(chunk.content)}</p>")

    is_pdf = (
        doc.doc_type == "application/pdf"
        or doc.title.lower().endswith(".pdf")
    )
    file_url = reverse("document-file", args=[connector.pk, doc.pk])

    return JsonResponse({
        "title": doc.title,
        "doc_type": doc.doc_type,
        "author": doc.author,
        "source_url": doc.source_url,
        "word_count": doc.word_count,
        "chunk_count": doc.chunk_count,
        "status": doc.get_status_display(),
        "modified": doc.source_modified_at.isoformat() if doc.source_modified_at else None,
        "content_html": "\n".join(parts) if parts else "<p class='text-muted'>" + _("Aucun contenu disponible.") + "</p>",
        "file_url": file_url,
        "is_pdf": is_pdf,
    })


@login_required
def document_file(request, pk, doc_pk):
    """Serve the original document file for inline viewing or download."""
    connector = get_object_or_404(ConnectorConfig, pk=pk, project=request.project)
    doc = get_object_or_404(Document, pk=doc_pk, connector=connector)

    # For generic filesystem connector, serve from disk
    if connector.connector_type == ConnectorConfig.ConnectorType.GENERIC:
        cfg = connector.config or {}
        source_type = cfg.get("source_type", "filesystem")
        if source_type == "filesystem":
            base_path = Path(cfg.get("base_path", "")).resolve()
            file_path = (base_path / doc.source_id).resolve()
            # Security: ensure path is within base_path
            if not file_path.is_relative_to(base_path):
                return JsonResponse({"error": "Access denied"}, status=403)
            if file_path.is_file():
                content_type = (
                    mimetypes.guess_type(str(file_path))[0]
                    or "application/octet-stream"
                )
                response = FileResponse(
                    open(file_path, "rb"),
                    content_type=content_type,
                )
                response["Content-Disposition"] = (
                    f'inline; filename="{file_path.name}"'
                )
                return response

    # For remote sources, redirect to source_url
    if doc.source_url:
        return redirect(doc.source_url)

    return JsonResponse({"error": "File not available"}, status=404)
