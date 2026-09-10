"""Tests for analysis.gaps — GapDetector."""

import json
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

pytest.importorskip("openai", reason="openai not installed")

from analysis.gaps import GapDetector, flatten_text  # noqa: E402
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

    def test_detector_does_not_invent_a_coverage_score(self, tenant, project, analysis_job):
        """Un nombre de documents n'est pas une couverture ; _measure_coverage tranche."""
        cluster = _make_cluster(tenant, project, analysis_job, label="Small", doc_count=2)
        det = _make_detector(tenant, analysis_job, project, orphan_max_size=2)

        gaps = det._orphan_topics([cluster])
        assert len(gaps) == 1
        assert gaps[0].coverage_score is None
        assert gaps[0].evidence["doc_count"] == 2

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


# ---------------------------------------------------------------------------
# Coverage measurement
# ---------------------------------------------------------------------------


def _gap(tenant, project, analysis_job, gap_type, **kwargs):
    return GapReport.objects.create(
        tenant=tenant,
        project=project,
        analysis_job=analysis_job,
        gap_type=gap_type,
        title=kwargs.pop("title", "Titre"),
        description=kwargs.pop("description", "Description"),
        severity="medium",
        **kwargs,
    )


@pytest.mark.django_db
class TestProbe:
    """Chaque stratégie vise autre chose ; la sonde doit interroger le bon sujet."""

    def test_concept_island_probes_its_concepts(self, tenant, project, analysis_job):
        det = _make_detector(tenant, analysis_job, project)
        gap = _gap(
            tenant,
            project,
            analysis_job,
            GapReport.GapType.CONCEPT_ISLAND,
            evidence={"concepts": ["ayant droit", "orphelin"]},
        )

        assert det._probe(gap) == "ayant droit, orphelin"

    def test_weak_bridge_probes_both_ends(self, tenant, project, analysis_job):
        det = _make_detector(tenant, analysis_job, project)
        gap = _gap(
            tenant,
            project,
            analysis_job,
            GapReport.GapType.WEAK_BRIDGE,
            evidence={"bridge": ["PASS", "plafond"]},
        )

        assert det._probe(gap) == "PASS plafond"

    def test_cluster_gap_probes_the_label_not_the_prefixed_title(
        self, tenant, project, analysis_job
    ):
        """« Zone obsolète : X » fausserait la sonde ; le libellé du cluster nomme le sujet."""
        cluster = _make_cluster(tenant, project, analysis_job, label="Cotisations")
        det = _make_detector(tenant, analysis_job, project)
        gap = _gap(
            tenant,
            project,
            analysis_job,
            GapReport.GapType.STALE_AREA,
            title="Zone obsolète : Cotisations",
            related_cluster=cluster,
        )

        assert det._probe(gap) == "Cotisations"

    def test_missing_topic_probes_the_suggested_title(self, tenant, project, analysis_job):
        cluster = _make_cluster(tenant, project, analysis_job, label="Retraite")
        det = _make_detector(tenant, analysis_job, project)
        gap = _gap(
            tenant,
            project,
            analysis_job,
            GapReport.GapType.MISSING_TOPIC,
            title="Cumul emploi-retraite à l'étranger",
            related_cluster=cluster,
        )

        assert det._probe(gap) == "Cumul emploi-retraite à l'étranger"


@pytest.mark.django_db
class TestCoverageFrom:
    """L'échelle réutilise les bornes que QG/RAG applique déjà."""

    def test_silent_corpus_scores_zero(self, tenant, project, analysis_job):
        det = _make_detector(tenant, analysis_job, project)
        assert det._coverage_from([{"similarity": 0.20}]) == 0.0

    def test_answering_corpus_saturates_at_one(self, tenant, project, analysis_job):
        det = _make_detector(tenant, analysis_job, project)
        assert det._coverage_from([{"similarity": 0.95}]) == 1.0

    def test_midpoint_lands_mid_scale(self, tenant, project, analysis_job):
        det = _make_detector(tenant, analysis_job, project)
        midpoint = (0.35 + 0.82) / 2

        assert det._coverage_from([{"similarity": midpoint}]) == pytest.approx(0.5, abs=1e-3)

    def test_one_close_passage_does_not_make_a_coverage(self, tenant, project, analysis_job):
        """La moyenne sur trois empêche un extrait chanceux de porter le score."""
        det = _make_detector(tenant, analysis_job, project)

        lone = det._coverage_from(
            [{"similarity": 0.95}, {"similarity": 0.30}, {"similarity": 0.30}]
        )
        broad = det._coverage_from(
            [{"similarity": 0.95}, {"similarity": 0.90}, {"similarity": 0.88}]
        )

        assert lone < broad

    def test_no_hits_scores_zero(self, tenant, project, analysis_job):
        det = _make_detector(tenant, analysis_job, project)
        assert det._coverage_from([]) == 0.0


@pytest.mark.django_db
class TestMeasureCoverage:
    def test_writes_a_measured_score_to_the_database(self, tenant, project, analysis_job):
        det = _make_detector(tenant, analysis_job, project)
        gap = _gap(
            tenant,
            project,
            analysis_job,
            GapReport.GapType.CONCEPT_ISLAND,
            evidence={"concepts": ["ayant droit"]},
        )
        det.llm.embed.return_value = [random_embedding().tolist()]
        det.vec_store.search_batch.return_value = [[{"similarity": 0.82}]]

        det._measure_coverage([gap])

        gap.refresh_from_db()
        assert gap.coverage_score == 1.0

    def test_leaves_the_llm_measured_type_alone(self, tenant, project, analysis_job):
        """LOW_COVERAGE mesure déjà cela, question par question et vérifié."""
        det = _make_detector(tenant, analysis_job, project)
        gap = _gap(
            tenant,
            project,
            analysis_job,
            GapReport.GapType.LOW_COVERAGE,
            coverage_score=0.0,
        )

        det._measure_coverage([gap])

        gap.refresh_from_db()
        assert gap.coverage_score == 0.0
        det.llm.embed.assert_not_called()

    def test_a_failed_measurement_keeps_the_findings(self, tenant, project, analysis_job):
        """Les constats sont en base : une panne d'embedding ne doit pas couler la phase."""
        det = _make_detector(tenant, analysis_job, project)
        gap = _gap(
            tenant,
            project,
            analysis_job,
            GapReport.GapType.CONCEPT_ISLAND,
            evidence={"concepts": ["ayant droit"]},
        )
        det.llm.embed.side_effect = RuntimeError("embedding endpoint down")

        det._measure_coverage([gap])

        gap.refresh_from_db()
        assert gap.coverage_score is None

    def test_gaps_without_a_subject_are_skipped(self, tenant, project, analysis_job):
        det = _make_detector(tenant, analysis_job, project)
        gap = _gap(tenant, project, analysis_job, GapReport.GapType.CONCEPT_ISLAND, evidence={})

        det._measure_coverage([gap])

        det.llm.embed.assert_not_called()
        gap.refresh_from_db()
        assert gap.coverage_score is None


# ---------------------------------------------------------------------------
# flatten_text
# ---------------------------------------------------------------------------


class TestFlattenText:
    """Le modèle rend parfois un objet là où le prompt demande une chaîne."""

    def test_plain_string_is_kept(self):
        assert flatten_text("  Délais de traitement  ") == "Délais de traitement"

    def test_dict_keeps_the_sentences_and_drops_the_invented_keys(self):
        value = {
            "conditions_eligibilite": "Critères précis d'éligibilité.",
            "modalites_calcul": "Formule de calcul détaillée.",
        }

        assert (
            flatten_text(value) == "Critères précis d'éligibilité. ; Formule de calcul détaillée."
        )

    def test_list_is_joined(self):
        assert flatten_text(["Délais", "Interlocuteurs"]) == "Délais ; Interlocuteurs"

    def test_nested_structures_are_flattened(self):
        value = {"etapes": ["Formulaire à compléter", "Pièces à fournir"]}

        assert flatten_text(value) == "Formulaire à compléter ; Pièces à fournir"

    def test_no_python_syntax_survives(self):
        """C'est tout l'objet du correctif : plus de repr dans le titre affiché."""
        rendered = flatten_text({"a": ["x", "y"], "b": "z"})

        for token in ("{", "}", "[", "]", "'", '"'):
            assert token not in rendered

    def test_empty_parts_are_dropped(self):
        assert flatten_text({"a": "", "b": "Reste", "c": None}) == "Reste"

    def test_none_becomes_empty(self):
        assert flatten_text(None) == ""

    def test_number_is_stringified(self):
        assert flatten_text(10) == "10"


@pytest.mark.django_db
class TestQGRagTitleStaysReadable:
    def test_structured_missing_info_does_not_leak_into_the_title(
        self, tenant, project, connector, analysis_job
    ):
        cluster = _make_cluster(tenant, project, analysis_job, label="Congés")
        det = _make_detector(tenant, analysis_job, project, question_count=1)
        det.llm.chat_batch_or_concurrent.side_effect = [
            [
                make_llm_response(
                    json.dumps({"questions": [{"question": "Q1 ?", "importance": "high"}]})
                )
            ],
            [
                make_llm_response(
                    json.dumps(
                        {
                            "answered": False,
                            "confidence": 0.1,
                            "missing_info": {
                                "delais": "Délais de préavis.",
                                "etapes": ["Formulaire"],
                            },
                        }
                    )
                )
            ],
        ]
        det.llm.embed.return_value = [random_embedding().tolist()]
        det.vec_store.search_batch.return_value = [
            [{"similarity": 0.5, "chunk_id": "c", "document_id": "d"}]
        ]

        doc = make_document(tenant, project, connector, title="Doc")
        chunk = make_chunk(tenant, doc, 0, "Contenu")
        det.vec_store.search_batch.return_value = [
            [{"similarity": 0.5, "chunk_id": str(chunk.id), "document_id": str(doc.id)}]
        ]

        gaps = det._qg_rag_gaps([cluster])

        assert len(gaps) == 1
        assert gaps[0].title == "Manquant : Délais de préavis. ; Formulaire"
        assert "{" not in gaps[0].title
