import uuid

from django.conf import settings
from django.db import models

from tenants.models import Tenant


class Feedback(models.Model):
    class Type(models.TextChoices):
        FEEDBACK = "feedback", "Feedback"
        ISSUE = "issue", "Issue"

    class Area(models.TextChoices):
        CONNECTORS = "connectors", "Connecteurs"
        ANALYSIS = "analysis", "Analyses"
        AUDIT = "audit", "Audit RAG"
        ASSISTANT = "assistant", "Assistant"
        USERS = "users", "Gestion utilisateurs"
        OTHER = "other", "Autre"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.SET_NULL, null=True, blank=True, related_name="feedbacks"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="feedbacks"
    )
    feedback_type = models.CharField(max_length=20, choices=Type.choices)
    area = models.CharField(max_length=20, choices=Area.choices)
    subject = models.CharField(max_length=200)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_feedback_type_display()}] {self.subject}"
