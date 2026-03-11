"""
Data migration: create a default "Projet principal" project for each tenant
and backfill all existing records with this project.
Also create ProjectMembership for each existing TenantMembership.
"""
import uuid

from django.db import migrations
from django.utils.text import slugify


def backfill_default_project(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    Project = apps.get_model("tenants", "Project")
    TenantMembership = apps.get_model("tenants", "TenantMembership")
    ProjectMembership = apps.get_model("tenants", "ProjectMembership")
    ConnectorConfig = apps.get_model("connectors", "ConnectorConfig")
    Document = apps.get_model("ingestion", "Document")
    IngestionJob = apps.get_model("ingestion", "IngestionJob")
    AnalysisJob = apps.get_model("analysis", "AnalysisJob")
    DuplicateGroup = apps.get_model("analysis", "DuplicateGroup")
    DuplicatePair = apps.get_model("analysis", "DuplicatePair")
    Claim = apps.get_model("analysis", "Claim")
    ContradictionPair = apps.get_model("analysis", "ContradictionPair")
    TopicCluster = apps.get_model("analysis", "TopicCluster")
    ClusterMembership = apps.get_model("analysis", "ClusterMembership")
    GapReport = apps.get_model("analysis", "GapReport")
    TreeNode = apps.get_model("analysis", "TreeNode")
    Report = apps.get_model("reports", "Report")

    for tenant in Tenant.objects.all():
        # Create default project
        project = Project.objects.create(
            id=uuid.uuid4(),
            tenant=tenant,
            name="Projet principal",
            slug="projet-principal",
            description="Projet par défaut créé lors de la migration multi-projets.",
        )

        # Create ProjectMembership for each TenantMembership
        for tm in TenantMembership.objects.filter(tenant=tenant):
            ProjectMembership.objects.create(
                id=uuid.uuid4(),
                project=project,
                user=tm.user,
                role=tm.role,
            )

        # Backfill all records with this project
        ConnectorConfig.objects.filter(tenant=tenant, project__isnull=True).update(project=project)
        Document.objects.filter(tenant=tenant, project__isnull=True).update(project=project)
        IngestionJob.objects.filter(tenant=tenant, project__isnull=True).update(project=project)
        AnalysisJob.objects.filter(tenant=tenant, project__isnull=True).update(project=project)
        DuplicateGroup.objects.filter(tenant=tenant, project__isnull=True).update(project=project)
        DuplicatePair.objects.filter(tenant=tenant, project__isnull=True).update(project=project)
        Claim.objects.filter(tenant=tenant, project__isnull=True).update(project=project)
        ContradictionPair.objects.filter(tenant=tenant, project__isnull=True).update(project=project)
        TopicCluster.objects.filter(tenant=tenant, project__isnull=True).update(project=project)
        ClusterMembership.objects.filter(tenant=tenant, project__isnull=True).update(project=project)
        GapReport.objects.filter(tenant=tenant, project__isnull=True).update(project=project)
        TreeNode.objects.filter(tenant=tenant, project__isnull=True).update(project=project)
        Report.objects.filter(tenant=tenant, project__isnull=True).update(project=project)


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0002_project_projectmembership"),
        ("connectors", "0002_add_project"),
        ("ingestion", "0002_add_project"),
        ("analysis", "0002_add_project"),
        ("reports", "0002_add_project"),
    ]

    operations = [
        migrations.RunPython(backfill_default_project, migrations.RunPython.noop),
    ]
