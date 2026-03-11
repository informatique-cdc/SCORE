"""Tests for model constraints, properties, managers, and helpers across all apps."""
import hashlib
import uuid

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.utils import timezone

from analysis.models import (
    AnalysisJob,
    AuditAxisResult,
    AuditJob,
    Claim,
    ClusterMembership,
    ContradictionPair,
    DuplicateGroup,
    DuplicatePair,
    GapReport,
    PhaseTrace,
    PipelineTrace,
    TopicCluster,
    TreeNode,
)
from chat.models import ChatConfig, Conversation, Message
from connectors.models import ConnectorConfig
from dashboard.models import Feedback
from ingestion.models import Document, DocumentChunk, IngestionJob
from reports.models import Report
from tenants.models import (
    AuditLog,
    Project,
    ProjectMembership,
    ProjectScopedModel,
    Tenant,
    TenantMembership,
    TenantScopedModel,
    log_audit,
)
from tests.conftest import make_chunk, make_document


# ---------------------------------------------------------------------------
# Tenant models
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTenantModel:
    def test_str(self):
        t = Tenant.objects.create(name="Acme", slug="acme")
        assert str(t) == "Acme"

    def test_uuid_primary_key(self):
        t = Tenant.objects.create(name="UUID Test", slug="uuid-test")
        assert isinstance(t.id, uuid.UUID)

    def test_unique_name(self):
        Tenant.objects.create(name="Unique", slug="unique-1")
        with pytest.raises(IntegrityError):
            Tenant.objects.create(name="Unique", slug="unique-2")

    def test_unique_slug(self):
        Tenant.objects.create(name="First", slug="same-slug")
        with pytest.raises(IntegrityError):
            Tenant.objects.create(name="Second", slug="same-slug")

    def test_default_limits(self):
        t = Tenant.objects.create(name="Defaults", slug="defaults")
        assert t.max_documents == 10_000
        assert t.max_connectors == 10

    def test_ordering(self):
        Tenant.objects.create(name="Zeta", slug="zeta")
        Tenant.objects.create(name="Alpha", slug="alpha")
        names = list(Tenant.objects.values_list("name", flat=True))
        assert names == sorted(names)


@pytest.mark.django_db
class TestTenantMembership:
    def test_str(self, tenant, user):
        tm = TenantMembership.objects.create(tenant=tenant, user=user, role="admin")
        assert "testuser" in str(tm)
        assert "Test Tenant" in str(tm)
        assert "admin" in str(tm)

    def test_is_admin_property(self, tenant, user):
        tm = TenantMembership.objects.create(tenant=tenant, user=user, role="admin")
        assert tm.is_admin is True

    def test_is_admin_false_for_viewer(self, tenant, user):
        tm = TenantMembership.objects.create(tenant=tenant, user=user, role="viewer")
        assert tm.is_admin is False

    def test_can_edit_admin(self, tenant, user):
        tm = TenantMembership.objects.create(tenant=tenant, user=user, role="admin")
        assert tm.can_edit is True

    def test_can_edit_editor(self, tenant, user):
        tm = TenantMembership.objects.create(tenant=tenant, user=user, role="editor")
        assert tm.can_edit is True

    def test_can_edit_viewer(self, tenant, user):
        tm = TenantMembership.objects.create(tenant=tenant, user=user, role="viewer")
        assert tm.can_edit is False

    def test_unique_together(self, tenant, user):
        TenantMembership.objects.create(tenant=tenant, user=user, role="admin")
        with pytest.raises(IntegrityError):
            TenantMembership.objects.create(tenant=tenant, user=user, role="viewer")


@pytest.mark.django_db
class TestProjectModel:
    def test_str(self, tenant):
        p = Project.objects.create(tenant=tenant, name="My Project", slug="my-proj")
        assert str(p) == "My Project"

    def test_unique_together_slug(self, tenant):
        Project.objects.create(tenant=tenant, name="P1", slug="same")
        with pytest.raises(IntegrityError):
            Project.objects.create(tenant=tenant, name="P2", slug="same")

    def test_different_tenants_same_slug(self):
        t1 = Tenant.objects.create(name="T1", slug="t1")
        t2 = Tenant.objects.create(name="T2", slug="t2")
        Project.objects.create(tenant=t1, name="P", slug="same")
        p2 = Project.objects.create(tenant=t2, name="P", slug="same")
        assert p2.pk is not None


@pytest.mark.django_db
class TestProjectMembership:
    def test_is_admin(self, tenant, user):
        project = Project.objects.create(tenant=tenant, name="P", slug="p")
        pm = ProjectMembership.objects.create(
            project=project, user=user, role=TenantMembership.Role.ADMIN
        )
        assert pm.is_admin is True

    def test_can_edit(self, tenant, user):
        project = Project.objects.create(tenant=tenant, name="P", slug="p")
        pm = ProjectMembership.objects.create(
            project=project, user=user, role=TenantMembership.Role.EDITOR
        )
        assert pm.can_edit is True

    def test_str(self, tenant, user):
        project = Project.objects.create(tenant=tenant, name="P", slug="p")
        pm = ProjectMembership.objects.create(
            project=project, user=user, role=TenantMembership.Role.VIEWER
        )
        assert "testuser" in str(pm)
        assert "P" in str(pm)

    def test_unique_together(self, tenant, user):
        project = Project.objects.create(tenant=tenant, name="P", slug="p")
        ProjectMembership.objects.create(project=project, user=user, role="admin")
        with pytest.raises(IntegrityError):
            ProjectMembership.objects.create(project=project, user=user, role="viewer")


# ---------------------------------------------------------------------------
# Managers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTenantScopedManager:
    def test_for_tenant(self, tenant):
        p1 = Project.objects.create(tenant=tenant, name="P1", slug="p1")
        t2 = Tenant.objects.create(name="Other", slug="other")
        p2 = Project.objects.create(tenant=t2, name="P2", slug="p2")

        qs = Project.objects.for_tenant(tenant)
        assert p1 in qs
        assert p2 not in qs

    def test_for_project(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="D1")
        t2 = Tenant.objects.create(name="Other", slug="other")
        p2 = Project.objects.create(tenant=t2, name="P2", slug="p2")
        c2 = ConnectorConfig.objects.create(
            tenant=t2, project=p2, name="C2", connector_type="generic"
        )
        doc2 = make_document(t2, p2, c2, title="D2")

        qs = Document.objects.for_project(project)
        assert doc in qs
        assert doc2 not in qs


# ---------------------------------------------------------------------------
# AuditLog & log_audit helper
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAuditLog:
    def test_log_audit_creates_entry(self, tenant, user):
        log_audit(
            tenant=tenant,
            user=user,
            action=AuditLog.Action.TENANT_CREATED,
            target=tenant,
        )
        assert AuditLog.objects.filter(tenant=tenant).count() == 1

    def test_log_audit_captures_target_info(self, tenant, user):
        log_audit(
            tenant=tenant,
            user=user,
            action=AuditLog.Action.USER_INVITED,
            target=user,
            target_label="testuser",
            detail={"role": "editor"},
        )
        entry = AuditLog.objects.first()
        assert entry.target_type == "User"
        assert entry.target_label == "testuser"
        assert entry.detail["role"] == "editor"

    def test_str(self, tenant, user):
        log_audit(
            tenant=tenant, user=user,
            action=AuditLog.Action.PROJECT_CREATED,
            target_label="My Project",
        )
        entry = AuditLog.objects.first()
        assert "My Project" in str(entry)

    def test_log_audit_no_target(self, tenant, user):
        log_audit(
            tenant=tenant, user=user,
            action=AuditLog.Action.TENANT_CREATED,
        )
        entry = AuditLog.objects.first()
        assert entry.target_type == ""
        assert entry.target_id == ""


# ---------------------------------------------------------------------------
# Connector models
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConnectorConfig:
    def test_str(self, tenant, project):
        c = ConnectorConfig.objects.create(
            tenant=tenant, project=project, name="My Source", connector_type="generic"
        )
        assert "My Source" in str(c)
        assert "generic" in str(c)

    def test_default_config_is_dict(self, tenant, project):
        c = ConnectorConfig.objects.create(
            tenant=tenant, project=project, name="C", connector_type="generic"
        )
        assert c.config == {}

    def test_connector_type_choices(self):
        assert "sharepoint" in dict(ConnectorConfig.ConnectorType.choices)
        assert "confluence" in dict(ConnectorConfig.ConnectorType.choices)
        assert "generic" in dict(ConnectorConfig.ConnectorType.choices)


# ---------------------------------------------------------------------------
# Document & chunk models
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentModel:
    def test_str(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="My Doc")
        assert str(doc) == "My Doc"

    def test_unique_together_source_id(self, tenant, project, connector):
        make_document(tenant, project, connector, title="D1", source_id="same-id")
        with pytest.raises(IntegrityError):
            make_document(tenant, project, connector, title="D2", source_id="same-id")

    def test_status_choices(self):
        choices = dict(Document.Status.choices)
        assert "pending" in choices
        assert "ready" in choices
        assert "error" in choices
        assert "deleted" in choices


@pytest.mark.django_db
class TestDocumentChunkModel:
    def test_str(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="ChunkDoc")
        chunk = make_chunk(tenant, doc, 0, "Hello world")
        assert "ChunkDoc" in str(chunk)
        assert "chunk 0" in str(chunk)

    def test_unique_together(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="UniqueChunk")
        make_chunk(tenant, doc, 0, "first")
        with pytest.raises(IntegrityError):
            make_chunk(tenant, doc, 0, "second")


@pytest.mark.django_db
class TestIngestionJob:
    def test_str(self, tenant, project, connector):
        job = IngestionJob.objects.create(
            tenant=tenant, project=project, connector=connector
        )
        assert "Test Connector" in str(job)

    def test_progress_pct_zero_total(self, tenant, project, connector):
        job = IngestionJob.objects.create(
            tenant=tenant, project=project, connector=connector,
            total_documents=0, processed_documents=0,
        )
        assert job.progress_pct == 0

    def test_progress_pct_partial(self, tenant, project, connector):
        job = IngestionJob.objects.create(
            tenant=tenant, project=project, connector=connector,
            total_documents=10, processed_documents=3,
        )
        assert job.progress_pct == 30

    def test_progress_pct_complete(self, tenant, project, connector):
        job = IngestionJob.objects.create(
            tenant=tenant, project=project, connector=connector,
            total_documents=5, processed_documents=5,
        )
        assert job.progress_pct == 100


# ---------------------------------------------------------------------------
# Analysis models
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnalysisJob:
    def test_str(self, tenant, project):
        job = AnalysisJob.objects.create(tenant=tenant, project=project, status="queued")
        assert "queued" in str(job)

    def test_default_values(self, tenant, project):
        job = AnalysisJob.objects.create(tenant=tenant, project=project)
        assert job.status == AnalysisJob.Status.QUEUED
        assert job.current_phase == AnalysisJob.Phase.DUPLICATES
        assert job.includes_audit is True
        assert job.progress_pct == 0


@pytest.mark.django_db
class TestClaimModel:
    def test_str(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="ClaimDoc")
        chunk = make_chunk(tenant, doc, 0, "text")
        claim = Claim.objects.create(
            tenant=tenant, project=project, document=doc, chunk=chunk,
            subject="Python", predicate="is", object_value="a language",
            raw_text="Python is a language.",
        )
        assert "Python" in str(claim)
        assert "is" in str(claim)
        assert "a language" in str(claim)

    def test_as_text_property(self, tenant, project, connector):
        doc = make_document(tenant, project, connector, title="AsText")
        chunk = make_chunk(tenant, doc, 0, "text")
        claim = Claim.objects.create(
            tenant=tenant, project=project, document=doc, chunk=chunk,
            subject="Django", predicate="supports", object_value="ORM",
            raw_text="Django supports ORM.",
        )
        assert claim.as_text == "Django supports ORM"


@pytest.mark.django_db
class TestGapReport:
    def test_str(self, analysis_job, tenant, project):
        gap = GapReport.objects.create(
            tenant=tenant, project=project, analysis_job=analysis_job,
            gap_type="missing_topic", title="Missing API docs",
            description="No API documentation found.", severity="high",
        )
        assert "Missing API docs" in str(gap)


@pytest.mark.django_db
class TestTopicCluster:
    def test_str(self, analysis_job, tenant, project):
        cluster = TopicCluster.objects.create(
            tenant=tenant, project=project, analysis_job=analysis_job,
            label="Security Practices",
        )
        assert str(cluster) == "Security Practices"


@pytest.mark.django_db
class TestAuditJob:
    def test_str(self, tenant, project):
        analysis = AnalysisJob.objects.create(tenant=tenant, project=project)
        audit = AuditJob.objects.create(
            tenant=tenant, project=project, analysis_job=analysis,
            status="completed",
        )
        assert "completed" in str(audit)

    def test_default_status(self, tenant, project):
        analysis = AnalysisJob.objects.create(tenant=tenant, project=project)
        audit = AuditJob.objects.create(
            tenant=tenant, project=project, analysis_job=analysis,
        )
        assert audit.status == AuditJob.Status.QUEUED


@pytest.mark.django_db
class TestAuditAxisResult:
    def test_str(self, tenant, project):
        analysis = AnalysisJob.objects.create(tenant=tenant, project=project)
        audit = AuditJob.objects.create(
            tenant=tenant, project=project, analysis_job=analysis,
        )
        result = AuditAxisResult.objects.create(
            tenant=tenant, project=project, audit_job=audit,
            axis="hygiene", score=85.0, metrics={}, chart_data={}, details={},
        )
        assert "85" in str(result)

    def test_unique_together(self, tenant, project):
        analysis = AnalysisJob.objects.create(tenant=tenant, project=project)
        audit = AuditJob.objects.create(
            tenant=tenant, project=project, analysis_job=analysis,
        )
        AuditAxisResult.objects.create(
            tenant=tenant, project=project, audit_job=audit,
            axis="hygiene", score=80, metrics={}, chart_data={}, details={},
        )
        with pytest.raises(IntegrityError):
            AuditAxisResult.objects.create(
                tenant=tenant, project=project, audit_job=audit,
                axis="hygiene", score=90, metrics={}, chart_data={}, details={},
            )


# ---------------------------------------------------------------------------
# Chat models
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConversation:
    def test_str_with_title(self, tenant, project, user):
        conv = Conversation.objects.create(
            tenant=tenant, project=project, user=user, title="My Chat"
        )
        assert str(conv) == "My Chat"

    def test_str_without_title(self, tenant, project, user):
        conv = Conversation.objects.create(
            tenant=tenant, project=project, user=user, title=""
        )
        assert "Conversation" in str(conv)


@pytest.mark.django_db
class TestMessage:
    def test_str(self, tenant, project, user):
        conv = Conversation.objects.create(
            tenant=tenant, project=project, user=user, title="Chat"
        )
        msg = Message.objects.create(
            conversation=conv, role="user", content="Hello world!"
        )
        assert "user" in str(msg)
        assert "Hello" in str(msg)


@pytest.mark.django_db
class TestChatConfig:
    def test_str(self, tenant, project, user):
        config = ChatConfig.objects.create(
            tenant=tenant, project=project, user=user,
            system_prompt="You are helpful.",
        )
        assert "testuser" in str(config)

    def test_unique_together(self, tenant, project, user):
        ChatConfig.objects.create(
            tenant=tenant, project=project, user=user,
        )
        with pytest.raises(IntegrityError):
            ChatConfig.objects.create(
                tenant=tenant, project=project, user=user,
            )


# ---------------------------------------------------------------------------
# Dashboard models
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFeedback:
    def test_str(self, tenant, user):
        fb = Feedback.objects.create(
            tenant=tenant, user=user,
            feedback_type="feedback", area="analysis",
            subject="Great feature", description="I like the analysis.",
        )
        assert "Great feature" in str(fb)
        assert "Feedback" in str(fb)


# ---------------------------------------------------------------------------
# Report models
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReportModel:
    def test_str(self, tenant, project):
        job = AnalysisJob.objects.create(tenant=tenant, project=project)
        report = Report.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            report_type="full", title="Full Report Q1",
        )
        assert str(report) == "Full Report Q1"

    def test_default_format(self, tenant, project):
        job = AnalysisJob.objects.create(tenant=tenant, project=project)
        report = Report.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            report_type="duplicates", title="Dup Report",
        )
        assert report.format == Report.Format.HTML


# ---------------------------------------------------------------------------
# Trace models
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPipelineTrace:
    def test_str(self, tenant, project):
        job = AnalysisJob.objects.create(tenant=tenant, project=project)
        trace = PipelineTrace.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            started_at=timezone.now(),
        )
        assert str(job.id)[:8] in str(trace)


@pytest.mark.django_db
class TestPhaseTrace:
    def test_str(self, tenant, project):
        job = AnalysisJob.objects.create(tenant=tenant, project=project)
        pipeline_trace = PipelineTrace.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            started_at=timezone.now(),
        )
        phase = PhaseTrace.objects.create(
            tenant=tenant, project=project, pipeline_trace=pipeline_trace,
            phase_key="duplicates", phase_label="Duplicate Detection",
            status="completed",
        )
        assert "Duplicate Detection" in str(phase)
        assert "completed" in str(phase)

    def test_unique_together(self, tenant, project):
        job = AnalysisJob.objects.create(tenant=tenant, project=project)
        pt = PipelineTrace.objects.create(
            tenant=tenant, project=project, analysis_job=job,
            started_at=timezone.now(),
        )
        PhaseTrace.objects.create(
            tenant=tenant, project=project, pipeline_trace=pt,
            phase_key="duplicates", phase_label="Dup",
        )
        with pytest.raises(IntegrityError):
            PhaseTrace.objects.create(
                tenant=tenant, project=project, pipeline_trace=pt,
                phase_key="duplicates", phase_label="Dup Again",
            )
