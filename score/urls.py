from django.contrib import admin
from django.urls import include, path

from score.health import healthz

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    # Auth (allauth)
    path("auth/", include("allauth.urls")),
    # Bascule de langue de l'en-tête (set_language)
    path("i18n/", include("django.conf.urls.i18n")),
    # Apps
    path("dashboard/", include("dashboard.urls")),
    path("connectors/", include("connectors.urls")),
    path("analysis/", include("analysis.urls")),
    path("reports/", include("reports.urls")),
    path("tenants/", include("tenants.urls")),
    path("chat/", include("chat.urls")),
    # Root redirect
    path(
        "", lambda r: __import__("django.shortcuts", fromlist=["redirect"]).redirect("/dashboard/")
    ),
]
