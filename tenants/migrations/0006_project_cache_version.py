from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0005_audit_log"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="cache_version",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
