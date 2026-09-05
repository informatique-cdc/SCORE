import hashlib
import uuid

import pytest
from django.contrib.auth.models import User

from analysis.models import (
    AnalysisJob,
    Claim,
    ContradictionPair,
    DuplicateGroup,
    DuplicatePair,
    GapReport,
    TopicCluster,
)
from api.models import APIToken
from connectors.models import ConnectorConfig
from ingestion.models import Document, DocumentChunk
from tenants.models import Project, Tenant


@pytest.fixture
def results_setup(db):
    tenant = Tenant.objects.create(name="ResultsTenant", slug="results-tenant")
    project = Project.objects.create(tenant=tenant, name="ResultsProject", slug="results-project")
    user = User.objects.create_user(username="resultsuser", password="pass")
    raw_token = "results_test_token"
    APIToken.objects.create(
        key_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        user=user,
        tenant=tenant,
        project=project,
        name="results-token",
    )
    job = AnalysisJob.objects.create(
        tenant=tenant,
        project=project,
        status=AnalysisJob.Status.COMPLETED,
    )
    connector = ConnectorConfig.objects.create(
        tenant=tenant,
        project=project,
        name="test-connector",
        connector_type=ConnectorConfig.ConnectorType.GENERIC,
    )
    doc_a = Document.objects.create(
        tenant=tenant,
        project=project,
        connector=connector,
        source_id="doc-a",
        title="Document A",
        content_hash="aaa",
    )
    doc_b = Document.objects.create(
        tenant=tenant,
        project=project,
        connector=connector,
        source_id="doc-b",
        title="Document B",
        content_hash="bbb",
    )
    chunk_a = DocumentChunk.objects.create(
        tenant=tenant,
        document=doc_a,
        chunk_index=0,
        content="Chunk A content",
    )
    chunk_b = DocumentChunk.objects.create(
        tenant=tenant,
        document=doc_b,
        chunk_index=0,
        content="Chunk B content",
    )
    return {
        "tenant": tenant,
        "project": project,
        "token": raw_token,
        "job": job,
        "connector": connector,
        "doc_a": doc_a,
        "doc_b": doc_b,
        "chunk_a": chunk_a,
        "chunk_b": chunk_b,
    }


class TestDuplicatesView:
    def test_list_duplicate_groups_with_pairs(self, client, results_setup):
        s = results_setup
        group = DuplicateGroup.objects.create(
            tenant=s["tenant"],
            project=s["project"],
            analysis_job=s["job"],
            recommended_action=DuplicateGroup.Action.MERGE,
            rationale="Very similar content",
        )
        pair = DuplicatePair.objects.create(
            tenant=s["tenant"],
            project=s["project"],
            group=group,
            doc_a=s["doc_a"],
            doc_b=s["doc_b"],
            semantic_score=0.95,
            lexical_score=0.80,
            metadata_score=0.70,
            combined_score=0.85,
        )

        response = client.get(
            f"/api/v1/analysis/{s['job'].id}/duplicates/",
            HTTP_AUTHORIZATION=f"Bearer {s['token']}",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["groups"]) == 1
        g = data["groups"][0]
        assert g["id"] == str(group.id)
        assert g["recommended_action"] == "merge"
        assert g["rationale"] == "Very similar content"
        assert len(g["pairs"]) == 1
        p = g["pairs"][0]
        assert p["doc_a"]["title"] == "Document A"
        assert p["doc_b"]["title"] == "Document B"
        assert p["combined_score"] == 0.85
        assert p["verified"] is False

    def test_duplicates_not_found(self, client, results_setup):
        response = client.get(
            f"/api/v1/analysis/{uuid.uuid4()}/duplicates/",
            HTTP_AUTHORIZATION=f"Bearer {results_setup['token']}",
        )
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"

    def test_duplicates_empty(self, client, results_setup):
        response = client.get(
            f"/api/v1/analysis/{results_setup['job'].id}/duplicates/",
            HTTP_AUTHORIZATION=f"Bearer {results_setup['token']}",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["groups"] == []


class TestContradictionsView:
    def test_list_contradictions(self, client, results_setup):
        s = results_setup
        claim_a = Claim.objects.create(
            tenant=s["tenant"],
            project=s["project"],
            document=s["doc_a"],
            chunk=s["chunk_a"],
            subject="Policy X",
            predicate="requires",
            object_value="approval",
            raw_text="Policy X requires approval",
        )
        claim_b = Claim.objects.create(
            tenant=s["tenant"],
            project=s["project"],
            document=s["doc_b"],
            chunk=s["chunk_b"],
            subject="Policy X",
            predicate="does not require",
            object_value="approval",
            raw_text="Policy X does not require approval",
        )
        contradiction = ContradictionPair.objects.create(
            tenant=s["tenant"],
            project=s["project"],
            analysis_job=s["job"],
            claim_a=claim_a,
            claim_b=claim_b,
            classification=ContradictionPair.Classification.CONTRADICTION,
            severity=ContradictionPair.Severity.HIGH,
            confidence=0.92,
            evidence="Claims directly contradict each other.",
        )

        response = client.get(
            f"/api/v1/analysis/{s['job'].id}/contradictions/",
            HTTP_AUTHORIZATION=f"Bearer {s['token']}",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        c = data["contradictions"][0]
        assert c["id"] == str(contradiction.id)
        assert c["classification"] == "contradiction"
        assert c["severity"] == "high"
        assert c["confidence"] == 0.92
        assert c["claim_a"]["document_id"] == str(s["doc_a"].id)
        assert c["claim_b"]["document_id"] == str(s["doc_b"].id)

    def test_contradictions_not_found(self, client, results_setup):
        response = client.get(
            f"/api/v1/analysis/{uuid.uuid4()}/contradictions/",
            HTTP_AUTHORIZATION=f"Bearer {results_setup['token']}",
        )
        assert response.status_code == 404


class TestClustersView:
    def test_list_top_level_clusters(self, client, results_setup):
        s = results_setup
        parent_cluster = TopicCluster.objects.create(
            tenant=s["tenant"],
            project=s["project"],
            analysis_job=s["job"],
            label="Security",
            summary="Security-related documents",
            doc_count=5,
            chunk_count=20,
            level=0,
            key_concepts=["auth", "encryption"],
        )
        # Child cluster should NOT appear in results
        TopicCluster.objects.create(
            tenant=s["tenant"],
            project=s["project"],
            analysis_job=s["job"],
            parent=parent_cluster,
            label="Authentication",
            summary="Auth docs",
            doc_count=2,
            chunk_count=8,
            level=1,
            key_concepts=["login", "SSO"],
        )

        response = client.get(
            f"/api/v1/analysis/{s['job'].id}/clusters/",
            HTTP_AUTHORIZATION=f"Bearer {s['token']}",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        cl = data["clusters"][0]
        assert cl["id"] == str(parent_cluster.id)
        assert cl["label"] == "Security"
        assert cl["doc_count"] == 5
        assert cl["key_concepts"] == ["auth", "encryption"]

    def test_clusters_not_found(self, client, results_setup):
        response = client.get(
            f"/api/v1/analysis/{uuid.uuid4()}/clusters/",
            HTTP_AUTHORIZATION=f"Bearer {results_setup['token']}",
        )
        assert response.status_code == 404


class TestGapsView:
    def test_list_gaps(self, client, results_setup):
        s = results_setup
        gap = GapReport.objects.create(
            tenant=s["tenant"],
            project=s["project"],
            analysis_job=s["job"],
            gap_type=GapReport.GapType.MISSING_TOPIC,
            title="Missing disaster recovery docs",
            description="No documentation covers disaster recovery procedures.",
            severity="high",
            coverage_score=0.1,
            evidence={"unanswered_questions": ["What is the DR plan?"]},
        )

        response = client.get(
            f"/api/v1/analysis/{s['job'].id}/gaps/",
            HTTP_AUTHORIZATION=f"Bearer {s['token']}",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        g = data["gaps"][0]
        assert g["id"] == str(gap.id)
        assert g["gap_type"] == "missing_topic"
        assert g["title"] == "Missing disaster recovery docs"
        assert g["severity"] == "high"
        assert g["coverage_score"] == 0.1

    def test_gaps_not_found(self, client, results_setup):
        response = client.get(
            f"/api/v1/analysis/{uuid.uuid4()}/gaps/",
            HTTP_AUTHORIZATION=f"Bearer {results_setup['token']}",
        )
        assert response.status_code == 404

    def test_gaps_empty(self, client, results_setup):
        response = client.get(
            f"/api/v1/analysis/{results_setup['job'].id}/gaps/",
            HTTP_AUTHORIZATION=f"Bearer {results_setup['token']}",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["gaps"] == []


class TestAuthRequired:
    def test_no_token_returns_401(self, client, results_setup):
        response = client.get(
            f"/api/v1/analysis/{results_setup['job'].id}/duplicates/",
        )
        assert response.status_code == 401
