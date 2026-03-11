"""Tests for tenants/adapters.py — custom allauth adapter."""
from django.test import RequestFactory

from tenants.adapters import DocuScoreAccountAdapter


class TestDocuScoreAccountAdapter:
    def test_login_redirect_url(self):
        adapter = DocuScoreAccountAdapter()
        factory = RequestFactory()
        request = factory.get("/")
        assert adapter.get_login_redirect_url(request) == "/dashboard/"
