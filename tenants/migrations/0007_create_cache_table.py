"""Crée la table du cache en base.

Passer par une migration plutôt que par ``manage.py createcachetable`` : c'est le
seul moyen que la table existe dans la base de test de pytest-django, qui ne joue
que les migrations, et dans les déploiements dont le CMD ne lance aucune commande
custom.
"""

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", database=schema_editor.connection.alias, verbosity=0)


def drop_cache_table(apps, schema_editor):
    schema_editor.execute("DROP TABLE IF EXISTS score_cache")


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0006_project_cache_version"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
