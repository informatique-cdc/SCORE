import uuid

from django.conf import settings
from django.db import models


class APIToken(models.Model):
    """Bearer token for API authentication, scoped to a tenant and project."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens"
    )
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    project = models.ForeignKey("tenants.Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.user.username})"
