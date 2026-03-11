"""Tests for ContentSecurityPolicyMiddleware."""

from unittest.mock import patch, MagicMock

import pytest
from django.test import Client


@pytest.mark.django_db
class TestCSPMiddleware:
    def test_csp_header_present(self):
        """CSP header should be added to every response."""
        client = Client()
        with patch("vectorstore.store.get_vector_store", return_value=MagicMock()):
            resp = client.get("/healthz/")
        assert "Content-Security-Policy" in resp

    def test_csp_blocks_unsafe_eval(self):
        client = Client()
        with patch("vectorstore.store.get_vector_store", return_value=MagicMock()):
            resp = client.get("/healthz/")
        csp = resp["Content-Security-Policy"]
        assert "unsafe-eval" not in csp

    def test_csp_allows_self(self):
        client = Client()
        with patch("vectorstore.store.get_vector_store", return_value=MagicMock()):
            resp = client.get("/healthz/")
        csp = resp["Content-Security-Policy"]
        assert "'self'" in csp

    def test_csp_blocks_object(self):
        client = Client()
        with patch("vectorstore.store.get_vector_store", return_value=MagicMock()):
            resp = client.get("/healthz/")
        csp = resp["Content-Security-Policy"]
        assert "object-src 'none'" in csp
