from django.urls import path
from . import views

urlpatterns = [
    path("", views.connector_list, name="connector-list"),
    path("create/", views.connector_create, name="connector-create"),
    path("_cards/", views.connector_cards_partial, name="connector-cards-partial"),
    path("<uuid:pk>/", views.connector_detail, name="connector-detail"),
    path("<uuid:pk>/sync/", views.connector_sync, name="connector-sync"),
    path("<uuid:pk>/delete/", views.connector_delete, name="connector-delete"),
    path("<uuid:pk>/_jobs/", views.connector_jobs_partial, name="connector-jobs-partial"),
    path("<uuid:pk>/_live/", views.connector_detail_live_partial, name="connector-detail-live-partial"),
    path("<uuid:pk>/documents/<uuid:doc_pk>/content/", views.document_content, name="document-content"),
    path("<uuid:pk>/documents/<uuid:doc_pk>/file/", views.document_file, name="document-file"),
]
