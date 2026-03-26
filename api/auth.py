import hashlib
import functools

from django.http import JsonResponse

from api.models import APIToken


def authenticate_token(raw_token):
    """Validate a raw token string. Returns dict with user/tenant/project or None."""
    key_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    try:
        token = APIToken.objects.select_related("user", "tenant", "project").get(
            key_hash=key_hash, is_active=True
        )
    except APIToken.DoesNotExist:
        return None
    return {
        "user": token.user,
        "tenant": token.tenant,
        "project": token.project,
        "token": token,
    }


def require_api_token(view_func):
    """Decorator that enforces Bearer token auth on a view."""
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JsonResponse(
                {"error": "Missing or invalid Authorization header", "code": "AUTH_REQUIRED"},
                status=401,
            )
        raw_token = auth_header[7:]
        result = authenticate_token(raw_token)
        if result is None:
            return JsonResponse(
                {"error": "Invalid or inactive token", "code": "INVALID_TOKEN"},
                status=401,
            )
        request.api_user = result["user"]
        request.api_tenant = result["tenant"]
        request.api_project = result["project"]
        return view_func(request, *args, **kwargs)
    return wrapper
