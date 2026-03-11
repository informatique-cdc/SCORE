"""Tests for score/ratelimit.py — per-user rate limiting decorator."""
import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory

from score.ratelimit import ratelimit


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def _make_view():
    """Create a simple view decorated with ratelimit."""
    @ratelimit(max_calls=3, period=60)
    def my_view(request):
        return HttpResponse("ok")
    return my_view


@pytest.mark.django_db
class TestRateLimit:
    def test_allows_under_limit(self):
        factory = RequestFactory()
        user = User.objects.create_user("rater", "r@example.com", "pass1234")
        view = _make_view()

        request = factory.get("/")
        request.user = user

        for _ in range(3):
            resp = view(request)
            assert resp.status_code == 200

    def test_blocks_over_limit(self):
        factory = RequestFactory()
        user = User.objects.create_user("rater2", "r2@example.com", "pass1234")
        view = _make_view()

        request = factory.get("/")
        request.user = user

        for _ in range(3):
            view(request)

        resp = view(request)
        assert resp.status_code == 429
        data = resp.json() if hasattr(resp, "json") else {}
        assert "error" in data or resp.status_code == 429

    def test_different_users_independent(self):
        factory = RequestFactory()
        user1 = User.objects.create_user("u1", "u1@x.com", "pass1234")
        user2 = User.objects.create_user("u2", "u2@x.com", "pass1234")
        view = _make_view()

        req1 = factory.get("/")
        req1.user = user1
        req2 = factory.get("/")
        req2.user = user2

        for _ in range(3):
            view(req1)

        # User 1 is now rate limited
        resp1 = view(req1)
        assert resp1.status_code == 429

        # User 2 should still be allowed
        resp2 = view(req2)
        assert resp2.status_code == 200

    def test_anonymous_user(self):
        factory = RequestFactory()
        view = _make_view()

        request = factory.get("/")
        request.user = type("AnonymousUser", (), {"pk": None})()

        for _ in range(3):
            resp = view(request)
            assert resp.status_code == 200

        resp = view(request)
        assert resp.status_code == 429
