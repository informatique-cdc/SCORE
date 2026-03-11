"""
Duplicate detection via multi-signal similarity.

Algorithm:
  1. Semantic similarity: cosine similarity of document-level embeddings (avg of chunk embeddings)
  2. Lexical similarity: MinHash Jaccard similarity on token shingles
  3. Metadata similarity: normalized edit distance on title, path, author
  4. Combined score: weighted sum with configurable weights
  5. Two-tier verification:
     a. Pairs with combined >= llm_verify_threshold → LLM batch verification
     b. Pairs with combined between cross_encoder_threshold and llm_verify_threshold → auto-classified "review"
  6. LSH pre-filter replaces brute-force O(n²) candidate generation

Thresholds and weights are configurable in config.yaml / settings.
"""

import json
import logging
from difflib import SequenceMatcher

import numpy as np
from datasketch import MinHash, MinHashLSH

from django.conf import settings

from analysis.models import DuplicateGroup, DuplicatePair
from ingestion.models import Document, DocumentChunk
from llm.client import get_llm_client
from llm.prompt_loader import get_prompt
from vectorstore.store import get_vector_store

logger = logging.getLogger(__name__)


class DuplicateDetector:
    """Multi-signal duplicate detection engine."""

    def __init__(self, tenant, analysis_job, project, on_progress=None, config=None):
        self.tenant = tenant
        self.job = analysis_job
        self.project = project
        self.on_progress = on_progress
        self.config = (
            config if config is not None else settings.ANALYSIS_CONFIG.get("duplicate", {})
        )
        self.semantic_weight = self.config.get("semantic_weight", 0.55)
        self.lexical_weight = self.config.get("lexical_weight", 0.25)
        self.metadata_weight = self.config.get("metadata_weight", 0.20)
        self.semantic_threshold = self.config.get("semantic_threshold", 0.92)
        self.combined_threshold = self.config.get("combined_threshold", 0.80)
        self.cross_encoder_threshold = self.config.get("cross_encoder_threshold", 0.70)
        self.llm_verify_threshold = self.config.get("llm_verify_threshold", 0.85)
        self.llm_batch_size = self.config.get("llm_batch_size", 5)
        self.num_perm = self.config.get("minhash_num_perm", 128)
        self.lsh_threshold = self.config.get("lsh_threshold", 0.5)
        self.vec_store = get_vector_store()
        self.llm = get_llm_client()

    def run(self) -> list[DuplicateGroup]:
        """Execute duplicate detection across all ready documents in the tenant."""
        logger.info("Starting duplicate detection for tenant=%s", self.tenant.slug)

        docs = list(
            Document.objects.filter(project=self.project, status=Document.Status.READY).order_by(
                "title"
            )
        )
        if len(docs) < 2:
            return []

        logger.info("[duplicates] %d documents to process", len(docs))

        # Step 1: Compute document-level embeddings (average of chunk embeddings)
        logger.info("[duplicates] Step 1/6: Computing document embeddings...")
        doc_embeddings = self._compute_document_embeddings(docs)
        logger.info(
            "[duplicates] Step 1/6 done: %d document embeddings computed", len(doc_embeddings)
        )

        # Step 2: Build MinHash index + LSH for lexical candidate generation
        logger.info("[duplicates] Step 2/6: Computing MinHash signatures for %d docs...", len(docs))
        doc_minhashes = self._compute_minhashes(docs)
        logger.info("[duplicates] Step 2/6 done: %d MinHash signatures", len(doc_minhashes))

        # Step 3: Find candidate pairs using LSH + semantic similarity
        logger.info("[duplicates] Step 3/6: Finding candidate pairs (LSH + semantic)...")
        candidates = self._find_candidates_lsh(docs, doc_embeddings, doc_minhashes)
        logger.info("[duplicates] Step 3/6 done: %d candidate pairs found", len(candidates))

        # Step 4: Score all candidates with multi-signal similarity
        logger.info("[duplicates] Step 4/6: Scoring %d candidate pairs...", len(candidates))
        scored_pairs = []
        for doc_a, doc_b in candidates:
            scores = self._score_pair(doc_a, doc_b, doc_embeddings, doc_minhashes)
            if scores["combined"] >= self.cross_encoder_threshold:
                scored_pairs.append((doc_a, doc_b, scores))

        logger.info("[duplicates] Step 4/6 done: %d pairs above threshold", len(scored_pairs))

        # Step 5: Group duplicates (connected components)
        logger.info("[duplicates] Step 5/6: Grouping duplicates...")
        groups = self._group_duplicates(scored_pairs)
        logger.info("[duplicates] Step 5/6 done: %d groups formed", len(groups))

        # Step 6: Two-tier verification: LLM for high-confidence, auto-classify the rest
        total_pairs = sum(len(g) for g in groups)
        logger.info(
            "[duplicates] Step 6/6: Verifying %d groups (%d pairs) via LLM...",
            len(groups),
            total_pairs,
        )
        verified_groups = self._verify_groups(groups)

        logger.info("Duplicate detection found %d groups", len(verified_groups))
        return verified_groups

    def _compute_document_embeddings(self, docs: list[Document]) -> dict[str, np.ndarray]:
        """Compute document-level embedding as mean of chunk embeddings."""
        doc_embeddings = {}

        # Batch-load all chunk IDs grouped by document
        doc_chunk_map: dict[str, list[str]] = {}
        all_chunk_ids: list[str] = []
        for doc in docs:
            chunk_ids = list(
                DocumentChunk.objects.filter(document=doc, has_embedding=True).values_list(
                    "id", flat=True
                )
            )
            if chunk_ids:
                str_ids = [str(cid) for cid in chunk_ids]
                doc_chunk_map[str(doc.id)] = str_ids
                all_chunk_ids.extend(str_ids)

        # Single batch query for all embeddings
        embeddings_map = self.vec_store.get_chunk_embeddings_batch(all_chunk_ids)

        # Average per document
        for doc_id, chunk_ids in doc_chunk_map.items():
            vectors = [embeddings_map[cid] for cid in chunk_ids if cid in embeddings_map]
            if vectors:
                doc_embedding = np.mean(vectors, axis=0)
                norm = np.linalg.norm(doc_embedding)
                if norm > 0:
                    doc_embedding = doc_embedding / norm
                doc_embeddings[doc_id] = doc_embedding

        return doc_embeddings

    def _compute_minhashes(self, docs: list[Document]) -> dict[str, MinHash]:
        """Compute MinHash signatures for lexical similarity."""
        doc_minhashes = {}

        for mh_idx, doc in enumerate(docs):
            if mh_idx % 50 == 0:
                logger.info("[duplicates] MinHash: %d/%d documents", mh_idx, len(docs))
            chunks = DocumentChunk.objects.filter(document=doc)
            full_text = " ".join(c.content for c in chunks)

            mh = MinHash(num_perm=self.num_perm)
            # Use 3-word shingles
            words = full_text.lower().split()
            for i in range(len(words) - 2):
                shingle = " ".join(words[i : i + 3])
                mh.update(shingle.encode("utf-8"))

            doc_minhashes[str(doc.id)] = mh

        return doc_minhashes

    def _find_candidates_lsh(
        self,
        docs: list[Document],
        doc_embeddings: dict[str, np.ndarray],
        doc_minhashes: dict[str, MinHash],
    ) -> list[tuple[Document, Document]]:
        """Find candidate pairs using MinHash LSH + semantic similarity fallback."""
        doc_map = {str(d.id): d for d in docs}
        candidate_set: set[tuple[str, str]] = set()

        # --- LSH-based lexical candidates ---
        lsh = MinHashLSH(threshold=self.lsh_threshold, num_perm=self.num_perm)
        for doc_id, mh in doc_minhashes.items():
            lsh.insert(doc_id, mh)

        for doc_id, mh in doc_minhashes.items():
            neighbors = lsh.query(mh)
            for neighbor_id in neighbors:
                if neighbor_id != doc_id:
                    pair = tuple(sorted([doc_id, neighbor_id]))
                    candidate_set.add(pair)

        # --- Semantic candidates: only check docs with embeddings, using a tighter threshold ---
        embedded_ids = [str(d.id) for d in docs if str(d.id) in doc_embeddings]
        for i in range(len(embedded_ids)):
            for j in range(i + 1, len(embedded_ids)):
                id_a, id_b = embedded_ids[i], embedded_ids[j]
                sim = float(np.dot(doc_embeddings[id_a], doc_embeddings[id_b]))
                if sim >= self.semantic_threshold * 0.85:
                    pair = tuple(sorted([id_a, id_b]))
                    candidate_set.add(pair)

        # Convert to document pairs
        candidates = []
        for id_a, id_b in candidate_set:
            if id_a in doc_map and id_b in doc_map:
                candidates.append((doc_map[id_a], doc_map[id_b]))

        return candidates

    def _score_pair(
        self,
        doc_a: Document,
        doc_b: Document,
        doc_embeddings: dict[str, np.ndarray],
        doc_minhashes: dict[str, MinHash],
    ) -> dict:
        """Compute multi-signal similarity score for a document pair."""
        id_a, id_b = str(doc_a.id), str(doc_b.id)

        # Semantic similarity
        semantic = 0.0
        if id_a in doc_embeddings and id_b in doc_embeddings:
            semantic = float(np.dot(doc_embeddings[id_a], doc_embeddings[id_b]))

        # Lexical similarity (MinHash Jaccard)
        lexical = 0.0
        if id_a in doc_minhashes and id_b in doc_minhashes:
            lexical = doc_minhashes[id_a].jaccard(doc_minhashes[id_b])

        # Metadata similarity
        metadata = self._metadata_similarity(doc_a, doc_b)

        # Combined weighted score
        combined = (
            self.semantic_weight * semantic
            + self.lexical_weight * lexical
            + self.metadata_weight * metadata
        )

        return {
            "semantic": semantic,
            "lexical": lexical,
            "metadata": metadata,
            "combined": combined,
        }

    def _metadata_similarity(self, doc_a: Document, doc_b: Document) -> float:
        """Compute metadata similarity from title, path, and author."""
        scores = []

        # Title similarity
        if doc_a.title and doc_b.title:
            scores.append(SequenceMatcher(None, doc_a.title.lower(), doc_b.title.lower()).ratio())

        # Path similarity
        if doc_a.path and doc_b.path:
            scores.append(SequenceMatcher(None, doc_a.path.lower(), doc_b.path.lower()).ratio())

        # Author match (binary)
        if doc_a.author and doc_b.author:
            scores.append(1.0 if doc_a.author.lower() == doc_b.author.lower() else 0.0)

        return sum(scores) / len(scores) if scores else 0.0

    def _group_duplicates(
        self, scored_pairs: list[tuple[Document, Document, dict]]
    ) -> list[list[tuple[Document, Document, dict]]]:
        """Group duplicate pairs using connected components."""
        from collections import defaultdict

        if not scored_pairs:
            return []

        # Build adjacency list
        adj = defaultdict(set)
        pair_map = {}
        for doc_a, doc_b, scores in scored_pairs:
            adj[str(doc_a.id)].add(str(doc_b.id))
            adj[str(doc_b.id)].add(str(doc_a.id))
            pair_key = tuple(sorted([str(doc_a.id), str(doc_b.id)]))
            pair_map[pair_key] = (doc_a, doc_b, scores)

        # BFS connected components
        visited = set()
        groups = []

        for node in adj:
            if node in visited:
                continue
            component = set()
            queue = [node]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                queue.extend(adj[current] - visited)

            # Collect pairs in this component
            group_pairs = []
            component_list = sorted(component)
            for i in range(len(component_list)):
                for j in range(i + 1, len(component_list)):
                    key = (component_list[i], component_list[j])
                    if key in pair_map:
                        group_pairs.append(pair_map[key])
            if group_pairs:
                groups.append(group_pairs)

        return groups

    def _verify_groups(
        self, groups: list[list[tuple[Document, Document, dict]]]
    ) -> list[DuplicateGroup]:
        """Two-tier verification: LLM batch for high scores, auto-classify the rest."""
        result_groups = []

        for grp_idx, group_pairs in enumerate(groups):
            logger.info(
                "[duplicates] Verifying group %d/%d (%d pairs)",
                grp_idx + 1,
                len(groups),
                len(group_pairs),
            )
            db_group = DuplicateGroup.objects.create(
                tenant=self.tenant,
                project=self.project,
                analysis_job=self.job,
            )

            # Separate pairs into two tiers
            llm_pairs = []  # combined >= llm_verify_threshold → LLM verification
            auto_pairs = []  # cross_encoder_threshold <= combined < llm_verify_threshold → auto "review"

            for doc_a, doc_b, scores in group_pairs:
                evidence_a = self._get_evidence_chunks(doc_a, limit=2)
                evidence_b = self._get_evidence_chunks(doc_b, limit=2)
                entry = (doc_a, doc_b, scores, evidence_a, evidence_b)
                if scores["combined"] >= self.llm_verify_threshold:
                    llm_pairs.append(entry)
                else:
                    auto_pairs.append(entry)

            # --- LLM batch verification for high-score pairs ---
            llm_verifications = self._batch_verify_llm(llm_pairs)

            # --- Persist all pairs ---
            any_verified_dup = False

            for idx, (doc_a, doc_b, scores, evidence_a, evidence_b) in enumerate(llm_pairs):
                verification = llm_verifications.get(
                    idx,
                    {
                        "classification": "",
                        "confidence": 0.0,
                        "evidence": "",
                    },
                )
                if verification.get("classification") == "duplicate":
                    any_verified_dup = True

                DuplicatePair.objects.create(
                    tenant=self.tenant,
                    project=self.project,
                    group=db_group,
                    doc_a=doc_a,
                    doc_b=doc_b,
                    semantic_score=scores["semantic"],
                    lexical_score=scores["lexical"],
                    metadata_score=scores["metadata"],
                    combined_score=scores["combined"],
                    verified=bool(verification.get("classification")),
                    verification_result=verification.get("classification", ""),
                    verification_confidence=verification.get("confidence"),
                    verification_evidence=verification.get("evidence", ""),
                    evidence_chunks_a=[
                        {"chunk_id": str(c.id), "snippet": c.content[:200]} for c in evidence_a
                    ],
                    evidence_chunks_b=[
                        {"chunk_id": str(c.id), "snippet": c.content[:200]} for c in evidence_b
                    ],
                )

            for doc_a, doc_b, scores, evidence_a, evidence_b in auto_pairs:
                DuplicatePair.objects.create(
                    tenant=self.tenant,
                    project=self.project,
                    group=db_group,
                    doc_a=doc_a,
                    doc_b=doc_b,
                    semantic_score=scores["semantic"],
                    lexical_score=scores["lexical"],
                    metadata_score=scores["metadata"],
                    combined_score=scores["combined"],
                    verified=False,
                    verification_result="review",
                    verification_confidence=0.0,
                    verification_evidence="Score combiné entre les seuils — classé automatiquement pour revue manuelle.",
                    evidence_chunks_a=[
                        {"chunk_id": str(c.id), "snippet": c.content[:200]} for c in evidence_a
                    ],
                    evidence_chunks_b=[
                        {"chunk_id": str(c.id), "snippet": c.content[:200]} for c in evidence_b
                    ],
                )

            # Set group recommendation
            if any_verified_dup:
                db_group.recommended_action = DuplicateGroup.Action.DELETE_OLDER
                db_group.rationale = "LLM verified these documents as duplicates."
            elif all(s["combined"] >= self.semantic_threshold for _, _, s in group_pairs):
                db_group.recommended_action = DuplicateGroup.Action.REVIEW
                db_group.rationale = (
                    "High similarity scores but LLM did not confirm as exact duplicates."
                )
            else:
                db_group.recommended_action = DuplicateGroup.Action.KEEP
                db_group.rationale = "Documents are related but not duplicates."
            db_group.save()

            result_groups.append(db_group)

        return result_groups

    def _batch_verify_llm(
        self, pairs: list[tuple[Document, Document, dict, list, list]]
    ) -> dict[int, dict]:
        """Verify pairs with LLM using batched prompts (multiple pairs per call)."""
        if not pairs:
            return {}

        verifications: dict[int, dict] = {}

        # Split pairs into batches
        batches = []
        for i in range(0, len(pairs), self.llm_batch_size):
            batches.append(pairs[i : i + self.llm_batch_size])

        prompts = []
        batch_ranges = []  # (start_idx, count) for each batch
        offset = 0
        for batch in batches:
            prompt = self._build_batch_prompt(batch)
            prompts.append(prompt)
            batch_ranges.append((offset, len(batch)))
            offset += len(batch)

        if not prompts:
            return verifications

        responses = self.llm.chat_batch_or_concurrent(
            prompts, json_mode=True, on_progress=self.on_progress
        )

        for batch_idx, resp in enumerate(responses):
            start_idx, count = batch_ranges[batch_idx]
            if not resp:
                # Fill with empty verifications
                for i in range(count):
                    verifications[start_idx + i] = {
                        "classification": "",
                        "confidence": 0.0,
                        "evidence": "",
                    }
                continue

            try:
                data = json.loads(resp.content)
                results = data.get("results", [])
                # Map batch results back to global indices
                for result in results:
                    pair_index = result.get("pair_index", 0)
                    if 0 <= pair_index < count:
                        verifications[start_idx + pair_index] = result
                # Fill missing
                for i in range(count):
                    if start_idx + i not in verifications:
                        verifications[start_idx + i] = {
                            "classification": "",
                            "confidence": 0.0,
                            "evidence": "",
                        }
            except (json.JSONDecodeError, AttributeError):
                for i in range(count):
                    verifications[start_idx + i] = {
                        "classification": "",
                        "confidence": 0.0,
                        "evidence": "",
                    }

        return verifications

    def _build_batch_prompt(self, batch: list[tuple[Document, Document, dict, list, list]]) -> str:
        """Build a batched LLM verification prompt for multiple pairs."""
        pair_blocks = []
        for idx, (doc_a, doc_b, scores, evidence_a, evidence_b) in enumerate(batch):
            excerpt_a = "\n---\n".join(c.content[:250] for c in evidence_a)
            excerpt_b = "\n---\n".join(c.content[:250] for c in evidence_b)
            block = (
                f"=== Paire {idx} ===\n"
                f"Document A : « {doc_a.title} »\n"
                f"Chemin : {doc_a.path}\n"
                f"Extrait :\n{excerpt_a[:500]}\n\n"
                f"Document B : « {doc_b.title} »\n"
                f"Chemin : {doc_b.path}\n"
                f"Extrait :\n{excerpt_b[:500]}\n\n"
                f"Scores : sémantique={scores['semantic']:.3f}, "
                f"lexical={scores['lexical']:.3f}, "
                f"métadonnées={scores['metadata']:.3f}"
            )
            pair_blocks.append(block)

        pairs_block = "\n\n".join(pair_blocks)
        return get_prompt("DUPLICATE_VERIFICATION_BATCH").format(pairs_block=pairs_block)

    def _get_evidence_chunks(self, doc: Document, limit: int = 2) -> list[DocumentChunk]:
        """Get the first N chunks of a document as evidence."""
        return list(DocumentChunk.objects.filter(document=doc).order_by("chunk_index")[:limit])
