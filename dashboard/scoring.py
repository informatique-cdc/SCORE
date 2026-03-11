"""Backward-compatible re-export — scoring logic lives in score.scoring."""

from score.scoring import (  # noqa: F401
    _empty_result,
    _grade,
    health_score,
    build_breakdown_json,
    compute_score,
    compute_score_detail,
    compute_score_for_job,
    compute_penalty_score,
    grade,
)
