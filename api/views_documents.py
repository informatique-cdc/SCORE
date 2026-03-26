import hashlib
import json
import uuid

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from api.auth import require_api_token
from connectors.models import ConnectorConfig
from ingestion.chunking import chunk_document
from ingestion.hashing import hash_content
from ingestion.models import Document, DocumentChunk
from llm.client import get_llm_client
from vectorstore.store import get_vector_store


def _get_or_create_api_connector(tenant, project):
    connector, _ = ConnectorConfig.objects.get_or_create(
        tenant=tenant,
        project=project,
        connector_type="generic",
        name="API",
        defaults={"config": {"source_type": "api"}, "enabled": True},
    )
    return connector


@csrf_exempt
@require_api_token
@require_http_methods(["GET", "POST"])
def document_list(request):
    if request.method == "GET":
        return _list_documents(request)
    return _create_document(request)


def _list_documents(request):
    page = int(request.GET.get("page", 1))
    page_size = min(int(request.GET.get("page_size", 50)), 200)
    offset = (page - 1) * page_size

    qs = Document.objects.filter(
        tenant=request.api_tenant, project=request.api_project
    ).exclude(status=Document.Status.DELETED)

    total = qs.count()
    docs = qs[offset : offset + page_size]

    return JsonResponse({
        "documents": [
            {
                "id": str(d.id),
                "title": d.title,
                "status": d.status,
                "word_count": d.word_count,
                "chunk_count": d.chunk_count,
                "content_type": d.doc_type,
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


def _create_document(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON", "code": "BAD_REQUEST"}, status=400)

    title = body.get("title", "").strip()
    content = body.get("content", "").strip()
    content_type = body.get("content_type", "text/plain")
    metadata = body.get("metadata", {})

    if not title or not content:
        return JsonResponse(
            {"error": "title and content are required", "code": "BAD_REQUEST"}, status=400
        )

    tenant = request.api_tenant
    project = request.api_project
    connector = _get_or_create_api_connector(tenant, project)

    source_id = f"api:{uuid.uuid4()}"
    content_hash_val = hash_content(content)

    doc = Document.objects.create(
        tenant=tenant,
        project=project,
        connector=connector,
        source_id=source_id,
        title=title,
        content_hash=content_hash_val,
        doc_type=content_type,
        word_count=len(content.split()),
        status=Document.Status.INGESTED,
        author=metadata.get("author", ""),
        path=metadata.get("path", ""),
    )

    chunks_data = chunk_document(content)
    chunk_objects = []
    for i, chunk in enumerate(chunks_data):
        chunk_objects.append(
            DocumentChunk(
                tenant=tenant,
                document=doc,
                chunk_index=i,
                content=chunk.content,
                token_count=chunk.token_count,
                heading_path=chunk.heading_path or "",
                content_hash=chunk.content_hash,
            )
        )
    DocumentChunk.objects.bulk_create(chunk_objects)
    doc.chunk_count = len(chunk_objects)

    # Try to embed, but don't fail if LLM is unavailable
    try:
        llm = get_llm_client()
        vec_store = get_vector_store()
        texts = [c.content for c in chunk_objects]
        if texts:
            embeddings = llm.embed(texts)
            for chunk_obj, embedding in zip(chunk_objects, embeddings):
                vec_store.upsert(
                    chunk_id=str(chunk_obj.id),
                    embedding=embedding,
                    tenant_id=tenant.slug,
                    document_id=str(doc.id),
                    doc_type=content_type,
                    source_type="api",
                    project_id=str(project.id),
                )
                chunk_obj.has_embedding = True
            DocumentChunk.objects.bulk_update(chunk_objects, ["has_embedding"])
        doc.status = Document.Status.READY
    except Exception:
        doc.status = Document.Status.INGESTED

    doc.save()

    return JsonResponse(
        {
            "id": str(doc.id),
            "title": doc.title,
            "status": doc.status,
            "word_count": doc.word_count,
            "chunk_count": doc.chunk_count,
        },
        status=201,
    )


@csrf_exempt
@require_api_token
@require_http_methods(["GET", "DELETE"])
def document_detail(request, doc_id):
    try:
        doc = Document.objects.get(
            id=doc_id, tenant=request.api_tenant, project=request.api_project
        )
    except Document.DoesNotExist:
        return JsonResponse({"error": "Document not found", "code": "NOT_FOUND"}, status=404)

    if request.method == "DELETE":
        vec_store = get_vector_store()
        vec_store.delete_by_document(str(doc.id))
        doc.delete()
        return JsonResponse({}, status=204)

    return JsonResponse({
        "id": str(doc.id),
        "title": doc.title,
        "status": doc.status,
        "word_count": doc.word_count,
        "chunk_count": doc.chunk_count,
        "content_type": doc.doc_type,
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat(),
    })
