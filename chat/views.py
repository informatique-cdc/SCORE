"""Chat views: page rendering, RAG API, and conversation management."""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from docuscore.ratelimit import ratelimit
from docuscore.utils import parse_json_body
from ingestion.models import Document
from llm.client import get_llm_client
from llm.prompt_loader import get_prompt

from .models import ChatConfig, Conversation, Message
from .rag import ask_documents

logger = logging.getLogger(__name__)


@login_required
def chat_home(request):
    """Render the chat page with conversation history in the sidebar."""
    if not request.project:
        return redirect("project-list")

    conversations = Conversation.objects.filter(
        tenant=request.tenant,
        project=request.project,
        user=request.user,
    ).only("id", "title", "updated_at")[:50]

    # Load custom system prompt (if any)
    config = ChatConfig.objects.filter(
        tenant=request.tenant,
        project=request.project,
        user=request.user,
    ).first()
    custom_system_prompt = config.system_prompt if config else ""

    # Check if the project has any documents to chat about
    has_documents = Document.objects.filter(
        project=request.project,
    ).exclude(status=Document.Status.DELETED).exists()

    return render(request, "chat/home.html", {
        "conversations": conversations,
        "default_system_prompt": get_prompt("CHAT_QA_SYSTEM"),
        "custom_system_prompt": custom_system_prompt,
        "has_documents": has_documents,
    })


@login_required
@require_POST
@ratelimit(max_calls=30, period=60)
def chat_ask(request):
    """POST JSON endpoint: send a message, get a RAG response, persist both."""
    if not request.project:
        return JsonResponse({"error": str(_("Aucun projet sélectionné."))}, status=400)

    body, err = parse_json_body(request)
    if err:
        return err

    message = (body.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": str(_("Le message est vide."))}, status=400)

    conversation_id = body.get("conversation_id")
    history = body.get("history") or []
    tools = body.get("tools") or []

    # Resolve or create conversation
    if conversation_id:
        conversation = get_object_or_404(
            Conversation,
            id=conversation_id,
            tenant=request.tenant,
            project=request.project,
            user=request.user,
        )
    else:
        conversation = Conversation.objects.create(
            tenant=request.tenant,
            project=request.project,
            user=request.user,
            tools=tools,
        )

    # Persist tools selection on the conversation
    if conversation.tools != tools:
        conversation.tools = tools
        conversation.save(update_fields=["tools"])

    # Save user message
    Message.objects.create(conversation=conversation, role="user", content=message)

    # Load custom system prompt (if any)
    config = ChatConfig.objects.filter(
        tenant=request.tenant,
        project=request.project,
        user=request.user,
    ).first()
    custom_prompt = config.system_prompt if config and config.system_prompt else None

    # Call RAG pipeline
    try:
        result = ask_documents(
            question=message,
            tenant=request.tenant,
            project=request.project,
            history=history,
            tools=tools,
            system_prompt_template=custom_prompt,
        )
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("Chat RAG pipeline input error: %s", exc)
        return JsonResponse(
            {"error": str(_("Une erreur est survenue lors du traitement de votre question."))},
            status=500,
        )
    except (ConnectionError, TimeoutError, OSError) as exc:
        logger.error("Chat RAG pipeline network error: %s", exc)
        return JsonResponse(
            {"error": str(_("Une erreur est survenue lors du traitement de votre question."))},
            status=500,
        )
    except Exception:
        logger.exception("Chat RAG pipeline unexpected error")
        return JsonResponse(
            {"error": str(_("Une erreur est survenue lors du traitement de votre question."))},
            status=500,
        )

    # Guard against empty answer (content filter, model refusal, etc.)
    if not result.get("answer", "").strip():
        logger.warning("Empty answer from RAG pipeline, returning fallback")
        result["answer"] = str(_("Je n'ai pas pu générer de réponse. Veuillez reformuler votre question."))

    # Save assistant message
    Message.objects.create(
        conversation=conversation,
        role="assistant",
        content=result["answer"],
        sources=result.get("sources", []),
        suggestions=result.get("suggestions", []),
    )

    # Touch updated_at
    conversation.save(update_fields=["updated_at"])

    # Generate title after first exchange (conversation has exactly 2 messages)
    if not conversation.title:
        title = ""
        try:
            llm = get_llm_client()
            resp = llm.chat(
                user_message=message,
                system=get_prompt("CHAT_TITLE_SYSTEM"),
                temperature=0.3,
                max_tokens=30,
            )
            title = resp.content.strip().rstrip(".")[:200]
        except (ConnectionError, TimeoutError, OSError, ValueError) as exc:
            logger.warning("Failed to generate conversation title: %s", exc)
        # Fallback: truncate the user message
        if not title:
            title = message[:80].strip()
            if len(message) > 80:
                title = title.rsplit(" ", 1)[0] + "\u2026"
        conversation.title = title
        conversation.save(update_fields=["title"])

    response_data = {
        "answer": result["answer"],
        "sources": result.get("sources", []),
        "suggestions": result.get("suggestions", []),
        "conversation_id": str(conversation.id),
        "title": conversation.title or "",
    }

    return JsonResponse(response_data)


@login_required
@require_POST
def save_system_prompt(request):
    """POST JSON endpoint: save a custom system prompt for the current project."""
    if not request.project:
        return JsonResponse({"error": str(_("Aucun projet sélectionné."))}, status=400)

    body, err = parse_json_body(request)
    if err:
        return err

    prompt = (body.get("system_prompt") or "").strip()

    # Validate that {context} variable is present if prompt is not empty
    if prompt and "{context}" not in prompt:
        return JsonResponse(
            {"error": str(_("La variable {context} est obligatoire dans le prompt système."))},
            status=400,
        )

    config, _created = ChatConfig.objects.get_or_create(
        tenant=request.tenant,
        project=request.project,
        user=request.user,
    )
    config.system_prompt = prompt
    config.save(update_fields=["system_prompt", "updated_at"])

    return JsonResponse({"ok": True})


@login_required
def conversation_messages(request, pk):
    """GET: return all messages for a conversation."""
    conversation = get_object_or_404(
        Conversation,
        id=pk,
        tenant=request.tenant,
        project=request.project,
        user=request.user,
    )
    messages = conversation.messages.values("role", "content", "sources", "suggestions")
    return JsonResponse({
        "conversation_id": str(conversation.id),
        "title": conversation.title,
        "tools": conversation.tools or [],
        "messages": list(messages),
    })


@login_required
@require_POST
def conversation_delete(request, pk):
    """POST: delete a conversation."""
    conversation = get_object_or_404(
        Conversation,
        id=pk,
        tenant=request.tenant,
        project=request.project,
        user=request.user,
    )
    conversation.delete()
    return JsonResponse({"ok": True})
