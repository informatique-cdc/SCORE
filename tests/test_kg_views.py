"""Tests for the knowledge graph page and its JSON endpoint."""

import pytest
from django.contrib.auth.models import User
from django.test import Client

from analysis.models import (
    AnalysisJob,
    KGEntity,
    KGRelation,
    KnowledgeGraphRun,
    TopicCluster,
)
from tenants.models import Project, ProjectMembership, Tenant, TenantMembership


@pytest.fixture
def setup(db):
    user = User.objects.create_user("kguser", "kg@example.com", "pass1234")
    tenant = Tenant.objects.create(name="KGTenant", slug="kg-tenant")
    TenantMembership.objects.create(tenant=tenant, user=user, role=TenantMembership.Role.ADMIN)
    project = Project.objects.create(tenant=tenant, name="KGProject", slug="kg-project")
    ProjectMembership.objects.create(project=project, user=user, role=TenantMembership.Role.ADMIN)
    job = AnalysisJob.objects.create(tenant=tenant, project=project)
    return user, tenant, project, job


def _client(user, tenant, project):
    c = Client()
    c.login(username=user.username, password="pass1234")
    session = c.session
    session["tenant_id"] = str(tenant.id)
    session["project_id"] = str(project.id)
    session.save()
    return c


def _graph(tenant, project, job):
    cluster = TopicCluster.objects.create(
        tenant=tenant, project=project, analysis_job=job, label="Santé"
    )
    run = KnowledgeGraphRun.objects.create(
        tenant=tenant,
        project=project,
        analysis_job=job,
        entity_count=2,
        relation_count=1,
        bridge_count=1,
        cluster_count=1,
    )
    ipsec = KGEntity.objects.create(
        tenant=tenant,
        project=project,
        run=run,
        canonical="ipsec",
        label="IPSEC",
        frequency=9,
        degree=1,
        centrality=0.8,
        main_cluster=cluster,
        cluster_count=2,
        pos_x=0.4,
        pos_y=-0.2,
        aliases=["Ipsec"],
    )
    garantie = KGEntity.objects.create(
        tenant=tenant,
        project=project,
        run=run,
        canonical="garantie",
        label="garantie",
        frequency=4,
        degree=1,
        centrality=0.3,
        main_cluster=cluster,
        cluster_count=1,
    )
    KGRelation.objects.create(
        tenant=tenant,
        project=project,
        run=run,
        subject=ipsec,
        object=garantie,
        predicate="propose",
        cluster=cluster,
    )
    return run


@pytest.mark.django_db
class TestKnowledgeGraphPage:
    def test_renders_when_a_run_exists(self, setup):
        user, tenant, project, job = setup
        _graph(tenant, project, job)

        response = _client(user, tenant, project).get(f"/analysis/{job.pk}/knowledge-graph/")

        assert response.status_code == 200
        assert b"kg-graph" in response.content

    def test_shows_an_honest_empty_state_without_a_run(self, setup):
        user, tenant, project, job = setup

        response = _client(user, tenant, project).get(f"/analysis/{job.pk}/knowledge-graph/")

        assert response.status_code == 200
        # No canvas is wired up when there is nothing to draw.
        assert b"kg-graph" not in response.content


@pytest.mark.django_db
class TestKnowledgeGraphApi:
    def test_payload_shape(self, setup):
        user, tenant, project, job = setup
        _graph(tenant, project, job)

        response = _client(user, tenant, project).get(f"/analysis/{job.pk}/api/knowledge-graph/")

        assert response.status_code == 200
        data = response.json()
        assert {n["label"] for n in data["nodes"]} == {"IPSEC", "garantie"}
        assert data["edges"][0]["predicate"] == "propose"
        assert data["clusters"][0]["label"] == "Santé"
        assert data["stats"]["entities"] == 2

    def test_serves_stored_layout_and_bridge_flag(self, setup):
        """Positions come from the build, so the page draws without simulating."""
        user, tenant, project, job = setup
        _graph(tenant, project, job)

        data = _client(user, tenant, project).get(f"/analysis/{job.pk}/api/knowledge-graph/").json()
        ipsec = next(n for n in data["nodes"] if n["label"] == "IPSEC")

        assert (ipsec["x"], ipsec["y"]) == (0.4, -0.2)
        assert ipsec["bridge"] is True
        assert ipsec["aliases"] == ["Ipsec"]

    def test_404_without_a_run(self, setup):
        user, tenant, project, job = setup

        response = _client(user, tenant, project).get(f"/analysis/{job.pk}/api/knowledge-graph/")

        assert response.status_code == 404

    def test_another_project_cannot_read_the_graph(self, setup):
        user, tenant, project, job = setup
        _graph(tenant, project, job)

        other = Project.objects.create(tenant=tenant, name="Other", slug="other")
        ProjectMembership.objects.create(project=other, user=user, role=TenantMembership.Role.ADMIN)

        response = _client(user, tenant, other).get(f"/analysis/{job.pk}/api/knowledge-graph/")

        assert response.status_code == 404
