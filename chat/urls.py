from django.urls import path

from . import views

urlpatterns = [
    path("", views.chat_home, name="chat-home"),
    path("ask/", views.chat_ask, name="chat-ask"),
    path("config/system-prompt/", views.save_system_prompt, name="chat-save-system-prompt"),
    path(
        "conversations/<uuid:pk>/messages/",
        views.conversation_messages,
        name="chat-conversation-messages",
    ),
    path(
        "conversations/<uuid:pk>/delete/",
        views.conversation_delete,
        name="chat-conversation-delete",
    ),
]
