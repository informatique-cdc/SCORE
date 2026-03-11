import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConnectorConfig",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=200)),
                ("connector_type", models.CharField(choices=[("sharepoint", "SharePoint"), ("confluence", "Confluence"), ("generic", "Générique (Fichier/HTTP)")], max_length=20)),
                ("enabled", models.BooleanField(default=True)),
                ("config", models.JSONField(default=dict, help_text="Connection parameters (site_url, space_key, base_path, etc.)")),
                ("credential_ref", models.CharField(blank=True, help_text="Reference to credential store (env var name or secret manager path)", max_length=500)),
                ("schedule_cron", models.CharField(blank=True, default="", help_text="Cron expression for scheduled syncs (empty = manual only)", max_length=100)),
                ("last_sync_at", models.DateTimeField(blank=True, null=True)),
                ("last_sync_status", models.CharField(blank=True, default="", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(class)s_set", to="tenants.tenant")),
            ],
            options={"ordering": ["name"]},
        ),
    ]
