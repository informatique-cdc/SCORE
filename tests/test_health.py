"""Tests for the /healthz/ endpoint."""

from unittest.mock import patch, MagicMock

import pytest
from django.test import Client


@pytest.mark.django_db
class TestHealthEndpoint:
    def test_healthy(self):
        client = Client()
        with patch("vectorstore.store.get_vector_store") as mock_vs:
            mock_store = MagicMock()
            mock_vs.return_value = mock_store
            resp = client.get("/healthz/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["checks"]["database"] == "ok"
        assert data["checks"]["vector_store"] == "ok"

    def test_vector_store_failure(self):
        client = Client()
        with patch(
            "vectorstore.store.get_vector_store",
            side_effect=Exception("Vec down"),
        ):
            resp = client.get("/healthz/")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "unhealthy"
        assert "error" in data["checks"]["vector_store"]

    def test_no_auth_required(self):
        """Health endpoint must be accessible without login."""
        client = Client()
        with patch("vectorstore.store.get_vector_store") as mock_vs:
            mock_vs.return_value = MagicMock()
            resp = client.get("/healthz/")
        assert resp.status_code == 200
