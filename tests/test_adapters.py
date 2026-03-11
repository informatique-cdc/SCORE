"""Tests for tenants/adapters.py — custom allauth adapter."""
from django.test import RequestFactory

from tenants.adapters import ScoreAccountAdapter


class TestScoreAccountAdapter:
    def test_login_redirect_url(self):
        adapter = ScoreAccountAdapter()
        factory = RequestFactory()
        request = factory.get("/")
        assert adapter.get_login_redirect_url(request) == "/dashboard/"
