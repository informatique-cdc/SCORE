"""Axe 6 — Gouvernance & metadata: completeness, orphans, staleness, link graph."""
import collections
import logging
import os
from datetime import timedelta

import numpy as np
from django.utils import timezone

from .base import BaseAuditAxis

logger = logging.getLogger(__name__)


class GovernanceAxis(BaseAuditAxis):
    axis_key = "governance"
    axis_label = "Gouvernance & metadata"

    def analyze(self):
        from connectors.models import ConnectorConfig
        from ingestion.models import Document

        docs = list(
            Document.objects.filter(project=self.project, status="ready")
            .select_related("connector")
            .values_list(
                "id", "title", "author", "source_modified_at", "doc_type",
                "path", "source_url", "connector__name", "created_at",
            )
        )

        if not docs:
            return 100.0, {"total_docs": 0}, {}, {"message": "Aucun document"}

        cfg = self.config
        required_fields = cfg.get("required_fields", ["author", "source_modified_at", "doc_type", "path"])
        staleness_days = cfg.get("staleness_days", 180)

        total = len(docs)
        now = timezone.now()

        # 1. Metadata completeness
        field_map = {
            "author": 2,
            "source_modified_at": 3,
            "doc_type": 4,
            "path": 5,
            "source_url": 6,
        }
        field_completeness = {}
        for field in required_fields:
            idx = field_map.get(field)
            if idx is None:
                continue
            filled = sum(1 for d in docs if d[idx])
            field_completeness[field] = {
                "filled": filled,
                "total": total,
                "ratio": round(filled / total, 4),
            }
        avg_completeness = (
            sum(fc["ratio"] for fc in field_completeness.values()) / max(len(field_completeness), 1)
        )

        # Per-source completeness
        source_completeness = collections.defaultdict(lambda: {"total": 0, "filled": 0})
        for d in docs:
            source = d[7] or "Inconnu"
            source_completeness[source]["total"] += 1
            filled_count = sum(1 for field in required_fields if d[field_map.get(field, 0)])
            source_completeness[source]["filled"] += filled_count / max(len(required_fields), 1)

        source_completeness_data = [
            {
                "source": src,
                "total": data["total"],
                "avg_completeness": round(data["filled"] / data["total"], 4),
            }
            for src, data in source_completeness.items()
        ]

        # 2. Staleness
        stale_threshold = now - timedelta(days=staleness_days)
        stale_docs = []
        age_days_list = []
        for d in docs:
            mod_date = d[3] or d[8]  # source_modified_at or created_at
            if mod_date:
                age = (now - mod_date).days
                age_days_list.append(age)
                if mod_date < stale_threshold:
                    stale_docs.append({
                        "doc_id": str(d[0]),
                        "title": d[1][:80],
                        "age_days": age,
                        "source": d[7] or "Inconnu",
                    })

        stale_ratio = len(stale_docs) / total
        freshness_score = max(0, (1 - stale_ratio) * 100)

        # 3. Orphan detection (documents without path or in no cluster)
        orphan_docs = []
        for d in docs:
            has_path = bool(d[5])
            has_url = bool(d[6])
            if not has_path and not has_url:
                orphan_docs.append({
                    "doc_id": str(d[0]),
                    "title": d[1][:80],
                    "source": d[7] or "Inconnu",
                })
        orphan_ratio = len(orphan_docs) / total
        orphan_score = max(0, (1 - orphan_ratio * 3) * 100)

        # 4. Path graph: shared path prefixes → connectivity
        path_graph, connectivity_score = self._build_path_graph(docs)

        # Score
        score = (
            0.30 * avg_completeness * 100
            + 0.25 * freshness_score
            + 0.25 * orphan_score
            + 0.20 * connectivity_score
        )

        metrics = {
            "total_docs": total,
            "field_completeness": field_completeness,
            "avg_completeness": round(avg_completeness, 4),
            "stale_count": len(stale_docs),
            "stale_ratio": round(stale_ratio, 4),
            "staleness_threshold_days": staleness_days,
            "orphan_count": len(orphan_docs),
            "orphan_ratio": round(orphan_ratio, 4),
            "connectivity_score": round(connectivity_score, 1),
            "sub_scores": {
                "completeness": round(avg_completeness * 100, 1),
                "freshness": round(freshness_score, 1),
                "orphans": round(orphan_score, 1),
                "connectivity": round(connectivity_score, 1),
            },
        }

        # Chart data
        # Completeness per field
        completeness_bar = [
            {"field": field, "ratio": data["ratio"], "filled": data["filled"], "total": data["total"]}
            for field, data in field_completeness.items()
        ]

        # Age distribution
        age_hist = self._histogram(age_days_list, bins=15) if age_days_list else []

        # Problems by source (pareto)
        source_problems = collections.Counter()
        for d in stale_docs:
            source_problems[d["source"]] += 1
        for d in orphan_docs:
            source_problems[d["source"]] += 1
        for field, data in field_completeness.items():
            missing = data["total"] - data["filled"]
            if missing > 0:
                for d in docs:
                    source = d[7] or "Inconnu"
                    idx = field_map.get(field)
                    if idx and not d[idx]:
                        source_problems[source] += 1

        pareto = [
            {"source": src, "problems": cnt}
            for src, cnt in source_problems.most_common(20)
        ]

        chart_data = {
            "completeness_bar": completeness_bar,
            "source_completeness": source_completeness_data,
            "age_histogram": age_hist,
            "pareto_by_source": pareto,
            "path_graph": path_graph,
        }

        details = {
            "stale_docs": stale_docs[:50],
            "orphan_docs": orphan_docs[:50],
        }

        return score, metrics, chart_data, details

    def _build_path_graph(self, docs):
        """Build a simple graph from shared path prefixes."""
        paths = {}
        for d in docs:
            if d[5]:  # path
                paths[str(d[0])] = d[5]

        if len(paths) < 2:
            return {"nodes": [], "edges": []}, 50.0

        # Group by path prefix (first 2 segments)
        prefix_groups = collections.defaultdict(list)
        for doc_id, path in paths.items():
            parts = path.replace("\\", "/").split("/")
            prefix = "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
            prefix_groups[prefix].append(doc_id)

        nodes = []
        edges = []
        node_set = set()

        for prefix, doc_ids in prefix_groups.items():
            if prefix not in node_set:
                nodes.append({"id": prefix, "type": "prefix", "count": len(doc_ids)})
                node_set.add(prefix)

        # Edges between prefixes that share parent
        prefix_list = list(prefix_groups.keys())
        for i in range(len(prefix_list)):
            for j in range(i + 1, len(prefix_list)):
                p1 = prefix_list[i].split("/")
                p2 = prefix_list[j].split("/")
                if p1[0] == p2[0]:  # Same top-level
                    edges.append({
                        "source": prefix_list[i],
                        "target": prefix_list[j],
                        "weight": 1,
                    })

        # Connectivity: fraction of docs in connected components > 1
        if not edges:
            connectivity = 30.0
        else:
            total_connected = sum(
                len(ids) for ids in prefix_groups.values() if len(ids) > 1
            )
            connectivity = min(100, (total_connected / len(paths)) * 100)

        graph = {"nodes": nodes[:100], "edges": edges[:200]}
        return graph, connectivity

    def _histogram(self, values, bins=15):
        if not values:
            return []
        mn, mx = min(values), max(values)
        if mn == mx:
            return [{"bin_start": mn, "bin_end": mx, "count": len(values)}]
        step = (mx - mn) / bins
        result = []
        for i in range(bins):
            lo = mn + i * step
            hi = mn + (i + 1) * step
            cnt = sum(1 for v in values if lo <= v < hi) if i < bins - 1 else sum(1 for v in values if lo <= v <= hi)
            result.append({"bin_start": round(lo, 1), "bin_end": round(hi, 1), "count": cnt})
        return result
