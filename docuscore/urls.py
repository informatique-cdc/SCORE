from django.contrib import admin
from django.urls import include, path

from docuscore.health import healthz

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    # Auth (allauth)
    path("auth/", include("allauth.urls")),
    # Apps
    path("dashboard/", include("dashboard.urls")),
    path("connectors/", include("connectors.urls")),
    path("analysis/", include("analysis.urls")),
    path("reports/", include("reports.urls")),
    path("tenants/", include("tenants.urls")),
    path("chat/", include("chat.urls")),
    # Root redirect
    path("", lambda r: __import__("django.shortcuts", fromlist=["redirect"]).redirect("/dashboard/")),
]
