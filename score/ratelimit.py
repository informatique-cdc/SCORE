"""Simple per-user rate limiting using Django's cache framework."""

from functools import wraps

from django.core.cache import cache
from django.http import JsonResponse


def ratelimit(max_calls: int = 10, period: int = 60):
    """Decorator that limits authenticated users to *max_calls* per *period* seconds.

    Returns HTTP 429 with a JSON body when the limit is exceeded.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            user_id = getattr(request.user, "pk", None) or "anon"
            key = f"ratelimit:{view_func.__name__}:{user_id}"
            count = cache.get(key, 0)
            if count >= max_calls:
                return JsonResponse(
                    {"error": "Trop de requêtes. Veuillez patienter avant de réessayer."},
                    status=429,
                )
            cache.set(key, count + 1, timeout=period)
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator
