"""Tests for dashboard/scoring.py — DocuScore computation logic."""
import pytest

from analysis.models import (
    AnalysisJob,
    ContradictionPair,
    DuplicateGroup,
    Claim,
)
from docuscore.scoring import (
    _empty_result,
    _grade,
    health_score,
    compute_docuscore,
    compute_docuscore_detail,
    compute_docuscore_for_job,
)
from ingestion.models import Document
from tests.conftest import make_chunk, make_document


# ---------------------------------------------------------------------------
# _grade
# ---------------------------------------------------------------------------


class TestGrade:
    def test_grade_a(self):
        assert _grade(100) == "A"
        assert _grade(80) == "A"

    def test_grade_b(self):
        assert _grade(79) == "B"
        assert _grade(60) == "B"

    def test_grade_c(self):
        assert _grade(59) == "C"
        assert _grade(40) == "C"

    def test_grade_d(self):
        assert _grade(39) == "D"
        assert _grade(20) == "D"

    def test_grade_e(self):
        assert _grade(19) == "E"
        assert _grade(0) == "E"

    def test_boundary_values(self):
        """Exact boundary values."""
        assert _grade(80) == "A"
        assert _grade(60) == "B"
        assert _grade(40) == "C"
        assert _grade(20) == "D"


# ---------------------------------------------------------------------------
# health_score
# ---------------------------------------------------------------------------


class TestHealthScore:
    def test_all_ready(self):
        assert health_score(10, 0, 10) == 100

    def test_no_documents(self):
        assert health_score(0, 0, 0) == 0

    def test_all_errors(self):
        score = health_score(0, 10, 10)
        assert score == 0  # 100% errors

    def test_half_ready_no_errors(self):
        score = health_score(5, 0, 10)
        assert score == 50

    def test_errors_cause_penalty(self):
        # 1 error out of 10 = 10% error rate, penalty = min(50, 50) = 50
        score = health_score(9, 1, 10)
        assert score < 100
        assert score >= 0

    def test_result_clamped_to_0_100(self):
        score = health_score(0, 100, 100)
        assert score == 0
        score = health_score(100, 0, 100)
        assert score == 100


# ---------------------------------------------------------------------------
# _empty_result
# ---------------------------------------------------------------------------


class TestEmptyResult:
    def test_no_docs_no_analysis(self):
        result = _empty_result(has_docs=False, has_analysis=False)
        assert result["grade"] == "E"
        assert result["score"] == 0
        assert result["has_docs"] is False
        assert result["has_analysis"] is False

    def test_has_docs_no_analysis(self):
        result = _empty_result(has_docs=True, has_analysis=False)
        assert result["has_docs"] is True
        assert result["has_analysis"] is False
        assert result["breakdown"]["uniqueness"] is None


# ---------------------------------------------------------------------------
# compute_docuscore
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestComputeDocuscore:
    def test_no_documents(self, project):
        result = compute_docuscore(project)
        assert result["grade"] == "E"
        assert result["score"] == 0
        assert result["has_docs"] is False

    def test_documents_no_analysis(self, tenant, project, connector):
        make_document(tenant, project, connector, title="Doc1", status="ready")
        result = compute_docuscore(project)
        assert result["has_docs"] is True
        assert result["has_analysis"] is False
        assert result["breakdown"]["health"] is not None
        assert result["breakdown"]["uniqueness"] is None

    def test_with_completed_analysis(self, tenant, project, connector):
        for i in range(5):
            make_document(tenant, project, connector, title=f"Doc{i}", status="ready")

        AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED,
        )

        result = compute_docuscore(project)
        assert result["has_docs"] is True
        assert result["has_analysis"] is True
        assert 0 <= result["score"] <= 100
        assert result["grade"] in ("A", "B", "C", "D", "E")
        assert "uniqueness" in result["breakdown"]
        assert "consistency" in result["breakdown"]
        assert "coverage" in result["breakdown"]
        assert "structure" in result["breakdown"]
        assert "health" in result["breakdown"]

    def test_duplicates_lower_score(self, tenant, project, connector):
        """Many duplicates should reduce the score."""
        for i in range(10):
            make_document(tenant, project, connector, title=f"Doc{i}", status="ready")

        job = AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED,
        )
        # Create several duplicate groups (not KEEP)
        for i in range(5):
            DuplicateGroup.objects.create(
                tenant=tenant, project=project, analysis_job=job,
                recommended_action=DuplicateGroup.Action.MERGE,
            )

        result = compute_docuscore(project)
        assert result["breakdown"]["uniqueness"] < 100

    def test_contradictions_lower_score(self, tenant, project, connector):
        """High-severity contradictions should reduce consistency."""
        for i in range(5):
            doc = make_document(tenant, project, connector, title=f"Doc{i}", status="ready")
            make_chunk(tenant, doc, 0, f"Content {i}")

        job = AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED,
        )
        docs = list(Document.objects.filter(project=project))
        chunks = [doc.chunks.first() for doc in docs]

        # Create claims and contradiction
        claim_a = Claim.objects.create(
            tenant=tenant, project=project, document=docs[0], chunk=chunks[0],
            subject="X", predicate="is", object_value="A", raw_text="X is A",
        )
        claim_b = Claim.objects.create(
            tenant=tenant, project=project, document=docs[1], chunk=chunks[1],
            subject="X", predicate="is", object_value="B", raw_text="X is B",
        )
        ContradictionPair.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            claim_a=claim_a, claim_b=claim_b,
            classification="contradiction", severity="high",
            confidence=0.95, evidence="Direct conflict.",
        )

        result = compute_docuscore(project)
        assert result["breakdown"]["consistency"] < 100

    def test_only_uses_latest_completed_job(self, tenant, project, connector):
        """Should use the most recent completed analysis, not older or incomplete ones."""
        make_document(tenant, project, connector, title="Doc", status="ready")

        # Old completed job
        AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED,
        )
        # Running job (should be ignored)
        AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.RUNNING,
        )

        result = compute_docuscore(project)
        assert result["has_analysis"] is True


# ---------------------------------------------------------------------------
# compute_docuscore_for_job
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestComputeDocuscoreForJob:
    def test_non_completed_returns_none(self, tenant, project):
        job = AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.RUNNING,
        )
        assert compute_docuscore_for_job(job) is None

    def test_no_documents_returns_e(self, tenant, project):
        job = AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED,
        )
        result = compute_docuscore_for_job(job)
        assert result["grade"] == "E"
        assert result["score"] == 0

    def test_completed_with_docs(self, tenant, project, connector):
        for i in range(3):
            make_document(tenant, project, connector, title=f"Doc{i}", status="ready")

        job = AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED,
        )
        result = compute_docuscore_for_job(job)
        assert "grade" in result
        assert "score" in result
        assert 0 <= result["score"] <= 100


# ---------------------------------------------------------------------------
# compute_docuscore_detail
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestComputeDocuscoreDetail:
    def test_no_documents(self, project):
        result = compute_docuscore_detail(project)
        assert result["grade"] == "E"
        assert result["score"] == 0
        assert "vide" in result["summary"].lower() or "empty" in result["summary"].lower()
        assert len(result["dimensions"]) == 0 or len(result["top_recommendations"]) > 0

    def test_documents_no_analysis(self, tenant, project, connector):
        make_document(tenant, project, connector, title="Doc1", status="ready")
        result = compute_docuscore_detail(project)
        assert result["grade"] in ("A", "B", "C", "D", "E")
        assert "dimensions" in result
        # Should have at least the health dimension
        dim_names = [d["name"] for d in result["dimensions"]]
        assert "Santé" in dim_names

    def test_full_detail_with_analysis(self, tenant, project, connector):
        for i in range(5):
            make_document(tenant, project, connector, title=f"Doc{i}", status="ready")

        AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED,
        )
        result = compute_docuscore_detail(project)
        assert len(result["dimensions"]) == 7
        dim_names = [d["name"] for d in result["dimensions"]]
        assert "Unicité" in dim_names
        assert "Cohérence" in dim_names
        assert "Couverture" in dim_names
        assert "Structure" in dim_names
        assert "Santé" in dim_names
        assert "Retrievability" in dim_names
        assert "Gouvernance" in dim_names

    def test_each_dimension_has_required_fields(self, tenant, project, connector):
        for i in range(3):
            make_document(tenant, project, connector, title=f"Doc{i}", status="ready")
        AnalysisJob.objects.create(
            tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED,
        )
        result = compute_docuscore_detail(project)
        for dim in result["dimensions"]:
            assert "name" in dim
            assert "score" in dim
            assert "description" in dim
            assert "details" in dim
            assert "recommendations" in dim
