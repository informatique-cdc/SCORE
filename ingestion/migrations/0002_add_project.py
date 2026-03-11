import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ingestion", "0001_initial"),
        ("tenants", "0002_project_projectmembership"),
    ]

    operations = [
        # Add nullable project FK to Document
        migrations.AddField(
            model_name="document",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(class)s_set",
                to="tenants.project",
            ),
        ),
        # Add nullable project FK to IngestionJob
        migrations.AddField(
            model_name="ingestionjob",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(class)s_set",
                to="tenants.project",
            ),
        ),
        # Update unique_together and index for Document
        migrations.AlterUniqueTogether(
            name="document",
            unique_together={("tenant", "project", "connector", "source_id")},
        ),
        migrations.RemoveIndex(
            model_name="document",
            name="ingestion_d_tenant__6b1cdf_idx",
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(fields=["tenant", "project", "status"], name="ingestion_d_tenant_proj_st_idx"),
        ),
    ]
