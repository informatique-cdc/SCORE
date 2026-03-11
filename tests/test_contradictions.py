"""Tests for analysis.contradictions — ContradictionDetector."""

import json
from datetime import timedelta
from unittest.mock import MagicMock

import numpy as np
import pytest
from django.utils import timezone

from analysis.contradictions import ContradictionDetector
from analysis.models import Claim
from tests.conftest import make_chunk, make_document, make_llm_response, random_embedding


def _make_detector(tenant, analysis_job, project, **overrides):
    """Bypass __init__ and wire up a ContradictionDetector with mocked deps."""
    det = ContradictionDetector.__new__(ContradictionDetector)
    det.tenant = tenant
    det.job = analysis_job
    det.project = project
    det.llm = MagicMock()
    det.vec_store = MagicMock()
    det.on_progress = None
    det.config = overrides.get("config", {})
    det.confidence_threshold = overrides.get("confidence_threshold", 0.75)
    det.similarity_threshold = overrides.get("similarity_threshold", 0.70)
    det.max_neighbors = overrides.get("max_neighbors", 10)
    det.staleness_days = overrides.get("staleness_days", 180)
    det.authority_rules = overrides.get(
        "authority_rules",
        {
            "source_weights": {"generic": 0.5},
            "recency_bias": True,
        },
    )
    return det


def _create_claim_pair(tenant, project, connector, analysis_job, *, same_doc=False):
    """Create 2 claims (on different docs by default) with embeddings."""
    doc_a = make_document(tenant, project, connector, title="Doc A")
    doc_b = doc_a if same_doc else make_document(tenant, project, connector, title="Doc B")
    chunk_a = make_chunk(tenant, doc_a, 0, "Policy X is active.")
    chunk_b = make_chunk(tenant, doc_b, 1 if same_doc else 0, "Policy X is inactive.")

    claim_a = Claim.objects.create(
        tenant=tenant,
        project=project,
        document=doc_a,
        chunk=chunk_a,
        subject="Policy X",
        predicate="is",
        object_value="active",
        raw_text="Policy X is active.",
        has_embedding=True,
    )
    claim_b = Claim.objects.create(
        tenant=tenant,
        project=project,
        document=doc_b,
        chunk=chunk_b,
        subject="Policy X",
        predicate="is",
        object_value="inactive",
        raw_text="Policy X is inactive.",
        has_embedding=True,
    )
    return claim_a, claim_b


# ---------------------------------------------------------------------------
# Run method tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestContradictionDetectorRun:
    def test_finds_contradictions(self, tenant, project, connector, analysis_job):
        claim_a, claim_b = _create_claim_pair(tenant, project, connector, analysis_job)

        det = _make_detector(
            tenant, analysis_job, project, similarity_threshold=0.5, max_neighbors=1
        )
        # Use similar embeddings so they pass the threshold
        base_vec = random_embedding()
        noise = np.random.randn(1536).astype(np.float32) * 0.01
        similar_vec = base_vec + noise
        similar_vec = similar_vec / (np.linalg.norm(similar_vec) + 1e-10)

        det.vec_store.get_all_claim_embeddings_for_tenant.return_value = {
            str(claim_a.id): base_vec,
            str(claim_b.id): similar_vec,
        }
        det.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response(
                json.dumps(
                    {
                        "classification": "contradiction",
                        "confidence": 0.95,
                        "severity": "high",
                        "evidence": "Claims directly conflict.",
                    }
                )
            ),
        ]

        results = det.run()

        assert len(results) == 1
        assert results[0].classification == "contradiction"
        assert results[0].confidence == 0.95

    def test_skips_same_document_claims(self, tenant, project, connector, analysis_job):
        claim_a, claim_b = _create_claim_pair(
            tenant,
            project,
            connector,
            analysis_job,
            same_doc=True,
        )

        det = _make_detector(
            tenant, analysis_job, project, similarity_threshold=0.0, max_neighbors=1
        )
        base_vec = random_embedding()
        det.vec_store.get_all_claim_embeddings_for_tenant.return_value = {
            str(claim_a.id): base_vec,
            str(claim_b.id): base_vec.copy(),
        }

        results = det.run()

        assert len(results) == 0
        det.llm.chat_batch_or_concurrent.assert_not_called()

    def test_fewer_than_2_claims_returns_empty(self, tenant, project, connector, analysis_job):
        doc = make_document(tenant, project, connector, title="Solo")
        chunk = make_chunk(tenant, doc, 0, "Only one claim.")
        Claim.objects.create(
            tenant=tenant,
            project=project,
            document=doc,
            chunk=chunk,
            subject="x",
            predicate="y",
            object_value="z",
            raw_text="Only one.",
            has_embedding=True,
        )

        det = _make_detector(tenant, analysis_job, project)
        results = det.run()
        assert results == []

    def test_below_similarity_threshold_skipped(self, tenant, project, connector, analysis_job):
        claim_a, claim_b = _create_claim_pair(tenant, project, connector, analysis_job)

        det = _make_detector(
            tenant, analysis_job, project, similarity_threshold=0.99, max_neighbors=1
        )
        # Orthogonal vectors
        vec_a = np.zeros(1536, dtype=np.float32)
        vec_a[0] = 1.0
        vec_b = np.zeros(1536, dtype=np.float32)
        vec_b[1] = 1.0

        det.vec_store.get_all_claim_embeddings_for_tenant.return_value = {
            str(claim_a.id): vec_a,
            str(claim_b.id): vec_b,
        }

        results = det.run()

        assert len(results) == 0
        det.llm.chat_batch_or_concurrent.assert_not_called()

    def test_low_confidence_skipped(self, tenant, project, connector, analysis_job):
        claim_a, claim_b = _create_claim_pair(tenant, project, connector, analysis_job)

        det = _make_detector(
            tenant,
            analysis_job,
            project,
            confidence_threshold=0.9,
            similarity_threshold=0.0,
            max_neighbors=1,
        )
        base_vec = random_embedding()
        det.vec_store.get_all_claim_embeddings_for_tenant.return_value = {
            str(claim_a.id): base_vec,
            str(claim_b.id): base_vec.copy(),
        }
        det.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response(
                json.dumps(
                    {
                        "classification": "contradiction",
                        "confidence": 0.3,
                    }
                )
            ),
        ]

        results = det.run()
        assert len(results) == 0

    def test_unrelated_classification_skipped(self, tenant, project, connector, analysis_job):
        claim_a, claim_b = _create_claim_pair(tenant, project, connector, analysis_job)

        det = _make_detector(
            tenant, analysis_job, project, similarity_threshold=0.0, max_neighbors=1
        )
        base_vec = random_embedding()
        det.vec_store.get_all_claim_embeddings_for_tenant.return_value = {
            str(claim_a.id): base_vec,
            str(claim_b.id): base_vec.copy(),
        }
        det.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response(
                json.dumps(
                    {
                        "classification": "unrelated",
                        "confidence": 0.95,
                    }
                )
            ),
        ]

        results = det.run()
        assert len(results) == 0

    def test_malformed_json_skipped(self, tenant, project, connector, analysis_job):
        claim_a, claim_b = _create_claim_pair(tenant, project, connector, analysis_job)

        det = _make_detector(
            tenant, analysis_job, project, similarity_threshold=0.0, max_neighbors=1
        )
        base_vec = random_embedding()
        det.vec_store.get_all_claim_embeddings_for_tenant.return_value = {
            str(claim_a.id): base_vec,
            str(claim_b.id): base_vec.copy(),
        }
        det.llm.chat_batch_or_concurrent.return_value = [
            make_llm_response("totally not json {{{"),
        ]

        results = det.run()
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Authority determination
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDetermineAuthority:
    def _setup(self, tenant, project, connector, analysis_job):
        claim_a, claim_b = _create_claim_pair(tenant, project, connector, analysis_job)
        det = _make_detector(tenant, analysis_job, project)
        return det, claim_a, claim_b

    def test_newer_label_b(self, tenant, project, connector, analysis_job):
        det, claim_a, claim_b = self._setup(tenant, project, connector, analysis_job)
        result = det._determine_authority(claim_a, claim_b, newer_label="B")
        assert result == claim_b

    def test_newer_label_a(self, tenant, project, connector, analysis_job):
        det, claim_a, claim_b = self._setup(tenant, project, connector, analysis_job)
        result = det._determine_authority(claim_a, claim_b, newer_label="A")
        assert result == claim_a

    def test_fallback_recency(self, tenant, project, connector, analysis_job):
        doc_a = make_document(tenant, project, connector, title="Old Doc")
        doc_b = make_document(tenant, project, connector, title="New Doc")
        now = timezone.now()
        doc_a.source_modified_at = now - timedelta(days=365)
        doc_a.save()
        doc_b.source_modified_at = now
        doc_b.save()

        chunk_a = make_chunk(tenant, doc_a, 0, "Old fact.")
        chunk_b = make_chunk(tenant, doc_b, 0, "New fact.")
        claim_a = Claim.objects.create(
            tenant=tenant,
            project=project,
            document=doc_a,
            chunk=chunk_a,
            subject="x",
            predicate="y",
            object_value="z",
            raw_text="Old.",
            has_embedding=True,
        )
        claim_b = Claim.objects.create(
            tenant=tenant,
            project=project,
            document=doc_b,
            chunk=chunk_b,
            subject="x",
            predicate="y",
            object_value="w",
            raw_text="New.",
            has_embedding=True,
        )

        det = _make_detector(tenant, analysis_job, project)
        result = det._determine_authority(claim_a, claim_b, newer_label=None)
        assert result == claim_b

    def test_no_winner(self, tenant, project, connector, analysis_job):
        det, claim_a, claim_b = self._setup(tenant, project, connector, analysis_job)
        # Both docs have same created_at (auto_now_add), no source_modified_at, no label
        det.authority_rules = {"source_weights": {"generic": 0.5}, "recency_bias": False}
        result = det._determine_authority(claim_a, claim_b, newer_label=None)
        assert result is None


# ---------------------------------------------------------------------------
# Severity adjustment
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSeverityAdjustment:
    def test_escalation_for_stale_doc(self, tenant, project, connector, analysis_job):
        doc_a = make_document(tenant, project, connector, title="Stale")
        doc_a.source_modified_at = timezone.now() - timedelta(days=365)
        doc_a.save()
        doc_b = make_document(tenant, project, connector, title="Fresh")
        doc_b.source_modified_at = timezone.now()
        doc_b.save()

        chunk_a = make_chunk(tenant, doc_a, 0, "Stale claim.")
        chunk_b = make_chunk(tenant, doc_b, 0, "Fresh claim.")
        claim_a = Claim.objects.create(
            tenant=tenant,
            project=project,
            document=doc_a,
            chunk=chunk_a,
            subject="x",
            predicate="y",
            object_value="z",
            raw_text="Stale.",
        )
        claim_b = Claim.objects.create(
            tenant=tenant,
            project=project,
            document=doc_b,
            chunk=chunk_b,
            subject="x",
            predicate="y",
            object_value="w",
            raw_text="Fresh.",
        )

        det = _make_detector(tenant, analysis_job, project, staleness_days=180)

        assert det._adjust_severity_for_staleness(claim_a, claim_b, "low") == "medium"
        assert det._adjust_severity_for_staleness(claim_a, claim_b, "medium") == "high"

    def test_no_change_when_fresh(self, tenant, project, connector, analysis_job):
        doc_a = make_document(tenant, project, connector, title="Recent A")
        doc_a.source_modified_at = timezone.now()
        doc_a.save()
        doc_b = make_document(tenant, project, connector, title="Recent B")
        doc_b.source_modified_at = timezone.now()
        doc_b.save()

        chunk_a = make_chunk(tenant, doc_a, 0, "A claim.")
        chunk_b = make_chunk(tenant, doc_b, 0, "B claim.")
        claim_a = Claim.objects.create(
            tenant=tenant,
            project=project,
            document=doc_a,
            chunk=chunk_a,
            subject="x",
            predicate="y",
            object_value="z",
            raw_text="A.",
        )
        claim_b = Claim.objects.create(
            tenant=tenant,
            project=project,
            document=doc_b,
            chunk=chunk_b,
            subject="x",
            predicate="y",
            object_value="w",
            raw_text="B.",
        )

        det = _make_detector(tenant, analysis_job, project, staleness_days=180)
        assert det._adjust_severity_for_staleness(claim_a, claim_b, "low") == "low"
        assert det._adjust_severity_for_staleness(claim_a, claim_b, "medium") == "medium"
