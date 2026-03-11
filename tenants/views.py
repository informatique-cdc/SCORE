from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import translation
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .models import AuditLog, Project, ProjectMembership, Tenant, TenantMembership, log_audit


@login_required
def tenant_select(request):
    memberships = TenantMembership.objects.filter(user=request.user).select_related("tenant")
    if request.method == "POST":
        tenant_id = request.POST.get("tenant_id")
        membership = get_object_or_404(memberships, tenant_id=tenant_id)
        request.session["tenant_id"] = str(membership.tenant_id)
        # Clear project selection when switching tenant
        request.session.pop("project_id", None)
        return redirect("dashboard-home")
    return render(request, "tenants/select.html", {"memberships": memberships})


@login_required
def tenant_create(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, _("Veuillez saisir un nom pour l'espace."))
            return redirect("tenant-select")
        slug = slugify(name)
        base_slug = slug
        counter = 1
        while Tenant.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        if Tenant.objects.filter(name=name).exists():
            messages.error(request, _("Un espace avec ce nom existe déjà."))
            return redirect("tenant-select")
        tenant = Tenant.objects.create(name=name, slug=slug)
        TenantMembership.objects.create(
            tenant=tenant, user=request.user, role=TenantMembership.Role.ADMIN,
        )
        log_audit(
            tenant=tenant, user=request.user,
            action=AuditLog.Action.TENANT_CREATED, target=tenant,
        )
        request.session["tenant_id"] = str(tenant.id)
        request.session.pop("project_id", None)
        messages.success(request, _("Espace « %(name)s » créé avec succès.") % {"name": name})
        return redirect("dashboard-home")
    return redirect("tenant-select")


def _settings_url(tab=None):
    """Build the settings URL with optional tab parameter."""
    url = reverse("tenant-settings")
    if tab:
        url += f"?tab={tab}"
    return url


@login_required
def settings_page(request):
    """Unified settings page with tabs: Profil, Espace, Membres, Projet."""
    tenant = getattr(request, "tenant", None)
    membership = getattr(request, "membership", None)
    is_admin = membership and membership.is_admin
    project = getattr(request, "project", None)
    active_tab = request.GET.get("tab", "profil")

    # Validate tab access
    if active_tab in ("espace", "membres") and not is_admin:
        active_tab = "profil"
    if active_tab == "projet" and (not is_admin or not project):
        active_tab = "profil"

    if request.method == "POST":
        form_type = request.POST.get("form_type")
        if form_type == "profil":
            request.user.first_name = request.POST.get("first_name", "").strip()
            request.user.last_name = request.POST.get("last_name", "").strip()
            request.user.save()
            # Save language preference
            lang = request.POST.get("language", "fr")
            valid_langs = [code for code, name in django_settings.LANGUAGES]
            if lang in valid_langs and membership:
                membership.language = lang
                membership.save(update_fields=["language"])
                translation.activate(lang)
                request.LANGUAGE_CODE = lang
            messages.success(request, _("Profil mis à jour."))
            return redirect(_settings_url("profil"))
        elif form_type == "espace" and is_admin:
            tenant.name = request.POST.get("name", tenant.name)
            tenant.save()
            messages.success(request, _("Espace mis à jour."))
            return redirect(_settings_url("espace"))
        elif form_type == "projet" and is_admin and project:
            project.name = request.POST.get("name", project.name)
            project.description = request.POST.get("description", project.description)
            project.save()
            messages.success(request, _("Projet mis à jour."))
            return redirect(_settings_url("projet"))

    ctx = {"active_tab": active_tab, "tenant": tenant, "languages": django_settings.LANGUAGES}
    if is_admin:
        ctx["members"] = (
            TenantMembership.objects.filter(tenant=tenant)
            .select_related("user")
            .order_by("role", "user__username")
        )
        ctx["roles"] = TenantMembership.Role.choices
        if project:
            ctx["project"] = project
            ctx["project_members"] = (
                ProjectMembership.objects.filter(project=project)
                .select_related("user")
            )
    return render(request, "tenants/settings.html", ctx)


@login_required
def project_list(request):
    if not request.tenant:
        return redirect("tenant-select")
    if request.method == "POST":
        project_id = request.POST.get("project_id")
        pm = get_object_or_404(
            ProjectMembership,
            user=request.user,
            project_id=project_id,
            project__tenant=request.tenant,
        )
        request.session["project_id"] = str(pm.project_id)
        return redirect("dashboard-home")
    projects = Project.objects.for_tenant(request.tenant)
    user_project_ids = set(
        ProjectMembership.objects.filter(user=request.user, project__tenant=request.tenant)
        .values_list("project_id", flat=True)
    )
    return render(request, "tenants/projects.html", {
        "projects": projects,
        "user_project_ids": user_project_ids,
    })


@login_required
def project_create(request):
    if not getattr(request, "membership", None) or not request.membership.is_admin:
        return redirect("project-list")
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        if name:
            slug = slugify(name)
            # Ensure unique slug within tenant
            base_slug = slug
            counter = 1
            while Project.objects.filter(tenant=request.tenant, slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            project = Project.objects.create(
                tenant=request.tenant,
                name=name,
                slug=slug,
                description=description,
            )
            # Auto-create membership for creator as admin
            ProjectMembership.objects.create(
                project=project,
                user=request.user,
                role=TenantMembership.Role.ADMIN,
            )
            log_audit(
                tenant=request.tenant, user=request.user,
                action=AuditLog.Action.PROJECT_CREATED, target=project,
            )
            request.session["project_id"] = str(project.id)
            return redirect("dashboard-home")
    return render(request, "tenants/project_create.html")


# ── User management (tenant admin only) ──────────────────────────


@login_required
def user_invite(request):
    if request.method != "POST":
        return redirect(_settings_url("membres"))
    if not getattr(request, "membership", None) or not request.membership.is_admin:
        return redirect("dashboard-home")

    email = request.POST.get("email", "").strip().lower()
    role = request.POST.get("role", TenantMembership.Role.VIEWER)

    if not email:
        messages.error(request, _("Veuillez saisir une adresse email."))
        return redirect(_settings_url("membres"))

    if role not in dict(TenantMembership.Role.choices):
        role = TenantMembership.Role.VIEWER

    # Find or create user
    user = User.objects.filter(email=email).first()
    if not user:
        # Create inactive user — they'll set password via signup or password reset
        username = email.split("@")[0]
        base_username = username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1
        user = User.objects.create_user(username=username, email=email, is_active=True)
        user.set_unusable_password()
        user.save()

    # Check if already a member
    if TenantMembership.objects.filter(tenant=request.tenant, user=user).exists():
        messages.warning(request, _("%(email)s est déjà membre de cet espace.") % {"email": email})
        return redirect(_settings_url("membres"))

    # Create tenant membership
    TenantMembership.objects.create(tenant=request.tenant, user=user, role=role)
    log_audit(
        tenant=request.tenant, user=request.user,
        action=AuditLog.Action.USER_INVITED,
        target_label=email,
        detail={"role": role, "target_user_id": str(user.pk)},
    )

    # Create project memberships for all projects in this tenant
    projects = Project.objects.for_tenant(request.tenant)
    for project in projects:
        ProjectMembership.objects.get_or_create(
            project=project, user=user, defaults={"role": role}
        )

    role_label = dict(TenantMembership.Role.choices).get(role)
    messages.success(request, _("%(email)s a été invité avec le rôle %(role)s.") % {"email": email, "role": role_label})
    return redirect(_settings_url("membres"))


@login_required
def user_role_update(request, pk):
    if request.method != "POST":
        return redirect(_settings_url("membres"))
    if not getattr(request, "membership", None) or not request.membership.is_admin:
        return redirect("dashboard-home")

    target = get_object_or_404(TenantMembership, pk=pk, tenant=request.tenant)
    new_role = request.POST.get("role", "").strip()

    if new_role not in dict(TenantMembership.Role.choices):
        messages.error(request, _("Rôle invalide."))
        return redirect(_settings_url("membres"))

    # Guard: cannot demote the last admin
    if target.is_admin and new_role != TenantMembership.Role.ADMIN:
        admin_count = TenantMembership.objects.filter(
            tenant=request.tenant, role=TenantMembership.Role.ADMIN
        ).count()
        if admin_count <= 1:
            messages.error(request, _("Impossible de retirer le dernier administrateur."))
            return redirect(_settings_url("membres"))

    old_role = target.role
    target.role = new_role
    target.save()
    log_audit(
        tenant=request.tenant, user=request.user,
        action=AuditLog.Action.ROLE_CHANGED, target=target,
        target_label=target.user.get_full_name() or target.user.username,
        detail={"old_role": old_role, "new_role": new_role},
    )

    # Sync project memberships
    ProjectMembership.objects.filter(
        user=target.user, project__tenant=request.tenant
    ).update(role=new_role)

    messages.success(request, _("Rôle de %(name)s mis à jour.") % {"name": target.user.get_full_name() or target.user.username})
    return redirect(_settings_url("membres"))


@login_required
def user_remove(request, pk):
    if request.method != "POST":
        return redirect(_settings_url("membres"))
    if not getattr(request, "membership", None) or not request.membership.is_admin:
        return redirect("dashboard-home")

    target = get_object_or_404(TenantMembership, pk=pk, tenant=request.tenant)

    # Guard: cannot remove self
    if target.user == request.user:
        messages.error(request, _("Vous ne pouvez pas vous retirer vous-même."))
        return redirect(_settings_url("membres"))

    # Delete project memberships first, then tenant membership
    ProjectMembership.objects.filter(
        user=target.user, project__tenant=request.tenant
    ).delete()
    username = target.user.get_full_name() or target.user.username
    log_audit(
        tenant=request.tenant, user=request.user,
        action=AuditLog.Action.USER_REMOVED,
        target_label=username,
        detail={"target_user_id": str(target.user_id), "role": target.role},
    )
    target.delete()

    messages.success(request, _("%(name)s a été retiré de l'espace.") % {"name": username})
    return redirect(_settings_url("membres"))


# ── Project management ────────────────────────────────────────────


@login_required
@require_POST
def project_delete(request, pk):
    """Delete a project and all its data (documents, vectors, analysis…)."""
    membership = getattr(request, "membership", None)
    if not membership or not membership.is_admin:
        return redirect("dashboard-home")

    project = get_object_or_404(Project, pk=pk, tenant=request.tenant)

    # Clean up vectors (not managed by Django ORM)
    from ingestion.models import Document
    from vectorstore.store import get_vector_store

    doc_ids = list(
        Document.objects.filter(connector__project=project).values_list("id", flat=True)
    )
    if doc_ids:
        store = get_vector_store()
        store.delete_by_documents([str(d) for d in doc_ids])

    log_audit(
        tenant=request.tenant, user=request.user,
        action=AuditLog.Action.PROJECT_DELETED, target=project,
    )
    project.delete()

    # Clear project from session
    request.session.pop("project_id", None)
    messages.success(request, _("Projet supprimé."))
    return redirect("project-list")
