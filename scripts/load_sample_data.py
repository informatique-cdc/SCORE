#!/usr/bin/env python
"""
Load sample data for development and testing.

Usage:
    python manage.py shell < scripts/load_sample_data.py
    OR
    python scripts/load_sample_data.py  (with DJANGO_SETTINGS_MODULE set)
"""
import os
import sys

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "score.settings")

import django
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from tenants.models import Tenant, TenantMembership  # noqa: E402
from connectors.models import ConnectorConfig  # noqa: E402
from ingestion.models import Document, DocumentChunk  # noqa: E402
from ingestion.hashing import hash_content  # noqa: E402


def main():
    print("Chargement des données d'exemple...")

    # Create superuser
    admin, created = User.objects.get_or_create(
        username="admin",
        defaults={"email": "admin@score.local", "is_staff": True, "is_superuser": True},
    )
    if created:
        admin.set_password("admin")
        admin.save()
        print("  Superutilisateur créé : admin / admin")

    # Create demo user
    demo, created = User.objects.get_or_create(
        username="demo",
        defaults={"email": "demo@score.local"},
    )
    if created:
        demo.set_password("demo")
        demo.save()
        print("  Utilisateur démo créé : demo / demo")

    # Create tenants
    tenant1, _ = Tenant.objects.get_or_create(
        slug="acme-corp",
        defaults={"name": "ACME Corporation"},
    )
    tenant2, _ = Tenant.objects.get_or_create(
        slug="beta-labs",
        defaults={"name": "Beta Labs"},
    )
    print(f"  Espaces : {tenant1.name}, {tenant2.name}")

    # Memberships
    TenantMembership.objects.get_or_create(
        tenant=tenant1, user=admin, defaults={"role": "admin"}
    )
    TenantMembership.objects.get_or_create(
        tenant=tenant1, user=demo, defaults={"role": "editor"}
    )
    TenantMembership.objects.get_or_create(
        tenant=tenant2, user=admin, defaults={"role": "admin"}
    )

    # Create sample connector
    connector, _ = ConnectorConfig.objects.get_or_create(
        tenant=tenant1,
        name="Sample Docs",
        defaults={
            "connector_type": "generic",
            "config": {"source_type": "filesystem", "base_path": "/tmp/score-sample"},
        },
    )

    # Create sample documents
    sample_docs = [
        {
            "title": "Getting Started with ACME Platform",
            "content": (
                "# Getting Started\n\n"
                "Welcome to the ACME Platform. This guide covers initial setup, "
                "configuration, and first steps for new users.\n\n"
                "## Prerequisites\n\n"
                "You need Python 3.12 or later and Docker installed.\n\n"
                "## Installation\n\n"
                "Run `pip install acme-platform` to install the CLI tool. "
                "Then run `acme init` to initialize your project."
            ),
            "doc_type": "guide",
            "author": "Alice Smith",
        },
        {
            "title": "Getting Started Guide (Old Version)",
            "content": (
                "# Getting Started\n\n"
                "Welcome to the ACME Platform. This guide covers initial setup "
                "and configuration for new users.\n\n"
                "## Prerequisites\n\n"
                "You need Python 3.10 or later installed.\n\n"
                "## Installation\n\n"
                "Run `pip install acme-platform` to install. "
                "Then run `acme init` to set up your project."
            ),
            "doc_type": "guide",
            "author": "Alice Smith",
        },
        {
            "title": "API Reference - Rate Limits",
            "content": (
                "# API Rate Limits\n\n"
                "The ACME API enforces rate limits to ensure fair usage.\n\n"
                "## Current Limits\n\n"
                "- Free tier: 100 requests per minute\n"
                "- Pro tier: 1000 requests per minute\n"
                "- Enterprise: unlimited\n\n"
                "## Rate Limit Headers\n\n"
                "Each response includes X-RateLimit-Remaining and X-RateLimit-Reset headers."
            ),
            "doc_type": "reference",
            "author": "Bob Chen",
        },
        {
            "title": "API Rate Limiting Policy",
            "content": (
                "# Rate Limiting\n\n"
                "As of Q1 2024, the API rate limits have been updated:\n\n"
                "- Free tier: 50 requests per minute\n"
                "- Pro tier: 500 requests per minute\n"
                "- Enterprise: 10000 requests per minute\n\n"
                "The previous unlimited enterprise tier has been deprecated."
            ),
            "doc_type": "policy",
            "author": "Carol Davis",
        },
        {
            "title": "Architecture Overview",
            "content": (
                "# System Architecture\n\n"
                "The ACME platform uses a microservices architecture.\n\n"
                "## Core Services\n\n"
                "- API Gateway: handles routing and auth\n"
                "- User Service: manages user accounts\n"
                "- Data Service: handles data storage and retrieval\n\n"
                "## Infrastructure\n\n"
                "All services run on Kubernetes with auto-scaling enabled."
            ),
            "doc_type": "architecture",
            "author": "Dave Wilson",
        },
    ]

    for doc_data in sample_docs:
        content = doc_data["content"]
        doc, created = Document.objects.get_or_create(
            tenant=tenant1,
            connector=connector,
            source_id=doc_data["title"].lower().replace(" ", "-"),
            defaults={
                "title": doc_data["title"],
                "content_hash": hash_content(content),
                "source_version": "1",
                "author": doc_data["author"],
                "doc_type": doc_data["doc_type"],
                "word_count": len(content.split()),
                "status": Document.Status.INGESTED,
            },
        )
        if created:
            # Create a single chunk per doc for demo
            DocumentChunk.objects.create(
                tenant=tenant1,
                document=doc,
                chunk_index=0,
                content=content,
                token_count=len(content.split()),
                content_hash=hash_content(content),
            )
            print(f"  Document créé : {doc.title}")

    print(f"\nDonnées d'exemple chargées. {Document.objects.filter(tenant=tenant1).count()} documents dans {tenant1.name}.")
    print("\nIdentifiants de connexion :")
    print("  Admin : admin / admin")
    print("  Démo :  demo / demo")


if __name__ == "__main__":
    main()
