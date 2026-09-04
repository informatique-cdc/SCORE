"""
Knowledge graph builder — orchestrates extraction, standardisation and layout.

Follows the contract of the other detectors (GapDetector, ContradictionDetector):
``__init__(tenant, job, project, on_progress=, config=)`` then ``run()``.
"""

import collections
import logging
import time

from django.conf import settings
from django.db import transaction

from analysis.kg import layout
from analysis.kg.extraction import extract_triples, normalize
from analysis.kg.inference import infer
from analysis.kg.standardize import standardize
from analysis.models import KGEntity, KGRelation, KnowledgeGraphRun, TopicCluster
from llm.client import get_llm_client

logger = logging.getLogger(__name__)


class KnowledgeGraphBuilder:
    """Build one KnowledgeGraphRun for an analysis job, cluster by cluster."""

    def __init__(self, tenant, analysis_job, project, on_progress=None, config=None):
        self.tenant = tenant
        self.job = analysis_job
        self.project = project
        self.on_progress = on_progress
        self.llm = get_llm_client()
        self.config = (
            config if config is not None else settings.ANALYSIS_CONFIG.get("knowledge_graph", {})
        )

    def run(self) -> KnowledgeGraphRun:
        started = time.monotonic()

        clusters = self._clusters()
        if not clusters:
            logger.warning("[kg] No clusters for project %s — empty graph", self.project.id)
            return self._persist([], {}, [], clusters, time.monotonic() - started)

        logger.info("[kg] Step 1/4: extracting triples from %d clusters...", len(clusters))
        triples = extract_triples(self.llm, clusters, self.config, on_progress=self.on_progress)
        if not triples:
            return self._persist([], {}, [], clusters, time.monotonic() - started)

        logger.info("[kg] Step 2/4: standardising %d triples...", len(triples))
        mapping = standardize(triples, self.llm, self.config)

        logger.info("[kg] Step 3/5: aggregating graph...")
        entities, edges = self._aggregate(triples, mapping)
        observed = layout.summarize(
            layout.build_graph(
                entities.keys(), [(e["subject"], e["object"], e["weight"]) for e in edges]
            )
        )
        logger.info(
            "[kg] Observed: %(nodes)d nodes, %(edges)d edges, degree %(avg_degree)s, "
            "%(components)d components, largest %(largest_pct)s%%",
            observed,
        )

        logger.info("[kg] Step 4/5: inferring relations...")
        inferred = infer(entities, edges, clusters, self.llm, self.config)
        edges += inferred

        # Inferred edges must count towards degree, centrality and layout:
        # they are part of the graph the user explores, only flagged apart.
        logger.info("[kg] Step 5/5: computing layout for %d entities...", len(entities))
        graph = layout.build_graph(
            entities.keys(), [(e["subject"], e["object"], e["weight"]) for e in edges]
        )
        stats = layout.summarize(graph)
        logger.info(
            "[kg] Final: %(nodes)d nodes, %(edges)d edges, degree %(avg_degree)s, "
            "%(components)d components, largest %(largest_pct)s%%",
            stats,
        )

        centralities = layout.centrality(
            graph, sample=self.config.get("layout", {}).get("betweenness_sample", 500)
        )
        coords = layout.positions(
            graph, {key: data["main_cluster"] for key, data in entities.items()}
        )
        for key, data in entities.items():
            data["degree"] = graph.degree(key) if key in graph else 0
            data["centrality"] = centralities.get(key, 0.0)
            data["pos"] = coords.get(key, (0.0, 0.0))

        return self._persist(entities, mapping, edges, clusters, time.monotonic() - started)

    # ------------------------------------------------------------------

    def _clusters(self):
        level = self.config.get("extraction", {}).get("cluster_level", 1)
        qs = TopicCluster.objects.filter(analysis_job=self.job, level=level)
        if not qs.exists():
            # Projects whose clustering produced a single flat level.
            qs = TopicCluster.objects.filter(analysis_job=self.job)
        return list(qs)

    def _aggregate(self, triples, mapping):
        """Collapse triples onto canonical entities and deduplicated edges."""
        min_freq = self.config.get("min_entity_frequency", 2)

        freq = collections.Counter()
        for triple in triples:
            for surface in (triple.subject, triple.object):
                key = mapping.get(surface)
                if key:
                    freq[key] += 1

        kept = {key for key, n in freq.items() if n >= min_freq}
        if not kept:
            # min_frequency filtered everything out — keep the graph rather than
            # returning nothing, and let the numbers speak on screen.
            kept = set(freq)

        entities: dict[str, dict] = {}
        edge_map: dict[tuple, dict] = {}

        for triple in triples:
            subject_key = mapping.get(triple.subject)
            object_key = mapping.get(triple.object)
            if subject_key not in kept or object_key not in kept:
                continue
            if subject_key == object_key:
                continue

            for key, surface in ((subject_key, triple.subject), (object_key, triple.object)):
                data = entities.setdefault(
                    key,
                    {
                        "labels": collections.Counter(),
                        "aliases": set(),
                        "frequency": 0,
                        "clusters": collections.Counter(),
                        "documents": set(),
                    },
                )
                data["labels"][surface] += 1
                if normalize(surface) != key:
                    data["aliases"].add(surface)
                data["frequency"] += 1
                data["clusters"][triple.cluster_id] += 1
                data["documents"].add(triple.document_id)

            edge_key = (subject_key, object_key, triple.predicate.lower())
            edge = edge_map.setdefault(
                edge_key,
                {
                    "subject": subject_key,
                    "object": object_key,
                    "predicate": triple.predicate,
                    "weight": 0.0,
                    "cluster_id": triple.cluster_id,
                    "documents": set(),
                    "evidence": [],
                },
            )
            edge["weight"] += 1.0
            edge["documents"].add(triple.document_id)
            if len(edge["evidence"]) < 3:
                edge["evidence"].append(
                    {
                        "text": f"{triple.subject} {triple.predicate} {triple.object}",
                        "document": triple.document_title,
                    }
                )

        for key, data in entities.items():
            data["label"] = data["labels"].most_common(1)[0][0]
            data["main_cluster"] = data["clusters"].most_common(1)[0][0]
            data["cluster_count"] = len(data["clusters"])

        return entities, list(edge_map.values())

    @transaction.atomic
    def _persist(self, entities, mapping, edges, clusters, duration) -> KnowledgeGraphRun:
        """Single short transaction: everything above is computed in memory.

        SQLite has no WAL on the Django connection, and this runs in a Celery
        worker while the web process serves reads — a long write would lock it.
        """
        cluster_by_id = {str(c.id): c for c in clusters}

        run = KnowledgeGraphRun.objects.create(
            tenant=self.tenant,
            project=self.project,
            analysis_job=self.job,
            source=self.config.get("source", "extract"),
            config_snapshot=self.config,
            entity_count=len(entities),
            relation_count=len(edges),
            inferred_count=sum(1 for e in edges if e.get("inferred")),
            cluster_count=len(clusters),
            bridge_count=sum(1 for d in entities.values() if d["cluster_count"] > 1),
            duration_seconds=round(duration, 2),
        )

        rows = []
        for key, data in entities.items():
            aliases = sorted(data["aliases"])
            rows.append(
                KGEntity(
                    tenant=self.tenant,
                    project=self.project,
                    run=run,
                    canonical=key[:300],
                    label=data["label"][:300],
                    aliases=aliases[:20],
                    search_key=" ".join([key, *aliases])[:1000],
                    frequency=data["frequency"],
                    degree=data.get("degree", 0),
                    centrality=data.get("centrality", 0.0),
                    doc_count=len(data["documents"]),
                    main_cluster=cluster_by_id.get(data["main_cluster"]),
                    cluster_count=data["cluster_count"],
                    pos_x=data.get("pos", (0.0, 0.0))[0],
                    pos_y=data.get("pos", (0.0, 0.0))[1],
                )
            )
        KGEntity.objects.bulk_create(rows, batch_size=1000)

        by_canonical = {e.canonical: e for e in KGEntity.objects.filter(run=run)}

        relations = []
        doc_links = []
        through = KGRelation.documents.through
        for edge in edges:
            subject = by_canonical.get(edge["subject"][:300])
            obj = by_canonical.get(edge["object"][:300])
            if subject is None or obj is None:
                continue
            relation = KGRelation(
                tenant=self.tenant,
                project=self.project,
                run=run,
                subject=subject,
                object=obj,
                predicate=edge["predicate"][:120],
                weight=edge["weight"],
                confidence=edge.get("confidence", 1.0),
                cluster=cluster_by_id.get(edge["cluster_id"]),
                inferred=edge.get("inferred", False),
                inference_kind=edge.get("inference_kind", ""),
                evidence=edge["evidence"],
            )
            relations.append(relation)
            doc_links.extend(
                through(kgrelation_id=relation.id, document_id=doc_id)
                for doc_id in edge["documents"]
            )
        KGRelation.objects.bulk_create(relations, batch_size=1000)
        through.objects.bulk_create(doc_links, batch_size=2000, ignore_conflicts=True)

        run.relation_count = len(relations)
        run.save(update_fields=["relation_count"])
        logger.info(
            "[kg] Persisted %d entities, %d relations (%d bridges) in %.1fs",
            run.entity_count,
            run.relation_count,
            run.bridge_count,
            duration,
        )
        return run
