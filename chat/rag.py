"""
RAG pipeline for document Q&A chat.

Embeds the user question, searches the vector store, builds context,
and calls the LLM with conversation history.

Supports 8 composable RAG techniques via the ``tools`` parameter:
  Phase 1 (query expansion):  decomposition > rag-fusion > hyde  (mutually exclusive)
  Phase 2 (retrieval):        standard vector search; graph-rag adds concept context
  Phase 3 (post-retrieval):   crag → reranking → self-rag  (stacks in order)
  Phase 4 (generation):       standard, or agentic-rag overrides everything
"""

import logging

from ingestion.models import Document, DocumentChunk
from llm.client import get_llm_client
from llm.prompt_loader import get_prompt
from vectorstore.store import get_vector_store

from .rag_techniques import (
    agentic_rag,
    crag_evaluate,
    decompose_question,
    graph_rag_context,
    hyde,
    rag_fusion,
    rerank_chunks,
    self_rag_filter,
    synthesize_sub_results,
)

logger = logging.getLogger(__name__)

# Max history turns to keep in the LLM context
MAX_HISTORY_TURNS = 10
SEARCH_K = 5


# ---------------------------------------------------------------------------
# Helpers extracted from the former monolithic function
# ---------------------------------------------------------------------------


def _load_chunks_and_docs(results):
    """Load DocumentChunk and Document objects for search results.

    Returns (chunks_by_id, docs_by_id) keyed by string IDs.
    """
    chunk_ids = [r["chunk_id"] for r in results]
    doc_ids = list({r["document_id"] for r in results})

    chunks_by_id = {str(c.id): c for c in DocumentChunk.objects.filter(id__in=chunk_ids)}
    docs_by_id = {str(d.id): d for d in Document.objects.filter(id__in=doc_ids)}
    return chunks_by_id, docs_by_id


def _build_context_and_sources(results, chunks_by_id, docs_by_id):
    """Build context string and sources list from search results.

    Returns (context_str, sources).
    """
    context_parts = []
    sources = []
    for r in results:
        chunk = chunks_by_id.get(r["chunk_id"])
        doc = docs_by_id.get(r["document_id"])
        if not chunk or not doc:
            continue

        heading = f" > {chunk.heading_path}" if chunk.heading_path else ""
        context_parts.append(f"[{doc.title}{heading}]\n{chunk.content}")

        sources.append(
            {
                "title": doc.title,
                "chunk": chunk.content[:200],
                "chunk_index": chunk.chunk_index,
                "similarity": round(r.get("similarity", 0), 3),
                "document_id": str(doc.id),
                "source_url": doc.source_url or "",
                "doc_type": doc.doc_type or "",
                "connector_id": str(doc.connector_id) if doc.connector_id else "",
            }
        )

    # Deduplicate sources by document (keep best similarity)
    seen_docs = {}
    for s in sources:
        did = s["document_id"]
        if did not in seen_docs or s["similarity"] > seen_docs[did]["similarity"]:
            seen_docs[did] = s
    sources = list(seen_docs.values())

    context_str = "\n\n---\n\n".join(context_parts) if context_parts else "Aucun document trouvé."
    return context_str, sources


def _generate_answer(
    question,
    context_str,
    sources,
    concept_context,
    history,
    llm,
    system_prompt_template=None,
):
    """Build messages and call LLM to generate the final answer.

    Returns dict with "answer", "sources", "suggestions".
    """
    full_context = context_str
    if concept_context:
        full_context = concept_context + "\n\n" + context_str
    template = system_prompt_template or get_prompt("CHAT_QA_SYSTEM")
    system_prompt = template.format(context=full_context)
    messages = [{"role": "system", "content": system_prompt}]

    if history:
        for turn in history[-MAX_HISTORY_TURNS:]:
            if turn.get("role") in ("user", "assistant") and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})

    messages.append({"role": "user", "content": question})

    response = llm.chat_messages(messages, temperature=0.3, max_tokens=2048)
    answer = response.content

    # Extract suggestions (lines starting with ">> ")
    suggestions = []
    answer_lines = []
    for line in answer.split("\n"):
        stripped = line.strip()
        if stripped.startswith(">> "):
            suggestions.append(stripped[3:])
        else:
            answer_lines.append(line)

    clean_answer = "\n".join(answer_lines).rstrip()

    return {
        "answer": clean_answer,
        "sources": sources,
        "suggestions": suggestions[:3],
    }


# ---------------------------------------------------------------------------
# Sub-pipeline: runs Phases 1-3 for a single question
# ---------------------------------------------------------------------------


def _retrieval_pipeline(question, tid, pid, tools, llm, vec_store):
    """Run query expansion, retrieval, and post-retrieval for a single question.

    Returns dict with "context" and "sources".
    """
    # Phase 1: Query expansion (mutually exclusive)
    if "rag-fusion" in tools:
        results = rag_fusion(question, llm, vec_store, tid, pid, k=SEARCH_K)
    elif "hyde" in tools:
        results = hyde(question, llm, vec_store, tid, pid, k=SEARCH_K)
    else:
        qv = llm.embed_single(question)
        results = vec_store.search(query_vector=qv, tenant_id=tid, k=SEARCH_K, project_id=pid)

    if not results:
        return {"context": "Aucun document trouvé.", "sources": []}

    # Load chunks and documents
    chunks_by_id, docs_by_id = _load_chunks_and_docs(results)

    # Phase 3: Post-retrieval (fixed order: crag → reranking → self-rag)
    if "crag" in tools:
        results = crag_evaluate(
            question, results, chunks_by_id, llm, vec_store, tid, pid, k=SEARCH_K
        )
        # Reload if CRAG re-retrieved new results
        chunks_by_id, docs_by_id = _load_chunks_and_docs(results)

    if "reranking" in tools:
        results = rerank_chunks(question, results, chunks_by_id, llm, top_k=SEARCH_K)

    if "self-rag" in tools:
        results = self_rag_filter(question, results, chunks_by_id, llm)

    context, sources = _build_context_and_sources(results, chunks_by_id, docs_by_id)
    return {"context": context, "sources": sources}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def ask_documents(
    question: str,
    tenant,
    project,
    history: list[dict] | None = None,
    tools: list[str] | None = None,
    system_prompt_template: str | None = None,
) -> dict:
    """
    RAG pipeline: embed → search → context → LLM → answer + sources + suggestions.

    Args:
        question: The user's question.
        tenant: Tenant object for data isolation.
        project: Project object for scoping the search.
        history: List of {"role": "user"|"assistant", "content": "..."} dicts.
        tools: List of active RAG technique IDs (e.g. ["rag-fusion", "reranking"]).
        system_prompt_template: Optional custom system prompt template with {context}.

    Returns:
        {"answer": str, "sources": [...], "suggestions": [...]}
    """
    tools = tools or []
    llm = get_llm_client()
    vec_store = get_vector_store()
    tid, pid = str(tenant.id), str(project.id)

    # Agentic RAG overrides everything
    if "agentic-rag" in tools:
        return agentic_rag(question, tenant, project, history, llm, vec_store)

    # Decomposition: split → sub-pipelines → synthesize
    if "decomposition" in tools:
        sub_qs = decompose_question(question, llm)
        if sub_qs:
            sub_tools = [t for t in tools if t != "decomposition"]
            sub_results = [
                _retrieval_pipeline(sq, tid, pid, sub_tools, llm, vec_store) for sq in sub_qs
            ]
            return synthesize_sub_results(question, sub_qs, sub_results, history, llm)
        # If decomposition returned nothing, fall through to standard pipeline

    # Standard pipeline with optional techniques
    pipeline_result = _retrieval_pipeline(question, tid, pid, tools, llm, vec_store)

    # Graph RAG context (independent, composable)
    concept_ctx = graph_rag_context(question, project) if "graph-rag" in tools else ""

    return _generate_answer(
        question,
        pipeline_result["context"],
        pipeline_result["sources"],
        concept_ctx,
        history,
        llm,
        system_prompt_template=system_prompt_template,
    )
