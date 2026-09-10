"""Tests de la page vitrine publique."""

from django.urls import reverse


def test_anonymous_gets_the_landing_page(client):
    response = client.get(reverse("vitrine-home"))

    assert response.status_code == 200
    assert b"Un Nutri-Score pour vos bases de connaissances." in response.content


def test_authenticated_user_is_sent_to_the_dashboard(client, user, admin_membership):
    client.force_login(user)

    response = client.get(reverse("vitrine-home"))

    assert response.status_code == 302
    assert response.url == reverse("dashboard-home")


def test_repo_figures_come_from_settings(client, settings):
    settings.VITRINE = {**settings.VITRINE, "stars": 1284, "contributors": 37}

    content = client.get(reverse("vitrine-home")).content.decode()

    # `intcomma` applique le séparateur de milliers français (espace insécable).
    assert "1 284" in content
    assert "37" in content


def test_missing_figures_render_as_a_dash(client, settings):
    """Un compteur inconnu ne doit pas s'afficher en zéro, qui se lirait comme un fait."""
    settings.VITRINE = {**settings.VITRINE, "stars": 0, "contributors": 0, "open_issues": 0}

    content = client.get(reverse("vitrine-home")).content.decode()

    assert "—" in content
    assert ">0<" not in content


def test_version_pill_is_hidden_until_a_release_exists(client, settings):
    settings.VITRINE = {**settings.VITRINE, "version": "", "branch": "main"}

    content = client.get(reverse("vitrine-home")).content.decode()

    assert "sc-v-brand-tag" not in content

    settings.VITRINE = {**settings.VITRINE, "version": "v1.0.0"}

    content = client.get(reverse("vitrine-home")).content.decode()

    assert "sc-v-brand-tag" in content
    assert "v1.0.0" in content
