from django.urls import path

from api import views_tokens

app_name = "api"

urlpatterns = [
    path("tokens/", views_tokens.create_token, name="create-token"),
]
