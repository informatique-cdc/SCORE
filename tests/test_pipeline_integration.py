"""Integration tests for the analysis pipeline with mocked LLM/vector store."""
import importlib
import json
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_chunk, make_document, make_llm_response, random_embedding


# ---------------------------------------------------------------------------
# Mock factories
# ---------------------------------------------------------------------------

def _mock_llm_client():
    """Build a mock LLMClient that returns valid JSON for every phase."""
    llm = MagicMock()
    llm._trace = None
    llm._trace_local = MagicMock()
    llm._trace_local.trace = None

    def _embed(texts, on_progress=None):
        vecs = [random_embedding().tolist() for _ in texts]
        if on_progress:
            on_progress(len(texts), len(texts))
        return vecs

    llm.embed.side_effect = _embed

    def _chat(user_message, system="", temperature=0.0, max_tokens=4096, json_mode=False):
        return make_llm_response(json.dumps({
            "taxonomy": [{"category": "General", "clusters": [0]}],
        }))

    llm.chat.side_effect = _chat

    def _chat_batch(prompts, system="", temperature=0.0, max_tokens=4096,
                    json_mode=False, max_workers=None, on_progress=None):
        responses = []
        for i, prompt in enumerate(prompts):
            lower_sys = system.lower()
            prompt.lower() if prompt else ""
            if "doublon" in lower_sys or "duplicate" in lower_sys:
                resp = make_llm_response(json.dumps({
                    "results": [{"pair_index": 0, "classification": "duplicate",
                                 "confidence": 0.95, "evidence": "Same content.",
                                 "recommended_action": "merge"}],
                }))
            elif "affirmation" in lower_sys or "claim" in lower_sys:
                resp = make_llm_response(json.dumps({
                    "claims": [{
                        "subject": "System",
                        "predicate": "supports",
                        "object": "feature X",
                        "qualifiers": {},
                        "date": None,
                        "raw_text": "The system supports feature X.",
                    }],
                }))
            elif "contradiction" in lower_sys:
                resp = make_llm_response(json.dumps({
                    "classification": "unrelated",
                    "confidence": 0.3,
                    "evidence": "No contradiction found.",
                    "severity": "low",
                }))
            elif "cluster" in lower_sys or "résumé" in lower_sys or "summary" in lower_sys:
                resp = make_llm_response(json.dumps({
                    "label": f"Cluster {i}",
                    "summary": "A topic cluster about documents.",
                    "key_concepts": ["concept_a", "concept_b"],
                    "content_purpose": "Technical documentation",
                }))
            elif "question" in lower_sys:
                resp = make_llm_response(json.dumps({
                    "questions": [
                        {"question": "What is covered?", "importance": "medium"},
                    ],
                }))
            elif "couverture" in lower_sys or "coverage" in lower_sys:
                resp = make_llm_response(json.dumps({
                    "answered": True, "confidence": 0.9,
                    "explanation": "Well covered.",
                }))
            elif "adjacent" in lower_sys or "gap" in lower_sys:
                resp = make_llm_response(json.dumps({"has_gap": False}))
            else:
                # Default: return claims (ClaimsExtractor has no system prompt)
                resp = make_llm_response(json.dumps({
                    "claims": [{
                        "subject": "System",
                        "predicate": "supports",
                        "object": "feature X",
                        "qualifiers": {},
                        "date": None,
                        "raw_text": "The system supports feature X.",
                    }],
                }))
            responses.append(resp)
        if on_progress:
            on_progress(len(prompts), len(prompts))
        return responses

    llm.chat_batch_or_concurrent.side_effect = _chat_batch
    llm.chat_concurrent.side_effect = _chat_batch
    return llm


def _mock_vec_store():
    """Build a mock VectorStore with working search/embed ops."""
    vs = MagicMock()
    vs._trace = None
    vs._trace_local = MagicMock()
    vs._trace_local.trace = None

    vs.get_chunk_embeddings_batch.side_effect = (
        lambda chunk_ids: {cid: random_embedding() for cid in chunk_ids}
    )
    vs.get_all_vectors_for_tenant.return_value = []
    vs.search_batch.side_effect = (
        lambda query_vectors, tenant_id, k=10, project_id=None: [[] for _ in query_vectors]
    )
    vs.search_claims.return_value = []
    vs.upsert_claims_batch.return_value = None
    vs.get_all_claim_embeddings_for_tenant.return_value = {}
    return vs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PATCH_MODULES = [
    "analysis.duplicates",
    "analysis.claims",
    "analysis.clustering",
    "analysis.contradictions",
    "analysis.gaps",
    "llm.client",
    "vectorstore.store",
]


@pytest.fixture
def mock_llm_vs(monkeypatch):
    """Patch get_llm_client / get_vector_store in all analysis modules."""
    llm = _mock_llm_client()
    vs = _mock_vec_store()

    for mod_path in _PATCH_MODULES:
        mod = importlib.import_module(mod_path)
        if hasattr(mod, "get_llm_client"):
            monkeypatch.setattr(mod, "get_llm_client", lambda: llm)
        if hasattr(mod, "get_vector_store"):
            monkeypatch.setattr(mod, "get_vector_store", lambda: vs)

    return llm, vs


@pytest.fixture
def pipeline_data(tenant, project, connector):
    """Create documents + chunks for a full pipeline run."""
    docs = []
    for i in range(3):
        doc = make_document(
            tenant, project, connector,
            title=f"Document {i}",
            source_url=f"https://example.com/doc{i}",
        )
        make_chunk(tenant, doc, index=0, content=f"Content of document {i}, section A.")
        make_chunk(tenant, doc, index=1, content=f"Content of document {i}, section B.")
        docs.append(doc)
    return docs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestAnalysisPipelineIntegration:
    """End-to-end test of run_analysis_phases with mocked LLM + vector store."""

    def test_full_analysis_pipeline(self, mock_llm_vs, pipeline_data,
                                    tenant, project, analysis_job):
        """Run all 7 analysis phases and verify DB results."""
        from analysis.models import AnalysisJob
        from analysis.pipeline import run_analysis_phases

        mock_llm, mock_vs = mock_llm_vs

        stats = run_analysis_phases(analysis_job, collector=None)

        assert isinstance(stats, dict)
        for key in ("dup_groups", "claims", "contradictions", "clusters", "gaps"):
            assert key in stats

        analysis_job.refresh_from_db()
        assert analysis_job.current_phase == AnalysisJob.Phase.CONTRADICTIONS

        assert mock_llm.chat_batch_or_concurrent.called or mock_llm.chat_concurrent.called

    def test_pipeline_creates_claims(self, mock_llm_vs, pipeline_data,
                                     tenant, project, analysis_job):
        """Verify pipeline creates claim records."""
        from analysis.models import Claim
        from analysis.pipeline import run_analysis_phases

        run_analysis_phases(analysis_job, collector=None)

        claim_count = Claim.objects.filter(project=project).count()
        assert claim_count > 0, "Claims extraction should create claims"

    def test_pipeline_with_trace_collector(self, mock_llm_vs, pipeline_data,
                                           tenant, project, analysis_job):
        """Pipeline records phase traces when collector is provided."""
        from django.utils import timezone

        from analysis.models import PhaseTrace, PipelineTrace
        from analysis.pipeline import run_analysis_phases
        from analysis.trace import TraceCollector

        pipeline_trace = PipelineTrace.objects.create(
            tenant=tenant, project=project,
            analysis_job=analysis_job, started_at=timezone.now(),
        )
        collector = TraceCollector(pipeline_trace)

        run_analysis_phases(analysis_job, collector=collector)
        collector.finalize()

        phases = PhaseTrace.objects.filter(pipeline_trace=pipeline_trace)
        assert phases.count() >= 5, f"Expected >=5 phase traces, got {phases.count()}"

        pipeline_trace.refresh_from_db()
        assert pipeline_trace.completed_at is not None

    def test_pipeline_resume_from_clustering(self, mock_llm_vs, pipeline_data,
                                              tenant, project, analysis_job):
        """Resume skips earlier phases and runs from the specified phase."""
        from analysis.models import AnalysisJob
        from analysis.pipeline import run_analysis_phases

        analysis_job.current_phase = AnalysisJob.Phase.CLUSTERING
        analysis_job.save()

        stats = run_analysis_phases(analysis_job, collector=None, resume_from="clustering")

        assert isinstance(stats, dict)
        analysis_job.refresh_from_db()
        assert analysis_job.current_phase == AnalysisJob.Phase.CONTRADICTIONS

    def test_pipeline_phase_failure_propagates(self, mock_llm_vs, pipeline_data,
                                                tenant, project, analysis_job):
        """If a phase raises, the exception propagates."""
        from analysis.pipeline import run_analysis_phases

        mock_llm, _ = mock_llm_vs
        mock_llm.chat_batch_or_concurrent.side_effect = RuntimeError("LLM unavailable")

        with pytest.raises(RuntimeError, match="LLM unavailable"):
            run_analysis_phases(analysis_job, collector=None)


@pytest.mark.django_db(transaction=True)
class TestAuditPipelineIntegration:
    """End-to-end test of run_audit_phases with mocked audit axes."""

    def test_audit_phases_create_audit_job(self, tenant, project, analysis_job):
        """Audit phases create an AuditJob with scores."""
        from analysis.models import AuditAxisResult, AuditJob
        from analysis.pipeline import run_audit_phases

        mock_axis_instance = MagicMock()
        mock_axis_instance.execute.return_value = (
            75.0, {"metric": 1}, {"chart": []}, {"detail": "ok"}, 0.5
        )

        mock_mod = MagicMock()
        for attr in ("HygieneAxis", "StructureAxis", "CoverageAxis",
                     "CoherenceAxis", "RetrievabilityAxis", "GovernanceAxis"):
            getattr(mock_mod, attr).return_value = mock_axis_instance

        with patch("importlib.import_module", return_value=mock_mod):
            audit_job = run_audit_phases(analysis_job, collector=None)

        assert audit_job is not None
        assert audit_job.status == AuditJob.Status.COMPLETED
        assert audit_job.overall_score > 0
        assert audit_job.overall_grade in ("A", "B", "C", "D", "E")
        assert audit_job.completed_at is not None

        axis_count = AuditAxisResult.objects.filter(audit_job=audit_job).count()
        assert axis_count == 6, f"Expected 6 axis results, got {axis_count}"

    def test_audit_grade_boundaries(self):
        """Verify grade() returns correct grades."""
        from score.scoring import grade

        assert grade(85) == "A"
        assert grade(80) == "A"
        assert grade(79) == "B"
        assert grade(60) == "B"
        assert grade(59) == "C"
        assert grade(40) == "C"
        assert grade(39) == "D"
        assert grade(20) == "D"
        assert grade(19) == "E"
        assert grade(0) == "E"
