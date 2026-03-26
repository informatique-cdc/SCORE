from django.urls import path

from api import views_tokens, views_documents

app_name = "api"

urlpatterns = [
    path("tokens/", views_tokens.create_token, name="create-token"),
    path("documents/", views_documents.document_list, name="document-list"),
    path("documents/<uuid:doc_id>/", views_documents.document_detail, name="document-detail"),
]
