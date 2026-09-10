"""Contrat de mise en page des fiches de constat : lacunes et contradictions."""

import pytest
from django.contrib.auth.models import User
from django.test import Client

from analysis.models import AnalysisJob, GapReport, TopicCluster
from tenants.models import Project, ProjectMembership, Tenant, TenantMembership


@pytest.fixture
def report_page(db):
    user = User.objects.create_user("gapuser", "gap@example.com", "pass1234")
    tenant = Tenant.objects.create(name="GapTenant", slug="gap-tenant")
    TenantMembership.objects.create(tenant=tenant, user=user, role=TenantMembership.Role.ADMIN)
    project = Project.objects.create(tenant=tenant, name="GapProject", slug="gap-project")
    ProjectMembership.objects.create(project=project, user=user, role=TenantMembership.Role.ADMIN)
    job = AnalysisJob.objects.create(
        tenant=tenant, project=project, status=AnalysisJob.Status.COMPLETED
    )

    client = Client()
    client.login(username="gapuser", password="pass1234")
    session = client.session
    session["tenant_id"] = str(tenant.id)
    session["project_id"] = str(project.id)
    session.save()
    return client, tenant, project, job


def _gap(tenant, project, job, **kwargs):
    return GapReport.objects.create(
        tenant=tenant,
        project=project,
        analysis_job=job,
        gap_type=kwargs.pop("gap_type", "missing_topic"),
        title=kwargs.pop("title", "Cumul emploi-retraite"),
        description=kwargs.pop("description", "Aucun document ne traite ce sujet."),
        severity=kwargs.pop("severity", "high"),
        **kwargs,
    )


@pytest.mark.django_db
class TestGapCard:
    def test_coverage_leads_the_card(self, report_page):
        """The rate orders the page, so it has to be readable without opening a card."""
        client, tenant, project, job = report_page
        _gap(tenant, project, job, coverage_score=0.22)

        body = client.get(f"/analysis/{job.pk}/gaps/").content.decode()

        assert "sc-gap-cov-value" in body
        assert "22&nbsp;%" in body
        assert "--sc-fill: 22%" in body

    def test_coverage_colour_follows_the_score_scale(self, report_page):
        client, tenant, project, job = report_page
        _gap(tenant, project, job, coverage_score=0.0)

        body = client.get(f"/analysis/{job.pk}/gaps/").content.decode()

        assert "sc-ink--e" in body
        assert "sc-fill--e" in body

    def test_missing_coverage_does_not_fake_a_bar(self, report_page):
        client, tenant, project, job = report_page
        _gap(tenant, project, job, coverage_score=None)

        body = client.get(f"/analysis/{job.pk}/gaps/").content.decode()

        assert "sc-ink--none" in body
        assert "sc-gap-cov-track" not in body

    def test_related_cluster_is_named_inline(self, report_page):
        client, tenant, project, job = report_page
        cluster = TopicCluster.objects.create(
            tenant=tenant, project=project, analysis_job=job, label="Retraite progressive"
        )
        _gap(tenant, project, job, coverage_score=0.4, related_cluster=cluster)

        body = client.get(f"/analysis/{job.pk}/gaps/").content.decode()

        assert "sc-gap-cluster" in body
        assert "Retraite progressive" in body


@pytest.mark.django_db
class TestGapTerms:
    """Evidence terms were stored by the detectors but never shown before."""

    @pytest.mark.parametrize(
        ("evidence", "label", "term"),
        [
            ({"concepts": ["orphelin", "ayant droit"]}, "Concepts isolés", "ayant droit"),
            ({"bridge": ["PASS", "plafond"]}, "Concepts reliés", "PASS"),
            ({"adjacent_clusters": ["Cotisations"]}, "Sujets voisins", "Cotisations"),
        ],
    )
    def test_each_evidence_shape_gets_its_own_wording(self, report_page, evidence, label, term):
        client, tenant, project, job = report_page
        _gap(tenant, project, job, coverage_score=0.1, evidence=evidence)

        body = client.get(f"/analysis/{job.pk}/gaps/").content.decode()

        assert label in body
        assert f'<span class="sc-gap-term" title="{term}">{term}</span>' in body

    def test_no_terms_column_when_the_evidence_carries_none(self, report_page):
        client, tenant, project, job = report_page
        _gap(tenant, project, job, coverage_score=0.6, evidence={"doc_count": 3})

        body = client.get(f"/analysis/{job.pk}/gaps/").content.decode()

        assert "sc-gap-term" not in body


@pytest.mark.django_db
class TestGapActionsStillWork:
    """The redesign moved the buttons into the third column; the wiring must hold."""

    def test_buttons_target_the_per_gap_form(self, report_page):
        client, tenant, project, job = report_page
        gap = _gap(tenant, project, job, coverage_score=0.3)

        body = client.get(f"/analysis/{job.pk}/gaps/").content.decode()

        assert f'form="single-{gap.pk}"' in body
        assert f'id="single-{gap.pk}"' in body
        assert f'form="batch-form" name="selected" value="{gap.pk}"' in body

    def test_lazy_loaded_pages_use_the_same_card(self, report_page):
        """Page 1 and the Turbo frames share one template; they must not drift."""
        client, tenant, project, job = report_page
        for i in range(21):
            _gap(tenant, project, job, title=f"Lacune {i}", coverage_score=0.5)

        body = client.get(
            f"/analysis/{job.pk}/gaps/?page=2", headers={"turbo-frame": "gaps-page-2"}
        ).content.decode()

        assert "sc-gap-cov-value" in body
        assert "sc-gap-actions" in body

    def test_resolving_a_gap_flips_its_state(self, report_page):
        client, tenant, project, job = report_page
        gap = _gap(tenant, project, job, coverage_score=0.3)

        client.post(f"/analysis/{job.pk}/gaps/{gap.pk}/resolve/", {"resolution": "resolved"})

        gap.refresh_from_db()
        assert gap.resolution == "resolved"


# ---------------------------------------------------------------------------
# Contradictions — contrat de mise en page de la fiche
# ---------------------------------------------------------------------------


def _pair(tenant, project, job, connector, **kwargs):
    from analysis.models import Claim, ContradictionPair
    from tests.conftest import make_chunk, make_document

    doc = make_document(tenant, project, connector, title="Notice")
    chunk = make_chunk(tenant, doc, 0, "Contenu")

    def claim(obj):
        return Claim.objects.create(
            tenant=tenant,
            project=project,
            document=doc,
            chunk=chunk,
            subject="Plafond",
            predicate="est de",
            object_value=obj,
            raw_text=f"Le plafond est de {obj}.",
        )

    a, b = claim("40 €"), claim("200 €")
    pair = ContradictionPair.objects.create(
        tenant=tenant,
        project=project,
        analysis_job=job,
        claim_a=a,
        claim_b=b,
        classification=kwargs.pop("classification", "contradiction"),
        severity=kwargs.pop("severity", "high"),
        confidence=kwargs.pop("confidence", 0.94),
        evidence=kwargs.pop("evidence", "Les deux montants diffèrent."),
        **kwargs,
    )
    return pair, a, b


@pytest.mark.django_db
class TestContradictionCard:
    def test_confidence_sits_in_the_header_as_a_decimal(self, report_page, connector):
        """La maquette affiche « Confiance 0,94 », pas un pourcentage."""
        client, tenant, project, job = report_page
        _pair(tenant, project, job, connector)

        body = client.get(f"/analysis/{job.pk}/contradictions/").content.decode()

        assert "sc-contra-head" in body
        assert "Confiance 0,94" in body
        assert "--sc-fill: 94%" in body

    def test_actions_moved_to_the_card_footer(self, report_page, connector):
        client, tenant, project, job = report_page
        pair, _, _ = _pair(tenant, project, job, connector)

        body = client.get(f"/analysis/{job.pk}/contradictions/").content.decode()

        assert "sc-contra-foot" in body
        assert f'form="single-{pair.pk}"' in body

    def test_open_and_closed_states_are_distinguished(self, report_page, connector):
        client, tenant, project, job = report_page
        _pair(tenant, project, job, connector)

        body = client.get(f"/analysis/{job.pk}/contradictions/").content.decode()
        assert "sc-contra-state--open" in body

        from analysis.models import ContradictionPair

        ContradictionPair.objects.update(resolution="resolved")
        body = client.get(f"/analysis/{job.pk}/contradictions/").content.decode()
        assert "sc-contra-state--closed" in body

    def test_the_authoritative_claim_is_marked_on_its_own_side(self, report_page, connector):
        """La maquette fige la référence en B ; ici elle suit la donnée."""
        client, tenant, project, job = report_page
        pair, claim_a, _ = _pair(tenant, project, job, connector, classification="outdated")
        pair.authoritative_claim = claim_a
        pair.save(update_fields=["authoritative_claim"])

        body = client.get(f"/analysis/{job.pk}/contradictions/").content.decode()

        assert body.count("sc-claim-ref") == 1
        assert body.count("sc-claim is-reference") == 1

    def test_lazy_pages_share_the_same_card(self, report_page, connector):
        client, tenant, project, job = report_page
        for _ in range(21):
            _pair(tenant, project, job, connector)

        body = client.get(
            f"/analysis/{job.pk}/contradictions/?page=2",
            headers={"turbo-frame": "contradictions-page-2"},
        ).content.decode()

        assert "sc-contra-head" in body
        assert "sc-contra-foot" in body


@pytest.mark.django_db
class TestContradictionFilters:
    """La maquette fond type et statut en un seul axe exclusif."""

    def test_only_one_filter_is_ever_active(self, report_page, connector):
        client, tenant, project, job = report_page
        _pair(tenant, project, job, connector)

        body = client.get(f"/analysis/{job.pk}/contradictions/?type=outdated").content.decode()

        assert body.count("sc-pill is-active") == 1

    def test_all_is_active_when_nothing_is_filtered(self, report_page, connector):
        client, tenant, project, job = report_page
        _pair(tenant, project, job, connector)

        body = client.get(f"/analysis/{job.pk}/contradictions/").content.decode()

        assert body.count("sc-pill is-active") == 1

    def test_picking_a_filter_clears_the_other_axis(self, report_page, connector):
        """Aucun lien ne porte les deux paramètres : le croisement n'est plus offert."""
        client, tenant, project, job = report_page
        _pair(tenant, project, job, connector)

        body = client.get(f"/analysis/{job.pk}/contradictions/?type=outdated").content.decode()

        assert "&amp;resolution=" not in body
        assert "&amp;type=" not in body

    def test_bulk_bar_sits_above_the_list_not_pinned_to_the_viewport(self, report_page, connector):
        client, tenant, project, job = report_page
        _pair(tenant, project, job, connector)

        body = client.get(f"/analysis/{job.pk}/contradictions/").content.decode()

        assert body.index('id="batch-bar"') < body.index('class="sc-findings"')

    def test_card_actions_use_the_paired_button_variants(self, report_page, connector):
        """--ink et --outline partagent le gabarit ; --sm rétrécissait le couple."""
        client, tenant, project, job = report_page
        _pair(tenant, project, job, connector)

        body = client.get(f"/analysis/{job.pk}/contradictions/").content.decode()

        assert 'class="sc-btn sc-btn--ink"' in body
        assert 'class="sc-btn sc-btn--outline"' in body
        assert "sc-btn--secondary sc-btn--sm" not in body


# ---------------------------------------------------------------------------
# Risques d'hallucination — contrat de mise en page de la fiche
# ---------------------------------------------------------------------------


def _risk(tenant, project, job, **kwargs):
    from analysis.models import HallucinationReport

    return HallucinationReport.objects.create(
        tenant=tenant,
        project=project,
        analysis_job=job,
        risk_type=kwargs.pop("risk_type", "undefined_acronym"),
        title=kwargs.pop("title", "Acronyme non défini : CDC"),
        description=kwargs.pop("description", "L'acronyme « CDC » n'est jamais défini."),
        severity=kwargs.pop("severity", "high"),
        term=kwargs.pop("term", "CDC"),
        doc_count=kwargs.pop("doc_count", 135),
        risk_score=kwargs.pop("risk_score", 1.0),
        **kwargs,
    )


@pytest.mark.django_db
class TestHallucinationCard:
    def test_risk_gets_its_own_column(self, report_page):
        client, tenant, project, job = report_page
        _risk(tenant, project, job, risk_score=0.8)

        body = client.get(f"/analysis/{job.pk}/hallucinations/").content.decode()

        assert "sc-hallu-risk-value" in body
        assert "--sc-fill: 80%" in body

    def test_risk_colour_is_inverted(self, report_page):
        """Un risque élevé est un problème : l'échelle va à l'envers du SCORE."""
        client, tenant, project, job = report_page
        _risk(tenant, project, job, risk_score=1.0)

        body = client.get(f"/analysis/{job.pk}/hallucinations/").content.decode()

        assert "sc-ink--e" in body
        assert "sc-fill--e" in body

    def test_term_and_type_lead_the_card(self, report_page):
        client, tenant, project, job = report_page
        _risk(tenant, project, job, term="IPSEC")

        body = client.get(f"/analysis/{job.pk}/hallucinations/").content.decode()

        assert '<span class="sc-term">IPSEC</span>' in body
        assert "sc-hallu-type" in body

    def test_counts_sit_under_the_risk_bar(self, report_page):
        client, tenant, project, job = report_page
        _risk(tenant, project, job, doc_count=135)

        body = client.get(f"/analysis/{job.pk}/hallucinations/").content.decode()

        assert "135 documents" in body
        assert "aucune définition trouvée" in body

    def test_known_expansions_are_counted(self, report_page):
        client, tenant, project, job = report_page
        _risk(
            tenant,
            project,
            job,
            risk_type="conflicting_acronym",
            expansions=[{"expansion": "contrat de droit public"}, {"expansion": "cdp longs"}],
        )

        body = client.get(f"/analysis/{job.pk}/hallucinations/").content.decode()

        assert "2 définitions trouvées" in body

    def test_the_screen_states_what_it_measures(self, report_page):
        """Le bandeau porte la distinction que l'écran repose sur le corpus."""
        client, tenant, project, job = report_page
        _risk(tenant, project, job)

        body = client.get(f"/analysis/{job.pk}/hallucinations/").content.decode()

        assert "sc-card--dark" in body
        assert "Ce que mesure cet écran" in body
        assert "{#" not in body

    def test_lazy_pages_share_the_same_card(self, report_page):
        client, tenant, project, job = report_page
        for i in range(21):
            _risk(tenant, project, job, term=f"T{i}")

        body = client.get(
            f"/analysis/{job.pk}/hallucinations/?page=2",
            headers={"turbo-frame": "hallu-page-2"},
        ).content.decode()

        assert "sc-hallu-risk-value" in body
        assert "sc-hallu-actions" in body
