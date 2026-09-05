import hashlib
import json
import secrets

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from api.models import APIToken
from tenants.models import Project


@csrf_exempt
@require_POST
def create_token(request):
    """Create a new API token. Requires superuser (session auth)."""
    if not request.user.is_authenticated or not request.user.is_superuser:
        return JsonResponse(
            {"error": "Superuser access required", "code": "FORBIDDEN"}, status=403
        )

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body", "code": "BAD_REQUEST"}, status=400)

    name = body.get("name", "").strip()
    project_id = body.get("project_id", "")

    if not name:
        return JsonResponse({"error": "name is required", "code": "BAD_REQUEST"}, status=400)

    try:
        project = Project.objects.select_related("tenant").get(id=project_id)
    except (Project.DoesNotExist, ValueError):
        return JsonResponse({"error": "Project not found", "code": "NOT_FOUND"}, status=404)

    raw_token = f"score_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    APIToken.objects.create(
        key_hash=key_hash,
        user=request.user,
        tenant=project.tenant,
        project=project,
        name=name,
    )

    return JsonResponse({"token": raw_token, "name": name}, status=201)
