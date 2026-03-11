import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("analysis", "0001_initial"),
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Report",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "report_type",
                    models.CharField(
                        choices=[
                            ("duplicates", "Rapport de doublons"),
                            ("contradictions", "Rapport de contradictions"),
                            ("gaps", "Rapport de lacunes"),
                            ("full", "Rapport d'analyse complet"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "format",
                    models.CharField(
                        choices=[("html", "HTML"), ("csv", "CSV"), ("json", "JSON")],
                        default="html",
                        max_length=10,
                    ),
                ),
                ("title", models.CharField(max_length=500)),
                ("summary", models.TextField(blank=True, default="")),
                ("data", models.JSONField(default=dict, help_text="Structured report data")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "analysis_job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reports",
                        to="analysis.analysisjob",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(class)s_set",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
