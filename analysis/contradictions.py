"""
Contradiction and outdated information detection.

Algorithm:
  1. For each claim, find related claims via vector similarity search
  2. Filter to claims on the same topic (high similarity, different documents)
  3. Use LLM to classify: entailment / contradiction / outdated / unrelated
  4. Apply authority rules: newer + more authoritative source wins for "outdated"
  5. Score severity based on confidence, topic importance, and age

The system avoids comparing claims within the same document (they're typically consistent).
"""
import json
import logging
from datetime import timedelta

import numpy as np
from django.conf import settings
from django.utils import timezone

from analysis.models import Claim, ContradictionPair
from ingestion.models import Document
from llm.client import get_llm_client
from llm.prompt_loader import get_prompt
from vectorstore.store import get_vector_store

logger = logging.getLogger(__name__)


class ContradictionDetector:
    """Detect contradictions and outdated claims across documents."""

    def __init__(self, tenant, analysis_job, project, on_progress=None, config=None):
        self.tenant = tenant
        self.job = analysis_job
        self.project = project
        self.on_progress = on_progress
        self.llm = get_llm_client()
        self.vec_store = get_vector_store()
        self.config = config if config is not None else settings.ANALYSIS_CONFIG.get("contradiction", {})
        self.confidence_threshold = self.config.get("confidence_threshold", 0.75)
        self.similarity_threshold = self.config.get("similarity_threshold", 0.70)
        self.max_neighbors = self.config.get("max_neighbors", 10)
        self.staleness_days = self.config.get("staleness_days", 180)
        self.authority_rules = settings.AUTHORITY_RULES

    def run(self) -> list[ContradictionPair]:
        """Run contradiction detection across all claims in the tenant."""
        logger.info("Starting contradiction detection for tenant=%s", self.tenant.slug)

        claims = list(
            Claim.objects.filter(project=self.project, has_embedding=True)
            .select_related("document", "chunk")
        )

        if len(claims) < 2:
            return []

        # Preload ALL claim embeddings in one batch query
        claim_embeddings = self.vec_store.get_all_claim_embeddings_for_tenant(
            str(self.tenant.id), project_id=str(self.project.id)
        )
        claims_by_id = {str(c.id): c for c in claims}

        # Phase 1: In-memory cosine similarity to find candidate pairs
        # Build matrix of all claim embeddings for a single matmul
        embedded_claims = [c for c in claims if str(c.id) in claim_embeddings]
        logger.info("Contradiction detection: %d claims (%d with embeddings)",
                     len(claims), len(embedded_claims))

        if len(embedded_claims) < 2:
            return []

        logger.info("[contradictions] Step 1/2: Building similarity matrix for %d claims...", len(embedded_claims))
        claim_ids_ordered = [str(c.id) for c in embedded_claims]
        matrix = np.stack([claim_embeddings[cid] for cid in claim_ids_ordered])

        # Normalize rows for cosine similarity via dot product
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms > 0, norms, 1.0)
        matrix_normed = matrix / norms

        # Full cosine similarity matrix (N×N) — single matmul
        sim_matrix = matrix_normed @ matrix_normed.T

        # Build document_id lookup for cross-doc filtering
        doc_ids = [embedded_claims[i].document_id for i in range(len(embedded_claims))]

        pairs_to_verify: list[tuple[Claim, Claim]] = []
        checked_pairs: set[tuple[str, str]] = set()

        logger.info("[contradictions] Scanning %d claims for candidate pairs (max_neighbors=%d, threshold=%.2f)...",
                     len(embedded_claims), self.max_neighbors, self.similarity_threshold)
        for i in range(len(embedded_claims)):
            if i % 200 == 0 and i > 0:
                logger.info("[contradictions] Scanned %d/%d claims, %d candidate pairs so far",
                             i, len(embedded_claims), len(pairs_to_verify))
            # Get top-k neighbors for claim i (excluding self)
            sims = sim_matrix[i]
            # Mask self
            sims[i] = -1.0
            top_indices = np.argpartition(sims, -self.max_neighbors)[-self.max_neighbors:]
            top_indices = top_indices[np.argsort(sims[top_indices])[::-1]]

            for j in top_indices:
                similarity = float(sims[j])
                if similarity < self.similarity_threshold:
                    continue
                # Skip same-document claims
                if doc_ids[i] == doc_ids[j]:
                    continue

                pair_key = tuple(sorted([claim_ids_ordered[i], claim_ids_ordered[j]]))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)

                pairs_to_verify.append((embedded_claims[i], embedded_claims[j]))

        logger.info("[contradictions] Step 1/2 done: %d candidate pairs found", len(pairs_to_verify))

        if not pairs_to_verify:
            return []

        # Phase 2: Concurrent LLM classification for all candidate pairs
        logger.info("[contradictions] Step 2/2: Classifying %d pairs via LLM...", len(pairs_to_verify))
        prompts = [self._build_classify_prompt(a, b) for a, b in pairs_to_verify]
        responses = self.llm.chat_batch_or_concurrent(prompts, json_mode=True, on_progress=self.on_progress)

        logger.info("[contradictions] Step 2/2: LLM responses received, parsing %d results...", len(responses))
        results = []
        for (claim_a, claim_b), resp in zip(pairs_to_verify, responses):
            if not resp:
                continue
            try:
                result = json.loads(resp.content)
            except (json.JSONDecodeError, AttributeError):
                continue

            classification = result.get("classification", "unrelated")
            confidence = result.get("confidence", 0.0)

            if classification in ("contradiction", "outdated") and confidence >= self.confidence_threshold:
                contradiction = self._create_contradiction(claim_a, claim_b, result)
                results.append(contradiction)

        logger.info("Contradiction detection found %d issues", len(results))
        return results

    def _build_classify_prompt(self, claim_a: Claim, claim_b: Claim) -> str:
        """Build the LLM classification prompt for a claim pair."""
        doc_a = claim_a.document
        doc_b = claim_b.document
        date_a = str(doc_a.source_modified_at or doc_a.created_at)[:10]
        date_b = str(doc_b.source_modified_at or doc_b.created_at)[:10]

        return get_prompt("CONTRADICTION_CHECK").format(
            doc_a_title=doc_a.title,
            doc_a_date=date_a,
            claim_a=claim_a.as_text,
            context_a=claim_a.raw_text[:500],
            doc_b_title=doc_b.title,
            doc_b_date=date_b,
            claim_b=claim_b.as_text,
            context_b=claim_b.raw_text[:500],
        )

    def _create_contradiction(
        self, claim_a: Claim, claim_b: Claim, result: dict
    ) -> ContradictionPair:
        """Create and persist a ContradictionPair record."""
        classification = result["classification"]
        severity = result.get("severity", "medium")
        confidence = result.get("confidence", 0.0)
        evidence = result.get("evidence", "")

        # Determine authoritative claim for outdated items
        authoritative_claim = None
        if classification == "outdated":
            authoritative_claim = self._determine_authority(
                claim_a, claim_b, result.get("newer_claim")
            )

        # Adjust severity based on staleness
        if classification == "outdated":
            severity = self._adjust_severity_for_staleness(claim_a, claim_b, severity)

        return ContradictionPair.objects.create(
            tenant=self.tenant,
            project=self.project,
            analysis_job=self.job,
            claim_a=claim_a,
            claim_b=claim_b,
            classification=classification,
            severity=severity,
            confidence=confidence,
            evidence=evidence,
            authoritative_claim=authoritative_claim,
        )

    def _determine_authority(
        self, claim_a: Claim, claim_b: Claim, newer_label: str | None
    ) -> Claim | None:
        """Determine which claim is authoritative based on recency and source weight."""
        source_weights = self.authority_rules.get("source_weights", {})
        recency_bias = self.authority_rules.get("recency_bias", True)

        doc_a = claim_a.document
        doc_b = claim_b.document

        # Source authority
        weight_a = source_weights.get(doc_a.connector.connector_type, 0.5) if doc_a.connector else 0.5
        weight_b = source_weights.get(doc_b.connector.connector_type, 0.5) if doc_b.connector else 0.5

        # Recency
        date_a = doc_a.source_modified_at or doc_a.created_at
        date_b = doc_b.source_modified_at or doc_b.created_at

        if newer_label == "B":
            return claim_b
        elif newer_label == "A":
            return claim_a

        # Fallback: prefer newer + higher authority
        if recency_bias and date_a and date_b:
            if date_b > date_a and weight_b >= weight_a:
                return claim_b
            elif date_a > date_b and weight_a >= weight_b:
                return claim_a

        return None

    def _adjust_severity_for_staleness(
        self, claim_a: Claim, claim_b: Claim, current_severity: str
    ) -> str:
        """Increase severity if the outdated document is very old."""
        now = timezone.now()
        stale_threshold = now - timedelta(days=self.staleness_days)

        for claim in (claim_a, claim_b):
            doc_date = claim.document.source_modified_at or claim.document.created_at
            if doc_date and doc_date < stale_threshold:
                if current_severity == "low":
                    return "medium"
                elif current_severity == "medium":
                    return "high"
        return current_severity
