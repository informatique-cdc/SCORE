"""
Management command to migrate existing connector credentials from environment
variables into per-tenant encrypted secrets.

For production deployments that were using credential_ref (env var names),
this reads each env var value and encrypts it into the encrypted_secret field.

Usage:
    # Dry run (default) — shows what would be migrated without changing anything
    python manage.py migrate_connector_secrets

    # Actually perform the migration
    python manage.py migrate_connector_secrets --apply

    # Also clear credential_ref after migration (env vars no longer needed)
    python manage.py migrate_connector_secrets --apply --clear-ref
"""

import logging
import os

from django.core.management.base import BaseCommand

from connectors.models import ConnectorConfig

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Migrate connector credentials from environment variables "
        "into per-tenant encrypted secrets."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually perform the migration. Without this flag, runs in dry-run mode.",
        )
        parser.add_argument(
            "--clear-ref",
            action="store_true",
            help="Clear credential_ref after successful encryption (env var no longer needed).",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        clear_ref = options["clear_ref"]

        if not apply:
            self.stdout.write(
                self.style.WARNING("DRY RUN — pass --apply to perform the migration.\n")
            )

        connectors = ConnectorConfig.objects.filter(
            credential_ref__gt="",
        ).exclude(
            encrypted_secret__gt="",
        )

        total = connectors.count()
        if total == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    "Nothing to migrate: no connectors with credential_ref and empty encrypted_secret."
                )
            )
            return

        self.stdout.write(f"Found {total} connector(s) to migrate.\n")

        migrated = 0
        skipped = 0
        errors = 0

        for connector in connectors:
            ref = connector.credential_ref
            env_value = os.environ.get(ref, "")

            tenant_name = connector.tenant.name if connector.tenant else "?"
            label = f"  [{connector.name}] tenant={tenant_name} ref={ref}"

            if not env_value:
                self.stdout.write(
                    self.style.WARNING(f"{label} — SKIPPED (env var '{ref}' is empty or unset)")
                )
                skipped += 1
                continue

            if apply:
                try:
                    connector.set_secret(env_value)
                    if clear_ref:
                        connector.credential_ref = ""
                    connector.save()
                    self.stdout.write(self.style.SUCCESS(f"{label} — MIGRATED"))
                    migrated += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"{label} — ERROR: {e}"))
                    errors += 1
            else:
                masked = env_value[:3] + "***" if len(env_value) > 3 else "***"
                self.stdout.write(f"{label} — WOULD MIGRATE (value: {masked})")
                migrated += 1

        self.stdout.write("")
        if apply:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. Migrated: {migrated}, Skipped: {skipped}, Errors: {errors}"
                )
            )
            if migrated > 0 and not clear_ref:
                self.stdout.write(
                    self.style.NOTICE(
                        "Tip: re-run with --clear-ref to remove credential_ref values "
                        "once you've verified the encrypted secrets work correctly."
                    )
                )
        else:
            self.stdout.write(
                f"Would migrate: {migrated}, Would skip: {skipped}\nPass --apply to execute."
            )
