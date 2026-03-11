"""Shared utilities for the DocuScore project."""

import json

from django.http import JsonResponse

# Maximum request body size for JSON endpoints (1 MB)
MAX_JSON_BODY_SIZE = 1_048_576


def parse_json_body(request, max_size=MAX_JSON_BODY_SIZE):
    """Parse JSON request body with size validation.

    Returns (data_dict, None) on success, or (None, JsonResponse) on error.
    """
    if len(request.body) > max_size:
        return None, JsonResponse(
            {"error": f"Request body too large (max {max_size} bytes)."},
            status=413,
        )
    try:
        return json.loads(request.body), None
    except (json.JSONDecodeError, ValueError):
        return None, JsonResponse({"error": "Invalid JSON."}, status=400)
