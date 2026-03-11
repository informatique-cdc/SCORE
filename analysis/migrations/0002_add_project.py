import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analysis", "0001_initial"),
        ("tenants", "0002_project_projectmembership"),
    ]

    operations = [
        # Remove old claim index
        migrations.RemoveIndex(
            model_name="claim",
            name="analysis_cl_tenant__05b49b_idx",
        ),
        # Add nullable project FK to all models
        migrations.AddField(
            model_name="analysisjob",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="%(class)s_set", to="tenants.project"),
        ),
        migrations.AddField(
            model_name="claim",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="%(class)s_set", to="tenants.project"),
        ),
        migrations.AddField(
            model_name="clustermembership",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="%(class)s_set", to="tenants.project"),
        ),
        migrations.AddField(
            model_name="contradictionpair",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="%(class)s_set", to="tenants.project"),
        ),
        migrations.AddField(
            model_name="duplicategroup",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="%(class)s_set", to="tenants.project"),
        ),
        migrations.AddField(
            model_name="duplicatepair",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="%(class)s_set", to="tenants.project"),
        ),
        migrations.AddField(
            model_name="gapreport",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="%(class)s_set", to="tenants.project"),
        ),
        migrations.AddField(
            model_name="topiccluster",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="%(class)s_set", to="tenants.project"),
        ),
        migrations.AddField(
            model_name="treenode",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="%(class)s_set", to="tenants.project"),
        ),
        # Update TreeNode node_type choices to include subcluster
        migrations.AlterField(
            model_name="treenode",
            name="node_type",
            field=models.CharField(
                choices=[("category", "Catégorie"), ("cluster", "Cluster"), ("subcluster", "Sous-cluster"), ("document", "Document"), ("section", "Section")],
                max_length=20,
            ),
        ),
        # New claim index with project
        migrations.AddIndex(
            model_name="claim",
            index=models.Index(fields=["tenant", "project", "subject"], name="analysis_cl_tenant_proj_subj_idx"),
        ),
    ]
