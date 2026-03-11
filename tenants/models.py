"""
Multi-tenant models.

Every data-bearing model in SCORE has a FK to Tenant.
TenantMembership links users to tenants with role-based access.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Tenant(models.Model):
    """An isolated workspace. All documents, jobs, and reports belong to a tenant."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=80, unique=True)
    max_documents = models.PositiveIntegerField(default=10_000)
    max_connectors = models.PositiveIntegerField(default=10)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TenantMembership(models.Model):
    """Links a Django user to a tenant with a specific role."""

    class Role(models.TextChoices):
        ADMIN = "admin", _("Administrateur")
        EDITOR = "editor", _("Éditeur")
        VIEWER = "viewer", _("Lecteur")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tenant_memberships"
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)
    language = models.CharField(
        max_length=10,
        choices=settings.LANGUAGES,
        default="fr",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("tenant", "user")
        ordering = ["tenant", "role"]

    def __str__(self):
        return f"{self.user} @ {self.tenant} ({self.role})"

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def can_edit(self):
        return self.role in (self.Role.ADMIN, self.Role.EDITOR)


class TenantScopedManager(models.Manager):
    """Manager that filters by tenant when tenant is set on the queryset."""

    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)


class TenantScopedModel(models.Model):
    """Abstract base for all tenant-scoped models."""

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="%(class)s_set"
    )

    objects = TenantScopedManager()

    class Meta:
        abstract = True


class Project(TenantScopedModel):
    """An independent project within a tenant, with its own documents and analyses."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("tenant", "slug")
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProjectMembership(models.Model):
    """Links a Django user to a project with a specific role."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="project_memberships"
    )
    role = models.CharField(max_length=10, choices=TenantMembership.Role.choices, default=TenantMembership.Role.VIEWER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("project", "user")
        ordering = ["project", "role"]

    def __str__(self):
        return f"{self.user} @ {self.project} ({self.role})"

    @property
    def is_admin(self):
        return self.role == TenantMembership.Role.ADMIN

    @property
    def can_edit(self):
        return self.role in (TenantMembership.Role.ADMIN, TenantMembership.Role.EDITOR)


class AuditLog(models.Model):
    """Immutable log of privileged operations for compliance and debugging."""

    class Action(models.TextChoices):
        USER_INVITED = "user_invited", _("Utilisateur invité")
        USER_REMOVED = "user_removed", _("Utilisateur retiré")
        ROLE_CHANGED = "role_changed", _("Rôle modifié")
        PROJECT_CREATED = "project_created", _("Projet créé")
        PROJECT_DELETED = "project_deleted", _("Projet supprimé")
        TENANT_CREATED = "tenant_created", _("Espace créé")
        ANALYSIS_DELETED = "analysis_deleted", _("Analyse supprimée")
        CONNECTOR_DELETED = "connector_deleted", _("Connecteur supprimé")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="audit_logs",
        null=True, blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name="audit_logs",
    )
    action = models.CharField(max_length=30, choices=Action.choices)
    target_type = models.CharField(max_length=50, blank=True, default="")
    target_id = models.CharField(max_length=200, blank=True, default="")
    target_label = models.CharField(max_length=300, blank=True, default="")
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self):
        return f"[{self.get_action_display()}] {self.target_label} by {self.user}"


def log_audit(*, tenant, user, action, target=None, target_label="", detail=None):
    """Helper to create an AuditLog entry."""
    target_type = ""
    target_id = ""
    if target is not None:
        target_type = type(target).__name__
        target_id = str(target.pk)
        if not target_label:
            target_label = str(target)
    AuditLog.objects.create(
        tenant=tenant,
        user=user,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        detail=detail or {},
    )


class ProjectScopedManager(TenantScopedManager):
    """Manager that adds project filtering."""

    def for_project(self, project):
        return self.filter(project=project)


class ProjectScopedModel(TenantScopedModel):
    """Abstract base for models scoped to a project within a tenant."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="%(class)s_set",
        null=True, blank=True,
    )

    objects = ProjectScopedManager()

    class Meta:
        abstract = True
