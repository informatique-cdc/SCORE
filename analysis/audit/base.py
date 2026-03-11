"""Abstract base class for audit axes."""
import logging
import time
from abc import ABC, abstractmethod

from django.conf import settings

logger = logging.getLogger(__name__)


class BaseAuditAxis(ABC):
    """Base class for all audit axis implementations."""

    axis_key: str = ""  # Override in subclass
    axis_label: str = ""

    def __init__(self, project, audit_job, config=None):
        self.project = project
        self.tenant = project.tenant
        self.audit_job = audit_job
        self.config = config or self._load_config()

    def _load_config(self):
        audit_cfg = settings.APP_CONFIG.get("audit", {})
        return audit_cfg.get(self.axis_key, {})

    def execute(self):
        """Run the axis and return (score, metrics, chart_data, details, duration)."""
        start = time.time()
        try:
            score, metrics, chart_data, details = self.analyze()
            score = max(0.0, min(100.0, score))
        except Exception:
            logger.exception("Audit axis %s failed", self.axis_key)
            raise
        duration = time.time() - start
        return score, metrics, chart_data, details, duration

    @abstractmethod
    def analyze(self):
        """
        Run the axis analysis.
        Returns: (score: float, metrics: dict, chart_data: dict, details: dict)
        """
        ...
