import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0001_initial"),
        ("tenants", "0002_project_projectmembership"),
    ]

    operations = [
        migrations.AddField(
            model_name="report",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="%(class)s_set",
                to="tenants.project",
            ),
        ),
    ]
