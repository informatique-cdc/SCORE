"""Tests for analysis.gaps — GapDetector."""

import json
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

pytest.importorskip("openai", reason="openai not installed")

from analysis.gaps import GapDetector  # noqa: E402
from analysis.models import ClusterMembership, GapReport, TopicCluster
from tests.conftest import make_chunk, make_document, make_llm_response, random_embedding


def _make_detector(tenant, analysis_job, project, **overrides):
    """Bypass __init__ and wire up a GapDetector with mocked deps."""
    det = GapDetector.__new__(GapDetector)
    det.tenant = tenant
    det.job = analysis_job
    det.project = project
    det.nsg = overrides.get("nsg", None)
    det.on_progress = None
    det.llm = MagicMock()
    det.vec_store = MagicMock()
    det.config = overrides.get("config", {})
    det.question_count = overrides.get("question_count", 3)
    det.confidence_threshold = overrides.get("confidence_threshold", 0.5)
    det.orphan_max_size = overrides.get("orphan_max_size", 2)
    det.staleness_days = overrides.get("staleness_days", 180)
    det.sim_auto_answer = overrides.get("sim_auto_answer", 0.82)
    det.sim_auto_unanswered = overrides.get("sim_auto_unanswered", 0.35)
    return det


def _make_cluster(
    tenant, project, analysis_job, label="Cluster", doc_count=5, centroid_x=0.0, centroid_y=0.0
):
    return TopicCluster.objects.create(
        tenant=tenant,
        project=project,
        analysis_job=analysis_job,
        label=label,
        doc_count=doc_count,
        chunk_count=doc_count * 3,
        centroid_x=centroid_x,
        centroid_y=centroid_y,
    )


# ---------------------------------------------------------------------------
# Orphan topics
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOrphanTopics:
    def test_orphan_detected_for_small_cluster(self, tenant, project, analysis_job):
        cluster = _make_cluster(tenant, project, analysis_job, label="Tiny", doc_count=1)
        det = _make_detector(tenant, analysis_job, project, orphan_max_size=2)

        gaps = det._orphan_topics([cluster])

        assert len(gaps) == 1
        assert gaps[0].gap_type == GapReport.GapType.ORPHAN_TOPIC
        assert gaps[0].related_cluster == cluster

    def test_no_orphan_for_large_cluster(self, tenant, project, analysis_job):
        cluster = _make_cluster(tenant, project, analysis_job, label="Big", doc_count=10)
        det = _make_detector(tenant, analysis_job, project, orphan_max_size=2)

        gaps = det._orphan_topics([cluster])
        assert len(gaps) == 0

    def test_orphan_coverage_score(self, tenant, project, analysis_job):
        cluster = _make_cluster(tenant, project, analysis_job, label="Small", doc_count=2)
        det = _make_detector(tenant, analysis_job, project, orphan_max_size=2)

        gaps = det._orphan_topics([cluster])
        assert len(gaps) == 1
        assert gaps[0].coverage_score == pytest.approx(2.0 / 5.0)

    def test_zero_doc_count_skipped(self, tenant, project, analysis_job):
        cluster = _make_cluster(tenant, project, analysis_job, label="Empty", doc_count=0)
        det = _make_detector(tenant, analysis_job, project, orphan_max_size=2)

        gaps = det._orphan_topics([cluster])
        assert len(gaps) == 0


# ---------------------------------------------------------------------------
# Stale areas
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStaleAreas:
    def test_stale_area_detected(self, tenant, project, connector, analysis_job):
        cluster = _make_cluster(tenant, project, analysis_job, label="Stale Cluster", doc_count=5)
        stale_date = timezone.now() - timedelta(days=365)

        # Create 5 documents, 4 of which are stale (80%)
        for i in range(5):
            doc = make_document(tenant, project, connector, title=f"Doc {i}")
            doc.source_modified_at = stale_date if i < 4 else timezone.now()
            doc.save()
            chunk = make_chunk(tenant, doc, 0, f"Content {i}")
            ClusterMembership.objects.create(
                tenant=tenant,
                project=project,
                cluster=cluster,
                chunk=chunk,
                document=doc,
            )

        det = _make_detector(tenant, analysis_job, project, staleness_days=180)
        gaps = det._stale_areas([cluster])

        assert len(gaps) == 1
        assert gaps[0].gap_type == GapReport.GapType.STALE_AREA

    def test_no_stale_when_fresh(self, tenant, project, connector, analysis_job):
        cluster = _make_cluster(tenant, project, analysis_job, label="Fresh Cluster", doc_count=3)

        for i in range(3):
            doc = make_document(tenant, project, connector, title=f"Fresh {i}")
            doc.source_modified_at = timezone.now()
            doc.save()
            chunk = make_chunk(tenant, doc, 0, f"Fresh content {i}")
            ClusterMembership.objects.create(
                tenant=tenant,
                project=project,
                cluster=cluster,
                chunk=chunk,
                document=doc,
            )

        det = _make_detector(tenant, analysis_job, project, staleness_days=180)
        gaps = det._stale_areas([cluster])
        assert len(gaps) == 0

    def test_stale_severity(self, tenant, project, connector, analysis_job):
        cluster = _make_cluster(tenant, project, analysis_job, label="Very Stale", doc_count=10)
        stale_date = timezone.now() - timedelta(days=365)

        # 9/10 stale = 90% → "high"
        for i in range(10):
            doc = make_document(tenant, project, connector, title=f"Doc {i}")
            doc.source_modified_at = stale_date if i < 9 else timezone.now()
            doc.save()
            chunk = make_chunk(tenant, doc, 0, f"Content {i}")
            ClusterMembership.objects.create(
                tenant=tenant,
                project=project,
                cluster=cluster,
                chunk=chunk,
                document=doc,
            )

        det = _make_detector(tenant, analysis_job, project, staleness_days=180)
        gaps = det._stale_areas([cluster])

        assert len(gaps) == 1
        assert gaps[0].severity == "high"


# ---------------------------------------------------------------------------
# QG/RAG gaps
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQGRagGaps:
    def test_creates_gap_for_unanswered_questions(self, tenant, project, connector, analysis_job):
        cluster = _make_cluster(tenant, project, analysis_job, label="Test Cluster")

        det = _make_detector(tenant, analysis_job, project)
        # LLM generates 2 questions
        det.llm.chat_batch_or_concurrent.side_effect = [
            # Question generation
            [
                make_llm_response(
                    json.dumps(
                        {
                            "questions": [
                                {"question": "What is X?", "importance": "high"},
                                {"question": "How does Y work?", "importance": "medium"},
                            ]
                        }
                    )
                )
            ],
            # Coverage check — both unanswered
            [
                make_llm_response(
                    json.dumps(
                        {"answered": False, "confidence": 0.2, "missing_info": "Info about X"}
                    )
                ),
                make_llm_response(
                    json.dumps(
                        {"answered": False, "confidence": 0.1, "missing_info": "Info about Y"}
                    )
                ),
            ],
        ]
        det.llm.embed.return_value = [random_embedding(), random_embedding()]
        # Vector search returns results in mid-similarity range (triggers LLM check)
        det.vec_store.search_batch.return_value = [
            [
                {
                    "chunk_id": str(
                        make_chunk(
                            tenant,
                            make_document(tenant, project, connector, title="SomeDoc"),
                            0,
                            "some text",
                        ).id
                    ),
                    "document_id": str(
                        make_document(tenant, project, connector, title="SomeDoc2").id
                    ),
                    "similarity": 0.5,
                }
            ],
            [
                {
                    "chunk_id": str(
                        make_chunk(
                            tenant,
                            make_document(tenant, project, connector, title="SomeDoc3"),
                            0,
                            "more text",
                        ).id
                    ),
                    "document_id": str(
                        make_document(tenant, project, connector, title="SomeDoc4").id
                    ),
                    "similarity": 0.5,
                }
            ],
        ]

        gaps = det._qg_rag_gaps([cluster])

        assert len(gaps) == 1
        assert gaps[0].gap_type == GapReport.GapType.LOW_COVERAGE

    def test_no_gap_when_all_answered(self, tenant, project, connector, analysis_job):
        cluster = _make_cluster(tenant, project, analysis_job, label="Well Covered")

        det = _make_detector(tenant, analysis_job, project)
        det.llm.chat_batch_or_concurrent.side_effect = [
            [
                make_llm_response(
                    json.dumps({"questions": [{"question": "What is Z?", "importance": "low"}]})
                )
            ],
            [
                make_llm_response(
                    json.dumps({"answered": True, "confidence": 0.9, "missing_info": ""})
                )
            ],
        ]
        det.llm.embed.return_value = [random_embedding()]

        doc = make_document(tenant, project, connector, title="CoverDoc")
        chunk = make_chunk(tenant, doc, 0, "Z is defined as...")
        det.vec_store.search_batch.return_value = [
            [{"chunk_id": str(chunk.id), "document_id": str(doc.id), "similarity": 0.6}],
        ]

        gaps = det._qg_rag_gaps([cluster])
        assert len(gaps) == 0

    def test_auto_answer_high_similarity(self, tenant, project, connector, analysis_job):
        cluster = _make_cluster(tenant, project, analysis_job, label="Auto Answer")

        det = _make_detector(tenant, analysis_job, project, sim_auto_answer=0.82)
        det.llm.chat_batch_or_concurrent.side_effect = [
            [
                make_llm_response(
                    json.dumps({"questions": [{"question": "What is A?", "importance": "low"}]})
                )
            ],
            # Coverage check should NOT be called — empty list since all auto-answered
            [],
        ]
        det.llm.embed.return_value = [random_embedding()]

        doc = make_document(tenant, project, connector, title="HighSimDoc")
        chunk = make_chunk(tenant, doc, 0, "A is defined.")
        det.vec_store.search_batch.return_value = [
            [{"chunk_id": str(chunk.id), "document_id": str(doc.id), "similarity": 0.95}],
        ]

        gaps = det._qg_rag_gaps([cluster])
        assert len(gaps) == 0

    def test_auto_unanswered_low_similarity(self, tenant, project, connector, analysis_job):
        cluster = _make_cluster(tenant, project, analysis_job, label="Auto Unanswered")

        det = _make_detector(tenant, analysis_job, project, sim_auto_unanswered=0.35)
        det.llm.chat_batch_or_concurrent.side_effect = [
            [
                make_llm_response(
                    json.dumps({"questions": [{"question": "What is B?", "importance": "high"}]})
                )
            ],
            # Coverage check not called for auto-unanswered items — empty list
            [],
        ]
        det.llm.embed.return_value = [random_embedding()]
        # Use real UUIDs for chunk/document IDs
        import uuid as _uuid

        det.vec_store.search_batch.return_value = [
            [
                {
                    "chunk_id": str(_uuid.uuid4()),
                    "document_id": str(_uuid.uuid4()),
                    "similarity": 0.1,
                }
            ],
        ]

        gaps = det._qg_rag_gaps([cluster])
        assert len(gaps) == 1
        assert gaps[0].gap_type == GapReport.GapType.LOW_COVERAGE

    def test_empty_clusters_no_crash(self, tenant, project, analysis_job):
        det = _make_detector(tenant, analysis_job, project)
        gaps = det._qg_rag_gaps([])
        assert gaps == []


# ---------------------------------------------------------------------------
# Adjacent cluster gaps
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAdjacentClusterGaps:
    def test_adjacent_gap_detected(self, tenant, project, analysis_job):
        clusters = [
            _make_cluster(
                tenant, project, analysis_job, label=f"C{i}", centroid_x=float(i), centroid_y=0.0
            )
            for i in range(4)
        ]

        det = _make_detector(tenant, analysis_job, project)
        det.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response(
                json.dumps(
                    {
                        "has_gap": True,
                        "suggested_title": "Missing: Bridge between C0 and C1",
                        "description": "There should be a doc bridging these topics.",
                    }
                )
            )
            for _ in range(4)
        ]

        gaps = det._adjacent_cluster_gaps(clusters)
        assert len(gaps) >= 1
        assert all(g.gap_type == GapReport.GapType.MISSING_TOPIC for g in gaps)

    def test_adjacent_no_gap(self, tenant, project, analysis_job):
        clusters = [
            _make_cluster(
                tenant, project, analysis_job, label=f"C{i}", centroid_x=float(i), centroid_y=0.0
            )
            for i in range(4)
        ]

        det = _make_detector(tenant, analysis_job, project)
        det.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response(json.dumps({"has_gap": False})) for _ in range(4)
        ]

        gaps = det._adjacent_cluster_gaps(clusters)
        assert len(gaps) == 0

    def test_too_few_clusters_skipped(self, tenant, project, analysis_job):
        clusters = [
            _make_cluster(tenant, project, analysis_job, label="Only1"),
            _make_cluster(tenant, project, analysis_job, label="Only2"),
        ]

        det = _make_detector(tenant, analysis_job, project)
        gaps = det._adjacent_cluster_gaps(clusters)
        assert gaps == []


# ---------------------------------------------------------------------------
# _get_adjacent_clusters (pure logic)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetAdjacentClusters:
    def test_returns_nearest_by_centroid(self, tenant, project, analysis_job):
        clusters = [
            _make_cluster(
                tenant, project, analysis_job, label="Origin", centroid_x=0.0, centroid_y=0.0
            ),
            _make_cluster(
                tenant, project, analysis_job, label="Near", centroid_x=1.0, centroid_y=0.0
            ),
            _make_cluster(
                tenant, project, analysis_job, label="Mid", centroid_x=3.0, centroid_y=0.0
            ),
            _make_cluster(
                tenant, project, analysis_job, label="Far", centroid_x=10.0, centroid_y=0.0
            ),
            _make_cluster(
                tenant, project, analysis_job, label="VeryFar", centroid_x=20.0, centroid_y=0.0
            ),
        ]

        det = _make_detector(tenant, analysis_job, project)
        adjacent = det._get_adjacent_clusters(clusters[0], clusters)

        assert len(adjacent) == 4  # all except self, limited to 5 max
        assert adjacent[0].label == "Near"
        assert adjacent[1].label == "Mid"

    def test_skips_clusters_without_centroid(self, tenant, project, analysis_job):
        origin = _make_cluster(
            tenant, project, analysis_job, label="Origin", centroid_x=0.0, centroid_y=0.0
        )
        no_centroid = TopicCluster.objects.create(
            tenant=tenant,
            project=project,
            analysis_job=analysis_job,
            label="NoCentroid",
            doc_count=5,
            chunk_count=15,
            centroid_x=None,
            centroid_y=None,
        )

        det = _make_detector(tenant, analysis_job, project)
        adjacent = det._get_adjacent_clusters(origin, [origin, no_centroid])
        assert all(c.label != "NoCentroid" for c in adjacent)

    def test_cluster_with_no_centroid_returns_empty(self, tenant, project, analysis_job):
        no_centroid = TopicCluster.objects.create(
            tenant=tenant,
            project=project,
            analysis_job=analysis_job,
            label="NoCentroid",
            doc_count=5,
            chunk_count=15,
            centroid_x=None,
            centroid_y=None,
        )
        other = _make_cluster(
            tenant, project, analysis_job, label="Other", centroid_x=1.0, centroid_y=1.0
        )

        det = _make_detector(tenant, analysis_job, project)
        adjacent = det._get_adjacent_clusters(no_centroid, [no_centroid, other])
        assert adjacent == []
