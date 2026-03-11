"""
Gap detection and topic coverage analysis.

Identifies gaps via:
  1. QG/RAG: Generate questions per cluster, attempt retrieval — unanswered = gap
  2. Adjacent cluster analysis: topics implied by neighboring clusters but missing docs
  3. Orphan topics: clusters with very few documents
  4. Stale areas: clusters where most documents are outdated

Outputs a ranked "missing topics" list with suggested document titles.
"""

import json
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from analysis.models import ClusterMembership, GapReport, TopicCluster
from ingestion.models import Document, DocumentChunk
from llm.client import get_llm_client
from llm.prompt_loader import get_prompt
from vectorstore.store import get_vector_store

logger = logging.getLogger(__name__)


class GapDetector:
    """Detect documentation gaps via QG/RAG, cluster analysis, and structural graph analysis."""

    def __init__(self, tenant, analysis_job, project, nsg=None, on_progress=None, config=None):
        self.tenant = tenant
        self.job = analysis_job
        self.project = project
        self.nsg = nsg
        self.on_progress = on_progress
        self.llm = get_llm_client()
        self.vec_store = get_vector_store()
        self.config = (
            config if config is not None else settings.ANALYSIS_CONFIG.get("gap_detection", {})
        )
        self.question_count = self.config.get("coverage_question_count", 5)
        self.confidence_threshold = self.config.get("confidence_threshold", 0.5)
        self.orphan_max_size = self.config.get("orphan_cluster_max_size", 2)
        self.staleness_days = settings.ANALYSIS_CONFIG.get("contradiction", {}).get(
            "staleness_days", 180
        )
        # Similarity pre-filter: skip LLM call when top passage similarity is
        # above (auto-answered) or below (auto-unanswered) these thresholds.
        self.sim_auto_answer = self.config.get("similarity_auto_answer", 0.82)
        self.sim_auto_unanswered = self.config.get("similarity_auto_unanswered", 0.35)

    def run(self) -> list[GapReport]:
        """Run all gap detection strategies."""
        logger.info("Starting gap detection for tenant=%s", self.tenant.slug)

        clusters = list(
            TopicCluster.objects.filter(project=self.project, analysis_job=self.job).order_by(
                "label"
            )
        )

        if not clusters:
            return []

        logger.info("[gaps] %d clusters to analyze", len(clusters))
        gaps = []

        # Strategy 1: QG/RAG coverage analysis
        logger.info("[gaps] Strategy 1/5: QG/RAG coverage analysis...")
        qg_gaps = self._qg_rag_gaps(clusters)
        gaps.extend(qg_gaps)
        logger.info("[gaps] Strategy 1/5 done: %d coverage gaps", len(qg_gaps))

        # Strategy 2: Orphan topics (tiny clusters)
        logger.info("[gaps] Strategy 2/5: Orphan topics...")
        orphan_gaps = self._orphan_topics(clusters)
        gaps.extend(orphan_gaps)
        logger.info("[gaps] Strategy 2/5 done: %d orphan gaps", len(orphan_gaps))

        # Strategy 3: Stale areas
        logger.info("[gaps] Strategy 3/5: Stale areas...")
        stale_gaps = self._stale_areas(clusters)
        gaps.extend(stale_gaps)
        logger.info("[gaps] Strategy 3/5 done: %d stale gaps", len(stale_gaps))

        # Strategy 4: Adjacent cluster gap inference
        logger.info("[gaps] Strategy 4/5: Adjacent cluster gaps...")
        adj_gaps = self._adjacent_cluster_gaps(clusters)
        gaps.extend(adj_gaps)
        logger.info("[gaps] Strategy 4/5 done: %d adjacent gaps", len(adj_gaps))

        # Strategy 5: Structural gaps from semantic graph
        if self.nsg is not None:
            logger.info("[gaps] Strategy 5/5: Structural gaps from semantic graph...")
            struct_gaps = self._structural_gaps()
            gaps.extend(struct_gaps)
            logger.info("[gaps] Strategy 5/5 done: %d structural gaps", len(struct_gaps))
        else:
            logger.info("[gaps] Strategy 5/5: Skipped (no semantic graph)")

        logger.info("Gap detection found %d gaps", len(gaps))
        return gaps

    def _qg_rag_gaps(self, clusters: list[TopicCluster]) -> list[GapReport]:
        """Generate questions per cluster and check if documentation answers them."""
        # Step 1: Generate questions for ALL clusters concurrently
        qg_prompts = []
        cluster_indices = []
        for i, cluster in enumerate(clusters):
            adjacent = self._get_adjacent_clusters(cluster, clusters)
            adjacent_labels = [c.label for c in adjacent[:5]]
            key_concepts = cluster.key_concepts or [cluster.label]
            prompt = get_prompt("GAP_DETECTION_QUESTIONS").format(
                n_questions=self.question_count,
                cluster_label=cluster.label,
                cluster_summary=cluster.summary or "No summary available",
                key_concepts=", ".join(key_concepts),
                adjacent_clusters=", ".join(adjacent_labels) if adjacent_labels else "none",
            )
            qg_prompts.append(prompt)
            cluster_indices.append(i)

        logger.info(
            "[gaps/qg_rag] Generating questions for %d clusters (%d prompts)...",
            len(clusters),
            len(qg_prompts),
        )
        qg_responses = self.llm.chat_batch_or_concurrent(
            qg_prompts, json_mode=True, on_progress=self.on_progress
        )
        logger.info("[gaps/qg_rag] Question generation done, parsing responses...")

        # Parse questions per cluster
        cluster_questions: dict[int, list[dict]] = {}
        for idx, resp in zip(cluster_indices, qg_responses):
            if not resp:
                continue
            try:
                data = json.loads(resp.content)
                questions = data.get("questions", [])
                if questions:
                    cluster_questions[idx] = questions
            except (json.JSONDecodeError, AttributeError):
                continue

        if not cluster_questions:
            return []

        # Step 2: Batch-embed ALL questions at once
        all_questions_flat: list[tuple[int, int, dict]] = []  # (cluster_idx, q_idx, q_info)
        question_texts: list[str] = []
        for cluster_idx, questions in cluster_questions.items():
            for q_idx, q_info in enumerate(questions):
                question = q_info.get("question", "")
                if question:
                    all_questions_flat.append((cluster_idx, q_idx, q_info))
                    question_texts.append(question)

        if not question_texts:
            return []

        logger.info("[gaps/qg_rag] Embedding %d questions...", len(question_texts))
        all_embeddings = self.llm.embed(question_texts)

        # Step 3: Batch vector search for all questions at once
        all_search_results = self.vec_store.search_batch(
            query_vectors=all_embeddings,
            tenant_id=str(self.tenant.id),
            k=5,
            project_id=str(self.project.id),
        )

        # Preload all referenced chunks and docs in bulk
        all_chunk_ids: set[str] = set()
        all_doc_ids: set[str] = set()
        for search_results in all_search_results:
            for r in search_results:
                all_chunk_ids.add(r["chunk_id"])
                all_doc_ids.add(r["document_id"])

        chunks_map = {str(c.id): c for c in DocumentChunk.objects.filter(id__in=all_chunk_ids)}
        docs_map = {str(d.id): d for d in Document.objects.filter(id__in=all_doc_ids)}

        # Build coverage check prompts from batch results, applying similarity
        # pre-filter to skip obvious cases and save LLM calls.
        coverage_prompts = []
        coverage_meta: list[tuple[int, int, dict]] = []  # (cluster_idx, q_idx, q_info)
        no_results_indices: list[tuple[int, int, dict]] = []
        auto_answered: list[tuple[int, int, dict, float]] = []  # high-sim → skip LLM

        for (cluster_idx, q_idx, q_info), search_results in zip(
            all_questions_flat, all_search_results
        ):
            question = q_info.get("question", "")

            if not search_results:
                no_results_indices.append((cluster_idx, q_idx, q_info))
                continue

            top_sim = max(r.get("similarity", 0.0) for r in search_results)

            # Pre-filter: very low similarity → treat as unanswered
            if top_sim < self.sim_auto_unanswered:
                no_results_indices.append((cluster_idx, q_idx, q_info))
                continue

            # Pre-filter: very high similarity → treat as answered
            if top_sim >= self.sim_auto_answer:
                auto_answered.append((cluster_idx, q_idx, q_info, top_sim))
                continue

            passages = []
            for r in search_results:
                chunk = chunks_map.get(r["chunk_id"])
                doc = docs_map.get(r["document_id"])
                if chunk and doc:
                    passages.append(f"[{doc.title}]: {chunk.content[:300]}")

            if not passages:
                no_results_indices.append((cluster_idx, q_idx, q_info))
                continue

            prompt = get_prompt("GAP_COVERAGE_CHECK").format(
                question=question,
                passages="\n\n".join(passages),
            )
            coverage_prompts.append(prompt)
            coverage_meta.append((cluster_idx, q_idx, q_info))

        # Step 4: Concurrent LLM coverage checks
        logger.info(
            "[gaps/qg_rag] Coverage pre-filter: %d auto-answered (sim>=%.2f), "
            "%d auto-unanswered (sim<%.2f), %d sent to LLM",
            len(auto_answered),
            self.sim_auto_answer,
            len(no_results_indices),
            self.sim_auto_unanswered,
            len(coverage_prompts),
        )
        coverage_responses = self.llm.chat_batch_or_concurrent(
            coverage_prompts, json_mode=True, on_progress=self.on_progress
        )
        logger.info("[gaps/qg_rag] Coverage check done")

        # Build per-cluster results
        cluster_coverage: dict[int, list[dict]] = {}  # cluster_idx -> list of coverage results
        for (cluster_idx, q_idx, q_info), resp in zip(coverage_meta, coverage_responses):
            coverage = {
                "answered": False,
                "confidence": 0.0,
                "missing_info": q_info.get("question", ""),
            }
            if resp:
                try:
                    coverage = json.loads(resp.content)
                except (json.JSONDecodeError, AttributeError):
                    pass
            cluster_coverage.setdefault(cluster_idx, []).append(
                {
                    "q_info": q_info,
                    "coverage": coverage,
                }
            )

        # Add auto-answered (high similarity, skipped LLM)
        for cluster_idx, q_idx, q_info, top_sim in auto_answered:
            cluster_coverage.setdefault(cluster_idx, []).append(
                {
                    "q_info": q_info,
                    "coverage": {"answered": True, "confidence": top_sim, "missing_info": ""},
                }
            )

        # Add no-results as unanswered
        for cluster_idx, q_idx, q_info in no_results_indices:
            cluster_coverage.setdefault(cluster_idx, []).append(
                {
                    "q_info": q_info,
                    "coverage": {
                        "answered": False,
                        "confidence": 0.0,
                        "missing_info": q_info.get("question", ""),
                    },
                }
            )

        # Step 5: Create GapReport records
        gaps = []
        for cluster_idx, questions in cluster_questions.items():
            cluster = clusters[cluster_idx]
            coverages = cluster_coverage.get(cluster_idx, [])

            unanswered = []
            total_confidence = 0.0
            total_questions = len(questions)

            for entry in coverages:
                q_info = entry["q_info"]
                coverage = entry["coverage"]
                total_confidence += coverage.get("confidence", 0.0)

                if (
                    not coverage.get("answered", True)
                    or coverage.get("confidence", 1.0) < self.confidence_threshold
                ):
                    missing_info = coverage.get("missing_info", "")
                    if not isinstance(missing_info, str):
                        missing_info = str(missing_info)
                    unanswered.append(
                        {
                            "question": q_info.get("question", ""),
                            "importance": q_info.get("importance", "medium"),
                            "confidence": coverage.get("confidence", 0.0),
                            "missing_info": missing_info,
                        }
                    )

            if unanswered:
                coverage_score = 1.0 - (len(unanswered) / total_questions) if total_questions else 0

                severity = "low"
                if coverage_score < 0.3:
                    severity = "high"
                elif coverage_score < 0.6:
                    severity = "medium"

                high_importance = [q for q in unanswered if q["importance"] == "high"]
                primary_gap = high_importance[0] if high_importance else unanswered[0]

                gap = GapReport.objects.create(
                    tenant=self.tenant,
                    project=self.project,
                    analysis_job=self.job,
                    gap_type=GapReport.GapType.LOW_COVERAGE,
                    title=f"Manquant : {primary_gap['missing_info'][:200]}"
                    if primary_gap.get("missing_info")
                    else f"Lacune dans : {cluster.label}",
                    description=f"Le cluster « {cluster.label} » a {len(unanswered)}/{total_questions} questions sans réponse.",
                    severity=severity,
                    related_cluster=cluster,
                    coverage_score=coverage_score,
                    evidence={
                        "unanswered_questions": unanswered,
                        "total_questions": total_questions,
                    },
                )
                gaps.append(gap)

        return gaps

    def _orphan_topics(self, clusters: list[TopicCluster]) -> list[GapReport]:
        """Identify clusters with very few documents (orphan topics)."""
        gaps = []
        for cluster in clusters:
            if cluster.doc_count <= self.orphan_max_size and cluster.doc_count > 0:
                gap = GapReport.objects.create(
                    tenant=self.tenant,
                    project=self.project,
                    analysis_job=self.job,
                    gap_type=GapReport.GapType.ORPHAN_TOPIC,
                    title=f"Sujet orphelin : {cluster.label}",
                    description=(
                        f"Le cluster « {cluster.label} » ne contient que {cluster.doc_count} document(s). "
                        f"Ce sujet nécessite peut-être davantage de documentation."
                    ),
                    severity="low",
                    related_cluster=cluster,
                    coverage_score=min(1.0, cluster.doc_count / 5.0),
                    evidence={"doc_count": cluster.doc_count},
                )
                gaps.append(gap)
        return gaps

    def _stale_areas(self, clusters: list[TopicCluster]) -> list[GapReport]:
        """Identify clusters where most documents are outdated."""
        stale_threshold = timezone.now() - timedelta(days=self.staleness_days)

        # Batch-load all cluster → document mappings
        memberships = ClusterMembership.objects.filter(cluster__in=clusters).values_list(
            "cluster_id", "document_id"
        )
        cluster_doc_ids: dict[str, set[str]] = {}
        all_doc_ids: set[str] = set()
        for cluster_id, doc_id in memberships:
            cid = str(cluster_id)
            did = str(doc_id)
            cluster_doc_ids.setdefault(cid, set()).add(did)
            all_doc_ids.add(did)

        # Batch-load stale status for all docs
        stale_doc_ids = set(
            str(d)
            for d in Document.objects.filter(
                id__in=all_doc_ids, source_modified_at__lt=stale_threshold
            ).values_list("id", flat=True)
        )

        gaps = []
        for cluster in clusters:
            doc_ids = cluster_doc_ids.get(str(cluster.id), set())
            total = len(doc_ids)
            if total == 0:
                continue

            stale = len(doc_ids & stale_doc_ids)
            stale_ratio = stale / total

            if stale_ratio >= 0.7:
                gap = GapReport.objects.create(
                    tenant=self.tenant,
                    project=self.project,
                    analysis_job=self.job,
                    gap_type=GapReport.GapType.STALE_AREA,
                    title=f"Zone obsolète : {cluster.label}",
                    description=(
                        f"{stale}/{total} documents dans « {cluster.label} » n'ont pas été mis à jour "
                        f"depuis plus de {self.staleness_days} jours."
                    ),
                    severity="medium" if stale_ratio < 0.9 else "high",
                    related_cluster=cluster,
                    coverage_score=1.0 - stale_ratio,
                    evidence={
                        "stale_count": stale,
                        "total_count": total,
                        "stale_ratio": stale_ratio,
                    },
                )
                gaps.append(gap)

        return gaps

    def _adjacent_cluster_gaps(self, clusters: list[TopicCluster]) -> list[GapReport]:
        """Infer missing topics from gaps between adjacent clusters."""
        if len(clusters) < 3:
            return []

        # Build all prompts
        prompts = []
        cluster_data: list[tuple[TopicCluster, list[str]]] = []
        for cluster in clusters:
            neighbors = self._get_adjacent_clusters(cluster, clusters)
            if len(neighbors) < 2:
                continue
            neighbor_labels = [n.label for n in neighbors[:3]]
            prompt = (
                f"Étant donné ces clusters thématiques de documentation :\n"
                f"- Actuel : {cluster.label} ({cluster.summary[:200] if cluster.summary else 'Pas de résumé'})\n"
                f"- Sujets adjacents : {', '.join(neighbor_labels)}\n\n"
                f"Existe-t-il un sujet qui devrait logiquement exister entre ces clusters "
                f"mais qui semble manquer ? Si oui, quel document devrait être créé ?\n\n"
                f'Réponds avec un JSON : {{"has_gap": true/false, "suggested_title": "...", '
                f'"description": "..."}}'
            )
            prompts.append(prompt)
            cluster_data.append((cluster, neighbor_labels))

        if not prompts:
            return []

        # Concurrent LLM calls
        responses = self.llm.chat_batch_or_concurrent(
            prompts, json_mode=True, on_progress=self.on_progress
        )

        gaps = []
        for (cluster, neighbor_labels), resp in zip(cluster_data, responses):
            if not resp:
                continue
            try:
                data = json.loads(resp.content)
                if data.get("has_gap"):
                    gap = GapReport.objects.create(
                        tenant=self.tenant,
                        project=self.project,
                        analysis_job=self.job,
                        gap_type=GapReport.GapType.MISSING_TOPIC,
                        title=data.get("suggested_title", "Sujet manquant")[:500],
                        description=data.get("description", ""),
                        severity="medium",
                        related_cluster=cluster,
                        coverage_score=0.0,
                        evidence={"adjacent_clusters": neighbor_labels},
                    )
                    gaps.append(gap)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("Adjacent cluster gap detection failed: %s", e)

        return gaps

    def _structural_gaps(self) -> list[GapReport]:
        """Detect gaps from the semantic graph structure: disconnected components and bridge edges."""
        import networkx as nx

        gaps = []
        graph = self.nsg.graph.to_undirected()

        if graph.number_of_nodes() < 3:
            return gaps

        # --- Concept islands: disconnected components with few nodes ---
        components = list(nx.connected_components(graph))
        if len(components) > 1:
            # Sort by size; the largest is the "main" component
            components.sort(key=len, reverse=True)
            main_size = len(components[0])
            for comp in components[1:]:
                # Small isolated components are concept islands
                if len(comp) <= max(3, main_size * 0.05):
                    concepts = sorted(comp)[:10]
                    gap = GapReport.objects.create(
                        tenant=self.tenant,
                        project=self.project,
                        analysis_job=self.job,
                        gap_type=GapReport.GapType.CONCEPT_ISLAND,
                        title=f"Îlot conceptuel : {', '.join(concepts[:3])}",
                        description=(
                            f"{len(comp)} concept(s) isolé(s) du reste du graphe sémantique : "
                            f"{', '.join(concepts)}. Ces sujets manquent de liens avec la "
                            f"documentation principale."
                        ),
                        severity="medium" if len(comp) >= 2 else "low",
                        coverage_score=0.0,
                        evidence={"concepts": concepts, "component_size": len(comp)},
                    )
                    gaps.append(gap)

        # --- Weak bridges: articulation points / bridge edges ---
        # Only analyze the largest connected component
        if components:
            largest = graph.subgraph(components[0]).copy()
            bridges = list(nx.bridges(largest))
            for src, dst in bridges:
                # Only flag bridges between well-connected regions
                src_degree = largest.degree(src)
                dst_degree = largest.degree(dst)
                if src_degree >= 2 and dst_degree >= 2:
                    gap = GapReport.objects.create(
                        tenant=self.tenant,
                        project=self.project,
                        analysis_job=self.job,
                        gap_type=GapReport.GapType.WEAK_BRIDGE,
                        title=f"Pont fragile : {src} — {dst}",
                        description=(
                            f"Le lien entre « {src} » et « {dst} » est le seul chemin reliant "
                            f"deux parties du graphe sémantique. Un document traitant de leur "
                            f"relation renforcerait la couverture."
                        ),
                        severity="medium",
                        coverage_score=0.2,
                        evidence={
                            "bridge": [src, dst],
                            "src_degree": src_degree,
                            "dst_degree": dst_degree,
                        },
                    )
                    gaps.append(gap)

        return gaps

    def _get_adjacent_clusters(
        self, cluster: TopicCluster, all_clusters: list[TopicCluster]
    ) -> list[TopicCluster]:
        """Find clusters closest to the given cluster by centroid distance."""
        if cluster.centroid_x is None or cluster.centroid_y is None:
            return []

        others = [c for c in all_clusters if c.id != cluster.id and c.centroid_x is not None]

        others.sort(
            key=lambda c: (
                (c.centroid_x - cluster.centroid_x) ** 2 + (c.centroid_y - cluster.centroid_y) ** 2
            )
        )

        return others[:5]
