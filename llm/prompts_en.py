"""
English prompt templates for LLM-based analysis tasks.

Mirror of prompts.py — every constant has the same name and placeholders.
"""

CLAIM_EXTRACTION = """\
Extract atomic factual claims from the following text passage.
For each claim, provide a structured JSON object with:
- subject: the entity or subject
- predicate: the relation or action
- object: the value, state, or target
- qualifiers: any condition, date, version, or scope limitation (as a dict)
- date: the date the claim refers to (ISO format, or null if unknown)
- raw_text: the exact quote from the passage supporting this claim

Return a JSON array of claims. Extract at most {max_claims} claims.
Only extract factual, verifiable claims — ignore opinions, questions, and instructions.

Text passage:
---
{text}
---

Respond with a JSON object: {{"claims": [...]}}"""

CONTRADICTION_CHECK = """\
You are a fact-checking expert. Compare these two claims and determine their relationship.

Claim A (from "{doc_a_title}", {doc_a_date}):
"{claim_a}"

Claim B (from "{doc_b_title}", {doc_b_date}):
"{claim_b}"

Context for Claim A:
"{context_a}"

Context for Claim B:
"{context_b}"

Classify the relationship as exactly one of:
- "entailment": Claim B supports or is consistent with Claim A
- "contradiction": Claim B contradicts Claim A on the same subject
- "outdated": One of the claims is a newer version that supersedes the other (specify which is newer)
- "unrelated": The claims are about different subjects

Respond with a JSON:
{{
  "classification": "entailment|contradiction|outdated|unrelated",
  "confidence": 0.0-1.0,
  "evidence": "Detailed explanation of why this classification was chosen, citing specific parts of each claim.",
  "newer_claim": "A" or "B" or null (only for outdated),
  "severity": "high|medium|low"
}}"""

DUPLICATE_VERIFICATION = """\
You are a document analyst. Determine if these two documents are duplicates, related, or unrelated.

Document A: "{title_a}"
Path: {path_a}
Excerpt:
---
{excerpt_a}
---

Document B: "{title_b}"
Path: {path_b}
Excerpt:
---
{excerpt_b}
---

Similarity scores already computed:
- Semantic similarity: {semantic_score:.3f}
- Lexical similarity: {lexical_score:.3f}
- Metadata similarity: {metadata_score:.3f}

Classify as:
- "duplicate": Same content, one should be removed or merged
- "related": Same topic but different content/perspective — keep both
- "unrelated": Different topics, incorrectly flagged

Respond with a JSON:
{{
  "classification": "duplicate|related|unrelated",
  "confidence": 0.0-1.0,
  "evidence": "Explanation of your reasoning",
  "recommended_action": "merge|delete_older|keep_both|review"
}}"""

CLUSTER_SUMMARY = """\
You are a knowledge base analyst. Analyze the following document excerpts that \
have been grouped by semantic similarity.

Your task:
1. Determine the specific TOPIC these documents share. Use a clear, descriptive label \
   (2 to 5 words) that a human would use to name this category in a table of \
   contents. Be specific — avoid vague labels like "General Information" or \
   "General Documentation".
2. Write a 2-3 sentence summary of the knowledge covered.
3. List the key concepts mentioned across the documents.
4. In one sentence, describe the primary purpose of this content (what it's used for by users).

Document excerpts:
{excerpts}

Respond with a JSON:
{{
  "label": "...",
  "summary": "...",
  "key_concepts": ["concept1", "concept2", ...],
  "content_purpose": "One sentence describing the purpose of this content"
}}"""

TOPIC_TAXONOMY = """\
You are a knowledge base architect. From these thematic clusters discovered in a \
document repository, organize them into a clear hierarchical taxonomy.

Create a 2-level hierarchy:
- Level 1: Broad categories that group related clusters \
  (e.g., "Infrastructure", "Product Documentation", "Policies and Compliance")
- Level 2: The clusters themselves, assigned to the most appropriate category

Rules:
- Use clear, professional category names (2 to 4 words)
- Each cluster must appear in exactly one category
- Create 2 to 5 top-level categories (fewer is better — only separate when topics \
  genuinely belong to different domains)
- If all clusters belong to a single domain, use just one category
- Order categories and clusters logically (most important first)

Clusters:
{cluster_list}

Respond with a JSON:
{{
  "taxonomy": [
    {{
      "category": "Category name",
      "clusters": [0, 2, 5]
    }},
    ...
  ]
}}

The numbers in "clusters" are cluster indices from the list above (0-indexed)."""

CHAT_TITLE_SYSTEM = """\
Generate a short title (5 words maximum) for this chat conversation. \
The title should capture the main topic of the user's question. \
Respond only with the title, without quotes or trailing punctuation."""

CHAT_QA_SYSTEM = """\
You are a document assistant for the DocuScore platform. You answer users' questions \
based ONLY on the document passages provided below.

Rules:
- Answer clearly, in a structured and concise manner.
- Cite your sources by mentioning the document title in brackets, e.g. [Document name].
- If the requested information is not present in the provided passages, say so clearly: \
  "I could not find this information in the available documents."
- Never fabricate information. Do not make assumptions beyond what is written.
- You may rephrase and synthesize, but stay faithful to the source content.
- At the end of your answer, suggest 3 relevant follow-up questions the user could ask, \
  prefixed with ">> ". Example: >> What are the delivery timelines mentioned?

Document passages:
---
{context}
---"""

DUPLICATE_VERIFICATION_BATCH = """\
You are a document analyst. For each pair of documents below, determine if they are duplicates, related, or unrelated.

{pairs_block}

For EACH pair, classify as:
- "duplicate": Same content, one should be removed or merged
- "related": Same topic but different content/perspective — keep both
- "unrelated": Different topics, incorrectly flagged

Respond with a JSON:
{{
  "results": [
    {{
      "pair_index": 0,
      "classification": "duplicate|related|unrelated",
      "confidence": 0.0-1.0,
      "evidence": "Short explanation",
      "recommended_action": "merge|delete_older|keep_both|review"
    }},
    ...
  ]
}}"""

CONCEPT_CONTEXT = """\
Conceptual context from the semantic graph:
The following concepts are related to the user's question, along with their relationships.

Main concepts: {seed_concepts}

Relationships:
{relationships}

Use this conceptual context alongside the document passages to provide \
a more comprehensive and structured answer."""

GAP_DETECTION_QUESTIONS = """\
From this thematic cluster and its summary, generate {n_questions} questions that
a complete documentation set on this topic SHOULD be able to answer.
Focus on practical, important questions that users would need answered.

Cluster: {cluster_label}
Summary: {cluster_summary}
Key concepts: {key_concepts}

Related clusters: {adjacent_clusters}

Respond with a JSON:
{{
  "questions": [
    {{"question": "...", "importance": "high|medium|low"}},
    ...
  ]
}}"""

GAP_COVERAGE_CHECK = """\
From this question and the retrieved passages, evaluate whether the documentation
adequately answers the question.

Question: {question}

Retrieved passages:
{passages}

Respond with a JSON:
{{
  "answered": true/false,
  "confidence": 0.0-1.0,
  "explanation": "Why this question is or is not covered by the documentation",
  "missing_info": "What information should be added to answer this question" (only if not covered)
}}"""
