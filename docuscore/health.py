"""Health check endpoint for production readiness."""
import logging

from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def healthz(request):
    """Return JSON health status for DB connectivity."""
    checks = {}
    healthy = True

    # Database check
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
        healthy = False
        logger.error("Health check: database failed — %s", exc)

    # Vector store check
    try:
        from vectorstore.store import get_vector_store
        store = get_vector_store()
        store.ensure_tables()
        checks["vector_store"] = "ok"
    except Exception as exc:
        checks["vector_store"] = f"error: {exc}"
        healthy = False
        logger.error("Health check: vector_store failed — %s", exc)

    status_code = 200 if healthy else 503
    return JsonResponse({"status": "healthy" if healthy else "unhealthy", "checks": checks}, status=status_code)
