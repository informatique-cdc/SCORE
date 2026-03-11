"""Template context processor for tenant information."""

from .models import ProjectMembership, TenantMembership


def tenant_context(request):
    ctx = {
        "current_tenant": getattr(request, "tenant", None),
        "membership": getattr(request, "membership", None),
        "current_project": getattr(request, "project", None),
        "project_membership": getattr(request, "project_membership", None),
        "user_projects": [],
        "user_tenants": [],
        "onboarding_steps": None,
    }
    tenant = ctx["current_tenant"]
    if hasattr(request, "user") and request.user.is_authenticated:
        ctx["user_tenants"] = list(
            TenantMembership.objects.filter(user=request.user)
            .select_related("tenant")
            .order_by("tenant__name")
        )
        if tenant:
            ctx["user_projects"] = list(
                ProjectMembership.objects.filter(
                    user=request.user,
                    project__tenant=tenant,
                )
                .select_related("project")
                .order_by("project__name")
            )
            ctx["onboarding_steps"] = _get_onboarding_steps(tenant, ctx["current_project"])
    return ctx


def _get_onboarding_steps(tenant, project):
    """Build the onboarding checklist for the sidebar."""
    from connectors.models import ConnectorConfig
    from ingestion.models import Document
    from analysis.models import AnalysisJob

    has_project = project is not None
    has_connector = False
    has_documents = False
    has_analysis = False

    if has_project:
        has_connector = ConnectorConfig.objects.filter(
            tenant=tenant,
            project=project,
        ).exists()
        has_documents = Document.objects.filter(
            tenant=tenant,
            project=project,
            status=Document.Status.READY,
        ).exists()
        has_analysis = AnalysisJob.objects.filter(
            tenant=tenant,
            project=project,
            status=AnalysisJob.Status.COMPLETED,
        ).exists()

    steps = [
        {"done": True, "label": "Créer un espace", "url": None},
        {"done": has_project, "label": "Créer un projet", "url": "project-create"},
        {"done": has_connector, "label": "Ajouter un connecteur", "url": "connector-create"},
        {"done": has_documents, "label": "Synchroniser des documents", "url": "connector-list"},
        {"done": has_analysis, "label": "Lancer une analyse", "url": "analysis-list"},
    ]

    # Hide checklist once everything is done
    all_done = all(s["done"] for s in steps)
    if all_done:
        return None

    return steps
