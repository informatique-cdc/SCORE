"""Tests for authentication flows (signup, login, logout) via allauth."""

import pytest
from django.contrib.auth.models import User
from django.test import Client


@pytest.mark.django_db
class TestSignup:
    def test_signup_page_loads(self):
        client = Client()
        resp = client.get("/auth/signup/")
        assert resp.status_code == 200

    def test_signup_creates_user(self):
        client = Client()
        resp = client.post(
            "/auth/signup/",
            {
                "username": "newuser",
                "email": "new@example.com",
                "password1": "Str0ngP@ss!",
                "password2": "Str0ngP@ss!",
            },
        )
        # allauth redirects on success
        assert resp.status_code in (302, 200)
        assert User.objects.filter(username="newuser").exists()

    def test_signup_duplicate_username_rejected(self):
        User.objects.create_user("taken", "taken@example.com", "pass1234")
        client = Client()
        resp = client.post(
            "/auth/signup/",
            {
                "username": "taken",
                "email": "other@example.com",
                "password1": "Str0ngP@ss!",
                "password2": "Str0ngP@ss!",
            },
        )
        # Should stay on page with errors (200) or re-render
        assert resp.status_code == 200
        assert User.objects.filter(username="taken").count() == 1

    def test_signup_password_mismatch_rejected(self):
        client = Client()
        resp = client.post(
            "/auth/signup/",
            {
                "username": "mismatch",
                "email": "mismatch@example.com",
                "password1": "Str0ngP@ss!",
                "password2": "Different1!",
            },
        )
        assert resp.status_code == 200
        assert not User.objects.filter(username="mismatch").exists()


@pytest.mark.django_db
class TestLogin:
    def test_login_page_loads(self):
        client = Client()
        resp = client.get("/auth/login/")
        assert resp.status_code == 200

    def test_login_valid_credentials(self):
        User.objects.create_user("loginuser", "login@example.com", "Str0ngP@ss!")
        client = Client()
        resp = client.post(
            "/auth/login/",
            {
                "login": "loginuser",
                "password": "Str0ngP@ss!",
            },
        )
        assert resp.status_code == 302  # redirect on success

    def test_login_invalid_password(self):
        User.objects.create_user("badpass", "bad@example.com", "Str0ngP@ss!")
        client = Client()
        resp = client.post(
            "/auth/login/",
            {
                "login": "badpass",
                "password": "wrong",
            },
        )
        assert resp.status_code == 200  # re-renders login form

    def test_login_nonexistent_user(self):
        client = Client()
        resp = client.post(
            "/auth/login/",
            {
                "login": "ghost",
                "password": "anything",
            },
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestLogout:
    def test_logout_redirects(self):
        User.objects.create_user("logoutuser", "logout@example.com", "Str0ngP@ss!")
        client = Client()
        client.login(username="logoutuser", password="Str0ngP@ss!")
        resp = client.post("/auth/logout/")
        assert resp.status_code == 302

    def test_unauthenticated_pages_redirect_to_login(self):
        client = Client()
        resp = client.get("/dashboard/")
        assert resp.status_code == 302
        assert "/auth/login/" in resp.url
