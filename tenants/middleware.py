"""
Tenant middleware: resolves the current tenant for each request.

The tenant is determined by:
1. Session key 'tenant_id' (user switches tenant in UI)
2. First tenant the user belongs to (default)
"""

import logging

from django.shortcuts import redirect
from django.urls import reverse
from django.utils import translation

from .models import ProjectMembership, TenantMembership

logger = logging.getLogger(__name__)


class TenantMiddleware:
    """Attach request.tenant and request.membership based on authenticated user."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.membership = None
        request.project = None
        request.project_membership = None

        if not request.user.is_authenticated:
            return self.get_response(request)

        # Skip tenant resolution for admin
        if request.path.startswith("/admin/"):
            return self.get_response(request)

        tenant_id = request.session.get("tenant_id")
        membership = None

        if tenant_id:
            membership = (
                TenantMembership.objects.filter(user=request.user, tenant_id=tenant_id)
                .select_related("tenant")
                .first()
            )

        if not membership:
            membership = (
                TenantMembership.objects.filter(user=request.user).select_related("tenant").first()
            )
            if membership:
                request.session["tenant_id"] = str(membership.tenant_id)

        if membership:
            request.tenant = membership.tenant
            request.membership = membership

            # Activate user's preferred language
            lang = getattr(membership, "language", "fr") or "fr"
            translation.activate(lang)
            request.LANGUAGE_CODE = lang

            # Resolve project
            self._resolve_project(request, membership.tenant)
        elif not request.path.startswith(("/auth/", "/admin/", "/tenants/", "/api/")):
            return redirect(reverse("tenant-select"))

        return self.get_response(request)

    def _resolve_project(self, request, tenant):
        """Resolve the current project from session or fallback."""
        project_id = request.session.get("project_id")
        project_membership = None

        if project_id:
            project_membership = (
                ProjectMembership.objects.filter(
                    user=request.user,
                    project_id=project_id,
                    project__tenant=tenant,
                )
                .select_related("project")
                .first()
            )

        if not project_membership:
            project_membership = (
                ProjectMembership.objects.filter(
                    user=request.user,
                    project__tenant=tenant,
                )
                .select_related("project")
                .first()
            )
            if project_membership:
                request.session["project_id"] = str(project_membership.project_id)

        if project_membership:
            request.project = project_membership.project
            request.project_membership = project_membership
        elif not request.path.startswith(("/auth/", "/admin/", "/tenants/", "/api/")):
            return redirect(reverse("project-list"))
