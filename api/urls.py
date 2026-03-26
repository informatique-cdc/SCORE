from django.urls import path

from api import views_tokens, views_documents, views_score, views_audit

app_name = "api"

urlpatterns = [
    path("tokens/", views_tokens.create_token, name="create-token"),
    path("documents/", views_documents.document_list, name="document-list"),
    path("documents/<uuid:doc_id>/", views_documents.document_detail, name="document-detail"),
    path("score/", views_score.score_view, name="score"),
    path("analysis/", views_score.analysis_trigger, name="analysis"),
    path("analysis/<uuid:job_id>/", views_score.analysis_detail_view, name="analysis-detail"),
    path("audit/", views_audit.audit_trigger, name="audit-trigger"),
    path("audit/<uuid:job_id>/", views_audit.audit_detail, name="audit-detail"),
]
