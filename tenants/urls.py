from django.urls import path
from . import views

urlpatterns = [
    path("select/", views.tenant_select, name="tenant-select"),
    path("create/", views.tenant_create, name="tenant-create"),
    path("settings/", views.settings_page, name="tenant-settings"),
    path("projects/", views.project_list, name="project-list"),
    path("projects/create/", views.project_create, name="project-create"),
    # User management actions (redirect back to settings?tab=membres)
    path("users/invite/", views.user_invite, name="user-invite"),
    path("users/<uuid:pk>/role/", views.user_role_update, name="user-role-update"),
    path("users/<uuid:pk>/remove/", views.user_remove, name="user-remove"),
    path("projects/<uuid:pk>/delete/", views.project_delete, name="project-delete"),
]
