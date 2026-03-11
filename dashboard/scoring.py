"""Backward-compatible re-export — scoring logic lives in docuscore.scoring."""

from docuscore.scoring import (  # noqa: F401
    _empty_result,
    _grade,
    health_score,
    build_breakdown_json,
    compute_docuscore,
    compute_docuscore_detail,
    compute_docuscore_for_job,
    compute_penalty_score,
    grade,
)
