"""
RAG technique implementations for the chat pipeline.

Each function corresponds to one of the 8 RAG techniques available
in the Outils dropdown. They are composed by the orchestrator in rag.py.
"""
import json
import logging

from llm.prompt_loader import get_prompt

logger = logging.getLogger(__name__)

_FENCE_RE = None  # lazy-compiled regex for markdown fences


def _parse_json(raw: str) -> dict:
    """Parse JSON from an LLM response, stripping markdown fences if present."""
    raw = raw.strip()
    if not raw:
        raise ValueError("empty LLM response")
    # Strip ```json ... ``` wrappers
    if raw.startswith("```"):
        global _FENCE_RE
        if _FENCE_RE is None:
            import re
            _FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)```\s*$", re.DOTALL)
        m = _FENCE_RE.match(raw)
        if m:
            raw = m.group(1).strip()
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _reciprocal_rank_fusion(result_lists: list[list[dict]], k_rrf: int = 60) -> list[dict]:
    """Merge multiple ranked result lists using Reciprocal Rank Fusion.

    score(doc) = sum(1 / (k_rrf + rank_i)) across all lists where doc appears.
    Returns deduplicated results sorted by RRF score descending.
    """
    scores: dict[str, float] = {}
    doc_map: dict[str, dict] = {}

    for results in result_lists:
        for rank, r in enumerate(results):
            cid = r["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k_rrf + rank)
            # Keep the result dict with the best original similarity
            if cid not in doc_map or r.get("similarity", 0) > doc_map[cid].get("similarity", 0):
                doc_map[cid] = r

    sorted_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [doc_map[cid] for cid in sorted_ids]


def _format_chunks_block(results: list[dict], chunks_by_id: dict) -> str:
    """Format chunks for LLM prompts with indexed passages."""
    parts = []
    for i, r in enumerate(results):
        chunk = chunks_by_id.get(r["chunk_id"])
        if not chunk:
            continue
        parts.append(f"[Passage {i}]\n{chunk.content}")
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Phase 1: Pre-retrieval — Query Expansion
# ---------------------------------------------------------------------------

def rag_fusion(question, llm, vec_store, tenant_id, project_id, k=5, n_variants=3):
    """RAG Fusion: generate query variants, search each, merge via RRF.

    Cost: 1 LLM + 1 embed batch + N vector searches (via search_batch).
    """
    # Generate variant queries
    prompt = get_prompt("RAG_FUSION_VARIANTS").format(question=question, n_variants=n_variants)
    try:
        resp = llm.chat(prompt, json_mode=True, temperature=0.5, max_tokens=512)
        data = _parse_json(resp.content)
        variants = data.get("queries", [])[:n_variants]
    except (json.JSONDecodeError, KeyError, ValueError):
        logger.warning("RAG Fusion: failed to parse variant queries, falling back to original")
        variants = []

    # All queries: original + variants
    all_queries = [question] + variants
    embeddings = llm.embed(all_queries)

    # Batch vector search
    result_lists = vec_store.search_batch(
        query_vectors=embeddings,
        tenant_id=tenant_id,
        k=k,
        project_id=project_id,
    )

    # Merge via RRF
    merged = _reciprocal_rank_fusion(result_lists)
    return merged[:k]


def hyde(question, llm, vec_store, tenant_id, project_id, k=5):
    """HyDE: generate a hypothetical document, embed it, search.

    Cost: 1 LLM + 1 embed.
    """
    prompt = get_prompt("HYDE_HYPOTHETICAL").format(question=question)
    try:
        resp = llm.chat(prompt, temperature=0.5, max_tokens=512)
        hypothetical = resp.content.strip()
        if not hypothetical:
            raise ValueError("empty response")
    except (ConnectionError, TimeoutError, OSError, ValueError):
        logger.warning("HyDE: failed to generate hypothetical passage, falling back", exc_info=True)
        hypothetical = question

    hyde_vector = llm.embed_single(hypothetical)
    return vec_store.search(
        query_vector=hyde_vector,
        tenant_id=tenant_id,
        k=k,
        project_id=project_id,
    )


def decompose_question(question, llm, max_sub=4):
    """Decompose a complex question into 2-4 sub-questions.

    Cost: 1 LLM.
    Returns list of sub-question strings.
    """
    prompt = get_prompt("DECOMPOSITION_SUBQUESTIONS").format(question=question, max_sub=max_sub)
    try:
        resp = llm.chat(prompt, json_mode=True, temperature=0.3, max_tokens=512)
        data = _parse_json(resp.content)
        sub_qs = data.get("sub_questions", [])[:max_sub]
    except (json.JSONDecodeError, KeyError, ValueError):
        logger.warning("Decomposition: failed to parse sub-questions")
        sub_qs = []

    # If decomposition returned 0 or 1 sub-question identical to original, skip
    if len(sub_qs) <= 1:
        return []
    return sub_qs


def synthesize_sub_results(question, sub_questions, sub_results, history, llm):
    """Combine sub-question results into a coherent final answer.

    Cost: 1 LLM.
    Args:
        sub_results: list of dicts with keys "context" and "sources".
    Returns:
        dict with "answer", "sources", "suggestions".
    """
    # Build the sub-results block for the prompt
    parts = []
    all_sources = []
    for i, (sq, sr) in enumerate(zip(sub_questions, sub_results)):
        parts.append(f"### Sous-question {i + 1} : {sq}\nContexte trouvé :\n{sr['context']}")
        all_sources.extend(sr.get("sources", []))

    sub_results_block = "\n\n".join(parts)
    prompt = get_prompt("DECOMPOSITION_SYNTHESIS").format(
        question=question,
        sub_results_block=sub_results_block,
    )

    # Build messages with history
    messages = [{"role": "system", "content": prompt}]
    if history:
        for turn in history[-10:]:
            if turn.get("role") in ("user", "assistant") and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    resp = llm.chat_messages(messages, temperature=0.3, max_tokens=2048)
    answer = resp.content

    # Extract suggestions
    suggestions = []
    answer_lines = []
    for line in answer.split("\n"):
        stripped = line.strip()
        if stripped.startswith(">> "):
            suggestions.append(stripped[3:])
        else:
            answer_lines.append(line)

    # Deduplicate sources
    seen_docs = {}
    for s in all_sources:
        did = s["document_id"]
        if did not in seen_docs or s.get("similarity", 0) > seen_docs[did].get("similarity", 0):
            seen_docs[did] = s

    return {
        "answer": "\n".join(answer_lines).rstrip(),
        "sources": list(seen_docs.values()),
        "suggestions": suggestions[:3],
    }


# ---------------------------------------------------------------------------
# Phase 2: Retrieval Enhancement
# ---------------------------------------------------------------------------

def graph_rag_context(question, project):
    """Extract concept context from the semantic graph.

    Cost: 0 extra LLM (NSG handles embedding internally).
    Returns a formatted context string, or "" if unavailable.
    """
    try:
        from analysis.semantic_graph import load_graph

        nsg = load_graph(str(project.id))
        if not nsg or nsg.graph.number_of_nodes() == 0:
            return ""

        subgraph = nsg.query_subgraph(question, top_k=5, hops=1, max_nodes=20)
        seeds = subgraph.get("seeds", [])
        edges = subgraph.get("edges", [])
        if not seeds:
            return ""

        seed_labels = [s["concept"] for s in seeds[:5]]
        rel_lines = []
        for e in edges[:15]:
            rel_lines.append(f"  {e['source']} —[{e['relation_type']}]→ {e['target']}")

        return get_prompt("CONCEPT_CONTEXT").format(
            seed_concepts=", ".join(seed_labels),
            relationships="\n".join(rel_lines) if rel_lines else "(aucune relation directe)",
        )
    except (ImportError, FileNotFoundError, AttributeError, ValueError):
        logger.debug("Graph RAG unavailable for project %s", project.id, exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# Phase 3: Post-retrieval
# ---------------------------------------------------------------------------

def rerank_chunks(question, results, chunks_by_id, llm, top_k=5):
    """LLM-based re-ranking: score each chunk's relevance 0-10.

    Cost: 1 LLM.
    """
    if not results:
        return results

    chunks_block = _format_chunks_block(results, chunks_by_id)
    if not chunks_block:
        return results

    prompt = get_prompt("RERANK_SCORE").format(question=question, chunks_block=chunks_block)
    try:
        resp = llm.chat(prompt, json_mode=True, temperature=0.0, max_tokens=1024)
        data = _parse_json(resp.content)
        scores = {s["chunk_index"]: s["score"] for s in data.get("scores", [])}
    except (json.JSONDecodeError, KeyError, ValueError):
        logger.warning("Re-ranking: failed to parse scores, keeping original order")
        return results

    # Sort by LLM score descending
    scored = []
    for i, r in enumerate(results):
        score = scores.get(i, 0)
        scored.append((score, i, r))
    scored.sort(key=lambda x: x[0], reverse=True)

    return [r for _, _, r in scored[:top_k]]


def crag_evaluate(question, results, chunks_by_id, llm, vec_store, tenant_id, project_id, k=5):
    """Corrective RAG: evaluate retrieval quality, re-search if poor.

    Cost: 1 LLM + conditionally (1 embed + 1 search).
    """
    if not results:
        return results

    chunks_block = _format_chunks_block(results, chunks_by_id)
    if not chunks_block:
        return results

    prompt = get_prompt("CRAG_EVALUATE").format(question=question, chunks_block=chunks_block)
    try:
        resp = llm.chat(prompt, json_mode=True, temperature=0.0, max_tokens=512)
        data = _parse_json(resp.content)
        quality = data.get("quality", "good")
        reformulation = data.get("suggested_reformulation")
    except (json.JSONDecodeError, KeyError, ValueError):
        logger.warning("CRAG: failed to parse evaluation, keeping original results")
        return results

    if quality == "good" or not reformulation:
        return results

    # Re-search with reformulated query
    logger.info("CRAG: quality=poor, re-searching with: %s", reformulation)
    new_vector = llm.embed_single(reformulation)
    new_results = vec_store.search(
        query_vector=new_vector,
        tenant_id=tenant_id,
        k=k,
        project_id=project_id,
    )
    return new_results if new_results else results


def self_rag_filter(question, results, chunks_by_id, llm):
    """Self-RAG: filter out irrelevant chunks via LLM judgment.

    Cost: 1 LLM.
    """
    if not results:
        return results

    chunks_block = _format_chunks_block(results, chunks_by_id)
    if not chunks_block:
        return results

    prompt = get_prompt("SELF_RAG_FILTER").format(question=question, chunks_block=chunks_block)
    try:
        resp = llm.chat(prompt, json_mode=True, temperature=0.0, max_tokens=512)
        data = _parse_json(resp.content)
        judgments = {j["chunk_index"]: j["relevant"] for j in data.get("judgments", [])}
    except (json.JSONDecodeError, KeyError, ValueError):
        logger.warning("Self-RAG: failed to parse judgments, keeping all chunks")
        return results

    filtered = [r for i, r in enumerate(results) if judgments.get(i, True)]

    if not filtered:
        logger.warning("Self-RAG: all chunks filtered out, keeping originals")
        return results

    return filtered


# ---------------------------------------------------------------------------
# Phase 4: Generation — Agentic RAG
# ---------------------------------------------------------------------------

def agentic_rag(question, tenant, project, history, llm, vec_store, max_steps=3):
    """Agentic RAG: autonomous search/evaluate/answer loop.

    Cost: 1-3 LLM per step x max_steps.
    Returns dict with "answer", "sources", "suggestions".
    """
    from ingestion.models import Document, DocumentChunk

    tid, pid = str(tenant.id), str(project.id)
    scratchpad_entries = []
    all_chunk_ids = set()
    all_doc_ids = set()

    for step in range(max_steps):
        # Build scratchpad text
        if scratchpad_entries:
            scratchpad_text = "Informations collectées :\n" + "\n\n".join(scratchpad_entries)
        else:
            scratchpad_text = "Aucune information collectée pour l'instant."

        prompt = get_prompt("AGENTIC_PLAN").format(
            question=question,
            scratchpad=scratchpad_text,
            max_steps=max_steps - step,
        )

        messages = [{"role": "system", "content": prompt}]
        if history:
            for turn in history[-10:]:
                if turn.get("role") in ("user", "assistant") and turn.get("content"):
                    messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": question})

        try:
            resp = llm.chat_messages(messages, temperature=0.3, max_tokens=2048, json_mode=True)
            action = _parse_json(resp.content)
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.warning("Agentic RAG step %d: failed to parse action", step, exc_info=True)
            break

        action_type = action.get("action")

        if action_type == "answer":
            answer_text = action.get("content", "")
            return _finalize_agentic(answer_text, all_chunk_ids, all_doc_ids)

        if action_type == "search":
            query = action.get("query", question)
            qv = llm.embed_single(query)
            results = vec_store.search(query_vector=qv, tenant_id=tid, k=5, project_id=pid)
            chunk_ids = [r["chunk_id"] for r in results]
            doc_ids = [r["document_id"] for r in results]
            all_chunk_ids.update(chunk_ids)
            all_doc_ids.update(doc_ids)

            chunks = DocumentChunk.objects.filter(id__in=chunk_ids)
            entry_parts = [f"Recherche : « {query} »\nRésultats :"]
            for c in chunks:
                entry_parts.append(f"- {c.content[:300]}")
            scratchpad_entries.append("\n".join(entry_parts))

        elif action_type == "search_graph":
            query = action.get("query", question)
            ctx = graph_rag_context(query, project)
            if ctx:
                scratchpad_entries.append(f"Graphe de concepts pour « {query} » :\n{ctx}")
            else:
                scratchpad_entries.append(f"Graphe de concepts pour « {query} » : aucun résultat.")

        else:
            logger.warning("Agentic RAG step %d: unknown action %s", step, action_type)
            break

    # If we reach max_steps without an answer, synthesize from scratchpad
    logger.info("Agentic RAG: max steps reached, synthesizing from scratchpad")
    scratchpad_text = "\n\n".join(scratchpad_entries) if scratchpad_entries else "Aucune information trouvée."
    fallback_prompt = (
        f"À partir des informations suivantes, réponds à la question : {question}\n\n"
        f"{scratchpad_text}\n\n"
        "Cite les sources [Nom du document] et ajoute 3 suggestions « >> »."
    )
    resp = llm.chat(fallback_prompt, temperature=0.3, max_tokens=2048)
    return _finalize_agentic(resp.content, all_chunk_ids, all_doc_ids)


def _finalize_agentic(answer_text, all_chunk_ids, all_doc_ids):
    """Build the final response dict for agentic RAG."""
    from ingestion.models import Document

    # Extract suggestions
    suggestions = []
    answer_lines = []
    for line in answer_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(">> "):
            suggestions.append(stripped[3:])
        else:
            answer_lines.append(line)

    # Build sources from collected doc IDs
    sources = []
    if all_doc_ids:
        docs = Document.objects.filter(id__in=list(all_doc_ids))
        for doc in docs:
            sources.append({
                "title": doc.title,
                "chunk": "",
                "similarity": 0.0,
                "document_id": str(doc.id),
                "source_url": doc.source_url or "",
                "doc_type": doc.doc_type or "",
                "connector_id": str(doc.connector_id) if doc.connector_id else "",
            })

    return {
        "answer": "\n".join(answer_lines).rstrip(),
        "sources": sources,
        "suggestions": suggestions[:3],
    }
