from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("connectors", "0004_merge_20260408_1308"),
    ]

    operations = [
        migrations.AlterField(
            model_name="connectorconfig",
            name="connector_type",
            field=models.CharField(
                choices=[
                    ("sharepoint", "SharePoint"),
                    ("confluence", "Confluence"),
                    ("elasticsearch", "Elasticsearch"),
                    ("generic", "Générique (Fichier/HTTP)"),
                    ("zipupload", "Import ZIP"),
                ],
                max_length=20,
            ),
        ),
    ]
