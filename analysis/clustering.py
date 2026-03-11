"""
Topic clustering and hierarchical tree index.

Uses HDBSCAN (or KMeans fallback) on chunk embeddings to discover topic clusters.
Builds a hierarchical tree: top-level clusters → subclusters → documents.
Generates LLM summaries for each cluster.

For visualization, 2D coordinates are computed via UMAP-style dimensionality reduction
(using sklearn TSNE/PCA as a lighter alternative).
"""
import json
import logging

import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA

from django.conf import settings

from analysis.models import ClusterMembership, TopicCluster, TreeNode
from ingestion.models import Document, DocumentChunk
from llm.client import get_llm_client
from llm.prompt_loader import get_prompt
from vectorstore.store import get_vector_store

logger = logging.getLogger(__name__)


class TopicClusterEngine:
    """Topic clustering on chunk embeddings with hierarchical decomposition."""

    def __init__(self, tenant, analysis_job, project, on_progress=None, config=None):
        self.tenant = tenant
        self.job = analysis_job
        self.project = project
        self.on_progress = on_progress
        self.vec_store = get_vector_store()
        self.llm = get_llm_client()
        self.config = config if config is not None else settings.ANALYSIS_CONFIG.get("clustering", {})
        self.algorithm = self.config.get("algorithm", "hdbscan")
        self.min_cluster_size = self.config.get("min_cluster_size", 3)
        self.min_samples = self.config.get("min_samples", 2)

    def run(self) -> list[TopicCluster]:
        """Run clustering and return created TopicCluster objects."""
        logger.info("Starting topic clustering for tenant=%s", self.tenant.slug)

        # Step 1: Get all chunk vectors
        chunk_vectors = self.vec_store.get_all_vectors_for_tenant(
            str(self.tenant.id), project_id=str(self.project.id)
        )
        if len(chunk_vectors) < self.min_cluster_size:
            logger.info("[clustering] Not enough chunks (%d) for clustering", len(chunk_vectors))
            return []

        logger.info("[clustering] %d chunk vectors loaded", len(chunk_vectors))
        chunk_ids = [cv[0] for cv in chunk_vectors]
        vectors = np.array([cv[1] for cv in chunk_vectors])

        # Step 2: Cluster
        logger.info("[clustering] Step 1/7: Running %s algorithm...", self.algorithm)
        labels = self._cluster(vectors)
        n_clusters = len(set(labels) - {-1})
        n_noise = int((labels == -1).sum())
        logger.info("[clustering] Step 1/7 done: %d clusters, %d noise points", n_clusters, n_noise)

        # Step 3: Compute 2D projections for visualization
        logger.info("[clustering] Step 2/7: PCA 2D projection...")
        coords_2d = self._project_2d(vectors)

        # Step 4: Create cluster records
        logger.info("[clustering] Step 3/7: Creating cluster records...")
        clusters = self._create_clusters(chunk_ids, labels, vectors, coords_2d)
        logger.info("[clustering] Step 3/7 done: %d cluster records created", len(clusters))

        # Step 5: Generate summaries (topic labels + descriptions)
        logger.info("[clustering] Step 4/7: Generating LLM summaries for %d clusters...", len(clusters))
        self._generate_summaries(clusters)
        logger.info("[clustering] Step 4/7 done")

        # Step 5b: Subcluster large clusters for finer-grained topics
        logger.info("[clustering] Step 5/7: Subclustering large clusters...")
        subclusters = self._subcluster(clusters, coords_2d, chunk_ids, labels)
        if subclusters:
            logger.info("[clustering] Step 5/7: Generating summaries for %d subclusters...", len(subclusters))
            self._generate_summaries(subclusters)
            logger.info("[clustering] Step 5/7 done: %d subclusters", len(subclusters))
        else:
            logger.info("[clustering] Step 5/7 done: no clusters large enough to subcluster")

        # Step 6: Generate taxonomy (organize clusters into categories)
        logger.info("[clustering] Step 6/7: Generating taxonomy...")
        taxonomy = self._generate_taxonomy(clusters)
        logger.info("[clustering] Step 6/7 done: %d categories", len(taxonomy))

        # Step 7: Build hierarchical tree from taxonomy
        logger.info("[clustering] Step 7/7: Building tree...")
        self._build_tree(clusters, taxonomy)
        logger.info("[clustering] Step 7/7 done")

        logger.info("Clustering produced %d clusters", len(clusters))
        return clusters

    def _cluster(self, vectors: np.ndarray) -> np.ndarray:
        """Run clustering algorithm and return labels."""
        if self.algorithm == "hdbscan":
            try:
                import hdbscan

                clusterer = hdbscan.HDBSCAN(
                    min_cluster_size=self.min_cluster_size,
                    min_samples=self.min_samples,
                    metric="euclidean",
                )
                labels = clusterer.fit_predict(vectors)
                return labels
            except ImportError:
                logger.warning("hdbscan not available, falling back to KMeans")

        # KMeans fallback
        k = self.config.get("kmeans_k")
        if k is None:
            # Auto-select k using elbow heuristic
            k = max(2, min(int(np.sqrt(len(vectors) / 2)), 20))

        kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
        return kmeans.fit_predict(vectors)

    def _project_2d(self, vectors: np.ndarray) -> np.ndarray:
        """Project vectors to 2D for visualization using PCA."""
        if vectors.shape[0] < 3:
            return np.zeros((vectors.shape[0], 2))

        pca = PCA(n_components=2, random_state=42)
        return pca.fit_transform(vectors)

    def _create_clusters(
        self,
        chunk_ids: list[str],
        labels: np.ndarray,
        vectors: np.ndarray,
        coords_2d: np.ndarray,
    ) -> list[TopicCluster]:
        """Create TopicCluster and ClusterMembership records."""
        unique_labels = set(labels)
        unique_labels.discard(-1)  # HDBSCAN noise label

        clusters = []
        chunk_to_doc = self._chunk_to_document_map(chunk_ids)

        for label in sorted(unique_labels):
            mask = labels == label
            cluster_chunk_ids = [chunk_ids[i] for i in range(len(chunk_ids)) if mask[i]]
            cluster_vectors = vectors[mask]
            cluster_coords = coords_2d[mask]

            # Compute centroid
            centroid = cluster_vectors.mean(axis=0)
            centroid_2d = cluster_coords.mean(axis=0)

            # Count unique documents in this cluster
            doc_ids = set()
            for cid in cluster_chunk_ids:
                if cid in chunk_to_doc:
                    doc_ids.add(chunk_to_doc[cid])

            cluster = TopicCluster.objects.create(
                tenant=self.tenant,
                project=self.project,
                analysis_job=self.job,
                label=f"Cluster {label}",  # Placeholder, LLM will generate real label
                level=0,
                doc_count=len(doc_ids),
                chunk_count=len(cluster_chunk_ids),
                centroid_x=float(centroid_2d[0]),
                centroid_y=float(centroid_2d[1]),
            )

            # Create memberships
            memberships = []
            for idx, cid in enumerate(cluster_chunk_ids):
                doc_id = chunk_to_doc.get(cid)
                if not doc_id:
                    continue

                # Compute similarity to centroid
                sim = float(np.dot(cluster_vectors[idx], centroid) / (
                    np.linalg.norm(cluster_vectors[idx]) * np.linalg.norm(centroid) + 1e-10
                ))

                memberships.append(ClusterMembership(
                    tenant=self.tenant,
                    project=self.project,
                    cluster=cluster,
                    chunk_id=cid,
                    document_id=doc_id,
                    similarity_to_centroid=sim,
                ))

            ClusterMembership.objects.bulk_create(memberships)
            clusters.append(cluster)

        # Handle noise points (label = -1) as orphans
        noise_mask = labels == -1
        noise_count = noise_mask.sum()
        if noise_count > 0:
            logger.info("Clustering produced %d noise points (orphans)", noise_count)

        return clusters

    def _subcluster(
        self,
        clusters: list[TopicCluster],
        coords_2d: np.ndarray,
        all_chunk_ids: list[str],
        all_labels: np.ndarray,
    ) -> list[TopicCluster]:
        """Subcluster large top-level clusters for finer-grained topics."""
        min_members = self.config.get("subcluster_min_members", 6)
        configured_k = self.config.get("subcluster_k")

        # Build mapping: chunk_id -> index in all_chunk_ids for fast lookup
        chunk_id_to_idx = {cid: i for i, cid in enumerate(all_chunk_ids)}

        # Load memberships for all clusters
        all_memberships = list(
            ClusterMembership.objects.filter(cluster__in=clusters)
            .values_list("cluster_id", "chunk_id")
        )
        memberships_by_cluster: dict[str, list[str]] = {}
        for cluster_id, chunk_id in all_memberships:
            memberships_by_cluster.setdefault(str(cluster_id), []).append(str(chunk_id))

        # Load vectors for subclustering
        all_member_chunk_ids = [str(cid) for _, cid in all_memberships]
        vectors_map = self.vec_store.get_chunk_embeddings_batch(all_member_chunk_ids)

        chunk_to_doc = self._chunk_to_document_map(all_member_chunk_ids)
        subclusters = []

        for sc_loop_idx, cluster in enumerate(clusters):
            member_chunk_ids = memberships_by_cluster.get(str(cluster.id), [])
            if len(member_chunk_ids) < min_members:
                continue
            logger.info("[clustering] Subclustering cluster %d/%d (%d members)", sc_loop_idx + 1, len(clusters), len(member_chunk_ids))

            # Gather vectors for this cluster's chunks
            sub_chunk_ids = []
            sub_vectors = []
            sub_coords = []
            for cid in member_chunk_ids:
                vec = vectors_map.get(cid)
                if vec is None:
                    continue
                sub_chunk_ids.append(cid)
                sub_vectors.append(vec)
                # Get 2D coords from the original projection
                idx = chunk_id_to_idx.get(cid)
                if idx is not None:
                    sub_coords.append(coords_2d[idx])
                else:
                    sub_coords.append(np.array([cluster.centroid_x or 0, cluster.centroid_y or 0]))

            if len(sub_chunk_ids) < min_members:
                continue

            sub_vectors_arr = np.array(sub_vectors)
            sub_coords_arr = np.array(sub_coords)

            # Determine k
            if configured_k:
                k = int(configured_k)
            else:
                k = max(2, min(int(np.sqrt(len(sub_chunk_ids) / 2)), 5))

            # Don't subcluster if we'd get trivial splits
            if k >= len(sub_chunk_ids):
                continue

            kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
            sub_labels = kmeans.fit_predict(sub_vectors_arr)

            for sub_label in range(k):
                mask = sub_labels == sub_label
                sc_chunk_ids = [sub_chunk_ids[i] for i in range(len(sub_chunk_ids)) if mask[i]]
                if not sc_chunk_ids:
                    continue

                sc_vectors = sub_vectors_arr[mask]
                sc_coords = sub_coords_arr[mask]

                centroid = sc_vectors.mean(axis=0)
                centroid_2d = sc_coords.mean(axis=0)

                doc_ids = {chunk_to_doc[cid] for cid in sc_chunk_ids if cid in chunk_to_doc}

                subcluster = TopicCluster.objects.create(
                    tenant=self.tenant,
                    project=self.project,
                    analysis_job=self.job,
                    parent=cluster,
                    label=f"{cluster.label} — sous-thème {sub_label + 1}",
                    level=1,
                    doc_count=len(doc_ids),
                    chunk_count=len(sc_chunk_ids),
                    centroid_x=float(centroid_2d[0]),
                    centroid_y=float(centroid_2d[1]),
                )

                memberships = []
                for idx_m, cid in enumerate(sc_chunk_ids):
                    doc_id = chunk_to_doc.get(cid)
                    if not doc_id:
                        continue
                    sim = float(np.dot(sc_vectors[idx_m], centroid) / (
                        np.linalg.norm(sc_vectors[idx_m]) * np.linalg.norm(centroid) + 1e-10
                    ))
                    memberships.append(ClusterMembership(
                        tenant=self.tenant,
                        project=self.project,
                        cluster=subcluster,
                        chunk_id=cid,
                        document_id=doc_id,
                        similarity_to_centroid=sim,
                    ))

                ClusterMembership.objects.bulk_create(memberships)
                subclusters.append(subcluster)

        return subclusters

    def _chunk_to_document_map(self, chunk_ids: list[str]) -> dict[str, str]:
        """Build chunk_id -> document_id mapping."""
        chunks = DocumentChunk.objects.filter(id__in=chunk_ids).values_list("id", "document_id")
        return {str(cid): str(did) for cid, did in chunks}

    def _generate_summaries(self, clusters: list[TopicCluster]):
        """Generate LLM summaries and labels for each cluster concurrently."""
        if not clusters:
            return

        # Preload all memberships, chunks, and docs to avoid N+1 queries
        all_memberships = list(
            ClusterMembership.objects.filter(cluster__in=clusters)
            .order_by("-similarity_to_centroid")
        )
        all_chunk_ids = {m.chunk_id for m in all_memberships}
        all_doc_ids = {m.document_id for m in all_memberships}

        chunks_map = {c.id: c for c in DocumentChunk.objects.filter(id__in=all_chunk_ids)}
        docs_map = {d.id: d for d in Document.objects.filter(id__in=all_doc_ids)}

        # Group memberships by cluster
        memberships_by_cluster: dict[str, list[ClusterMembership]] = {}
        for m in all_memberships:
            memberships_by_cluster.setdefault(str(m.cluster_id), []).append(m)

        # Build prompts for all clusters
        prompts = []
        cluster_indices = []
        for i, cluster in enumerate(clusters):
            members = memberships_by_cluster.get(str(cluster.id), [])
            top_members = members[:5]  # Already sorted by -similarity_to_centroid

            excerpts = []
            for m in top_members:
                chunk = chunks_map.get(m.chunk_id)
                doc = docs_map.get(m.document_id)
                if chunk and doc:
                    excerpts.append(f"[{doc.title}]: {chunk.content[:300]}")

            if excerpts:
                prompts.append(get_prompt("CLUSTER_SUMMARY").format(excerpts="\n\n".join(excerpts)))
                cluster_indices.append(i)

        # Concurrent LLM calls
        if prompts:
            responses = self.llm.chat_batch_or_concurrent(prompts, json_mode=True, on_progress=self.on_progress)
            for resp, idx in zip(responses, cluster_indices):
                if not resp:
                    continue
                try:
                    data = json.loads(resp.content)
                    clusters[idx].label = data.get("label", clusters[idx].label)[:500]
                    clusters[idx].summary = data.get("summary", "")
                    clusters[idx].key_concepts = data.get("key_concepts", [])
                    clusters[idx].content_purpose = data.get("content_purpose", "")[:500]
                    clusters[idx].save()
                except (json.JSONDecodeError, Exception) as e:
                    logger.warning("Cluster summary generation failed for %s: %s",
                                   clusters[idx].id, e)
                    # Fallback label from top document titles
                    members = memberships_by_cluster.get(str(clusters[idx].id), [])
                    top_docs = [docs_map.get(m.document_id) for m in members[:3] if m.document_id in docs_map]
                    top_docs = [d for d in top_docs if d]
                    if top_docs:
                        clusters[idx].label = " / ".join(d.title[:30] for d in top_docs)
                        clusters[idx].save()

    def _generate_taxonomy(self, clusters: list[TopicCluster]) -> list[dict]:
        """Use LLM to organize clusters into a hierarchical taxonomy.

        Returns a list of category dicts: [{"category": "...", "clusters": [idx, ...]}]
        Falls back to a flat list if LLM fails or there's only one cluster.
        """
        if len(clusters) <= 1:
            return [{"category": clusters[0].label if clusters else "Topics", "clusters": [0]}]

        cluster_list = "\n".join(
            f"{i}. {c.label} — {(c.summary or 'No summary')[:150]}"
            for i, c in enumerate(clusters)
        )

        try:
            prompt = get_prompt("TOPIC_TAXONOMY").format(cluster_list=cluster_list)
            response = self.llm.chat(prompt, json_mode=True)
            data = json.loads(response.content)
            taxonomy = data.get("taxonomy", [])

            # Validate: every cluster index must appear exactly once
            assigned = set()
            valid = True
            for cat in taxonomy:
                cat_clusters = cat.get("clusters", [])
                if not isinstance(cat_clusters, list):
                    valid = False
                    break
                for idx in cat_clusters:
                    if not isinstance(idx, int) or idx < 0 or idx >= len(clusters):
                        valid = False
                        break
                    assigned.add(idx)
                if not valid:
                    break

            if valid and assigned == set(range(len(clusters))):
                logger.info("Taxonomy generated: %d categories for %d clusters",
                            len(taxonomy), len(clusters))
                return taxonomy

            logger.warning("Taxonomy validation failed, falling back to flat structure")

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning("Taxonomy generation failed: %s", e)

        # Fallback: single category containing all clusters
        return [{"category": "Topics", "clusters": list(range(len(clusters)))}]

    def _build_tree(self, clusters: list[TopicCluster], taxonomy: list[dict]):
        """Build hierarchical tree: categories → clusters → [subclusters →] documents."""
        # Preload all clusters (including subclusters) and their document mappings
        all_clusters = list(TopicCluster.objects.filter(analysis_job=self.job))
        all_cluster_ids = [c.id for c in all_clusters]

        all_memberships = (
            ClusterMembership.objects.filter(cluster_id__in=all_cluster_ids)
            .values_list("cluster_id", "document_id")
        )
        cluster_doc_map: dict[str, set[str]] = {}
        for cluster_id, doc_id in all_memberships:
            cluster_doc_map.setdefault(str(cluster_id), set()).add(str(doc_id))

        all_doc_ids = set()
        for doc_ids in cluster_doc_map.values():
            all_doc_ids.update(doc_ids)
        docs_by_id = {str(d.id): d for d in Document.objects.filter(id__in=all_doc_ids)}

        # Preload subclusters grouped by parent
        subclusters_by_parent: dict[str, list[TopicCluster]] = {}
        for c in all_clusters:
            if c.parent_id:
                subclusters_by_parent.setdefault(str(c.parent_id), []).append(c)

        for cat_idx, cat in enumerate(taxonomy):
            category_name = cat.get("category", "Topics")
            cluster_indices = cat.get("clusters", [])

            # If only one category, skip the category level to avoid redundancy
            if len(taxonomy) == 1:
                category_node = None
            else:
                category_node = TreeNode.objects.create(
                    tenant=self.tenant,
                    project=self.project,
                    analysis_job=self.job,
                    label=category_name,
                    node_type="category",
                    level=0,
                    sort_order=cat_idx,
                )

            for sort_idx, cluster_idx in enumerate(cluster_indices):
                if cluster_idx >= len(clusters):
                    continue
                cluster = clusters[cluster_idx]
                base_level = 0 if category_node is None else 1

                cluster_node = TreeNode.objects.create(
                    tenant=self.tenant,
                    project=self.project,
                    analysis_job=self.job,
                    parent=category_node,
                    label=cluster.label,
                    node_type="cluster",
                    cluster=cluster,
                    level=base_level,
                    sort_order=sort_idx,
                )

                # Check for subclusters
                children = subclusters_by_parent.get(str(cluster.id), [])
                if children:
                    # Attach documents via subclusters
                    for sc_idx, subcluster in enumerate(children):
                        sc_node = TreeNode.objects.create(
                            tenant=self.tenant,
                            project=self.project,
                            analysis_job=self.job,
                            parent=cluster_node,
                            label=subcluster.label,
                            node_type="subcluster",
                            cluster=subcluster,
                            level=base_level + 1,
                            sort_order=sc_idx,
                        )

                        sc_doc_ids = cluster_doc_map.get(str(subcluster.id), set())
                        sorted_docs = sorted(
                            (docs_by_id[did] for did in sc_doc_ids if did in docs_by_id),
                            key=lambda d: d.title,
                        )
                        for doc_idx, doc in enumerate(sorted_docs):
                            TreeNode.objects.create(
                                tenant=self.tenant,
                                project=self.project,
                                analysis_job=self.job,
                                parent=sc_node,
                                label=doc.title,
                                node_type="document",
                                document=doc,
                                level=base_level + 2,
                                sort_order=doc_idx,
                            )
                else:
                    # No subclusters — attach documents directly
                    doc_ids = cluster_doc_map.get(str(cluster.id), set())
                    sorted_docs = sorted(
                        (docs_by_id[did] for did in doc_ids if did in docs_by_id),
                        key=lambda d: d.title,
                    )
                    for doc_idx, doc in enumerate(sorted_docs):
                        TreeNode.objects.create(
                            tenant=self.tenant,
                            project=self.project,
                            analysis_job=self.job,
                            parent=cluster_node,
                            label=doc.title,
                            node_type="document",
                            document=doc,
                            level=base_level + 1,
                            sort_order=doc_idx,
                        )
