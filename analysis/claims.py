"""
Claims extraction from document chunks.

Extracts atomic factual claims in structured form (subject, predicate, object)
for contradiction/outdated detection. Uses LLM with structured JSON output.
"""
import json
import logging
from datetime import date

from django.conf import settings

from analysis.models import Claim
from ingestion.models import Document, DocumentChunk
from llm.client import get_llm_client
from llm.prompt_loader import get_prompt
from vectorstore.store import get_vector_store

logger = logging.getLogger(__name__)


class ClaimsExtractor:
    """Extract structured claims from document chunks using LLM."""

    def __init__(self, tenant, project, on_progress=None, config=None):
        self.tenant = tenant
        self.project = project
        self.on_progress = on_progress
        self.llm = get_llm_client()
        self.vec_store = get_vector_store()
        self.config = config if config is not None else settings.ANALYSIS_CONFIG.get("contradiction", {})
        self.max_claims_per_chunk = self.config.get("max_claims_per_chunk", 5)

    def extract_all(self) -> int:
        """Extract claims from all ready documents that haven't been processed yet.

        Batches all chunks across all documents into a single LLM call for maximum
        concurrency, then dispatches results back per-document.
        """
        docs = list(Document.objects.filter(project=self.project, status=Document.Status.READY))

        # Filter to docs without existing claims
        doc_ids_with_claims = set(
            Claim.objects.filter(project=self.project)
            .values_list("document_id", flat=True)
            .distinct()
        )
        docs_to_process = [d for d in docs if d.id not in doc_ids_with_claims]

        if not docs_to_process:
            logger.info("[claims] All documents already have claims extracted")
            return 0

        logger.info("[claims] %d documents to process (%d already done)",
                     len(docs_to_process), len(docs) - len(docs_to_process))

        # Collect all chunks across all documents in one query
        all_chunks = list(
            DocumentChunk.objects.filter(document__in=docs_to_process)
            .select_related("document")
            .order_by("document_id", "chunk_index")
        )

        if not all_chunks:
            return 0

        # Build prompts for ALL chunks at once
        prompts = [
            get_prompt("CLAIM_EXTRACTION").format(
                text=chunk.content[:3000],
                max_claims=self.max_claims_per_chunk,
            )
            for chunk in all_chunks
        ]

        # Single batched LLM call across all documents
        logger.info("[claims] Step 1/3: Extracting claims from %d chunks across %d documents (LLM batch)...",
                     len(all_chunks), len(docs_to_process))
        responses = self.llm.chat_batch_or_concurrent(prompts, json_mode=True, on_progress=self.on_progress)
        logger.info("[claims] Step 1/3 done: %d responses received", len(responses))

        # Parse responses and create Claim objects
        logger.info("[claims] Step 2/3: Parsing responses...")
        all_claims: list[Claim] = []
        for chunk, resp in zip(all_chunks, responses):
            if not resp:
                continue
            try:
                data = json.loads(resp.content)
                raw_claims = data.get("claims", [])
            except (json.JSONDecodeError, AttributeError):
                continue

            for rc in raw_claims:
                claim_date = None
                if rc.get("date"):
                    try:
                        claim_date = date.fromisoformat(rc["date"])
                    except (ValueError, TypeError):
                        pass

                all_claims.append(Claim(
                    tenant=self.tenant,
                    project=self.project,
                    document=chunk.document,
                    chunk=chunk,
                    subject=str(rc.get("subject") or "")[:500],
                    predicate=str(rc.get("predicate") or "")[:500],
                    object_value=str(rc.get("object") or "")[:1000],
                    qualifiers=rc.get("qualifiers") or {},
                    claim_date=claim_date,
                    raw_text=str(rc.get("raw_text") or "")[:2000],
                ))

        if all_claims:
            logger.info("[claims] Step 2/3 done: %d claims parsed, saving to DB...", len(all_claims))
            Claim.objects.bulk_create(all_claims)
            logger.info("[claims] Step 3/3: Embedding %d claims...", len(all_claims))
            self._embed_claims(all_claims)
            logger.info("[claims] Step 3/3 done: embeddings stored")

        logger.info("[claims] Complete: %d claims across %d documents", len(all_claims), len(docs_to_process))
        return len(all_claims)

    def _embed_claims(self, claims: list[Claim]):
        """Generate embeddings for claims and store in sqlite-vec (batched)."""
        if not claims:
            return

        texts = [c.as_text for c in claims]
        embeddings = self.llm.embed(texts, on_progress=self.on_progress)

        # Batch upsert to vector store
        items = [
            (
                str(claim.id),
                str(self.tenant.id),
                str(claim.document_id),
                str(claim.chunk_id),
                embedding,
            )
            for claim, embedding in zip(claims, embeddings)
        ]
        self.vec_store.upsert_claims_batch(items, project_id=str(self.project.id))

        # Batch-update has_embedding flag
        claim_ids = [c.id for c in claims]
        Claim.objects.filter(id__in=claim_ids).update(has_embedding=True)
