"""Report generation and export models."""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _
from tenants.models import ProjectScopedModel


class Report(ProjectScopedModel):
    """A generated report from an analysis job."""

    class ReportType(models.TextChoices):
        DUPLICATES = "duplicates", _("Rapport de doublons")
        CONTRADICTIONS = "contradictions", _("Rapport de contradictions")
        GAPS = "gaps", _("Rapport de lacunes")
        FULL = "full", _("Rapport d'analyse complet")

    class Format(models.TextChoices):
        HTML = "html", _("HTML")
        CSV = "csv", _("CSV")
        JSON = "json", _("JSON")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis_job = models.ForeignKey(
        "analysis.AnalysisJob", on_delete=models.CASCADE, related_name="reports"
    )
    report_type = models.CharField(max_length=20, choices=ReportType.choices)
    format = models.CharField(max_length=10, choices=Format.choices, default=Format.HTML)
    title = models.CharField(max_length=500)
    summary = models.TextField(blank=True, default="")
    data = models.JSONField(default=dict, help_text="Structured report data")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
