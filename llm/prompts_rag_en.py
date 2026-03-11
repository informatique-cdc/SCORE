"""
English prompt templates for RAG technique functions.

Mirror of prompts_rag.py — every constant has the same name and placeholders.
"""

RAG_FUSION_VARIANTS = """\
You are a query reformulation assistant. From the user's question, \
generate {n_variants} semantically different variants that target the same information.

Original question: {question}

Respond with a JSON:
{{"queries": ["variant 1", "variant 2", ...]}}"""

HYDE_HYPOTHETICAL = """\
You are a document expert. From the user's question, write a short \
passage (3 to 5 sentences) that could be extracted from a document answering this question. \
This passage should be plausible, factual, and written in the style of technical documentation.

Question: {question}

Respond only with the hypothetical passage, without any preamble."""

DECOMPOSITION_SUBQUESTIONS = """\
You are a question analysis assistant. Decompose the following complex question \
into {max_sub} simpler, self-contained sub-questions. Each sub-question should \
target a specific aspect of the original question.

If the question is already simple and does not require decomposition, return it as-is.

Question: {question}

Respond with a JSON:
{{"sub_questions": ["sub-question 1", "sub-question 2", ...]}}"""

DECOMPOSITION_SYNTHESIS = """\
You are a document assistant. The user asked a complex question that was \
decomposed into sub-questions. Here are the results for each sub-question.

Original question: {question}

{sub_results_block}

Synthesize a coherent and comprehensive answer to the original question by combining \
the information from all sub-answers. Cite sources in brackets [Document name].

At the end of your answer, suggest 3 relevant follow-up questions prefixed with ">> ".

Respond directly with the synthesis."""

RERANK_SCORE = """\
You are a document relevance judge. Evaluate the relevance of each passage \
relative to the user's question. Assign a score from 0 to 10.

Question: {question}

Passages:
{chunks_block}

For each passage, assign a relevance score (0 = not relevant, 10 = perfectly relevant) \
and briefly explain your reasoning.

Respond with a JSON:
{{"scores": [{{"chunk_index": 0, "score": 8, "reason": "..."}}, ...]}}"""

CRAG_EVALUATE = """\
You are a document retrieval quality evaluator. Evaluate whether the retrieved passages \
are sufficient to answer the user's question.

Question: {question}

Retrieved passages:
{chunks_block}

Evaluate the overall quality of the results:
- "good": The passages contain the necessary information to answer the question.
- "poor": The passages are off-topic or insufficient.

If the quality is "poor", suggest a reformulation of the question that could yield \
better results.

Respond with a JSON:
{{"quality": "good|poor", "confidence": 0.0-1.0, "suggested_reformulation": "..." or null}}"""

SELF_RAG_FILTER = """\
You are a document relevance judge. For each passage below, \
determine whether it is relevant to answering the user's question.

Question: {question}

Passages:
{chunks_block}

For each passage, indicate whether it is relevant (true) or not (false).

Respond with a JSON:
{{"judgments": [{{"chunk_index": 0, "relevant": true}}, ...]}}"""

AGENTIC_PLAN = """\
You are an autonomous document research agent. You must answer the user's question \
by performing iterative searches in the document base.

Question: {question}

{scratchpad}

At each step, choose ONE action from:
- {{"action": "search", "query": "your search query"}} — semantic search in documents
- {{"action": "search_graph", "query": "your query"}} — search in the concept graph
- {{"action": "answer", "content": "your final answer"}} — answer the question

Rules:
- Maximum {max_steps} search steps.
- Formulate precise and varied search queries.
- When you have enough information, use the "answer" action.
- In your final answer, cite sources [Document name] and add 3 suggestions ">> ".

Respond with a JSON containing ONE single action."""
