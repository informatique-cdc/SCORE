"""Tests for the findings-card distributions rendered as design-system mini-bars."""

import pytest

from analysis.models import DuplicateGroup, GapReport
from analysis.presenters import (
    _ACTION_TONES,
    _GAP_TYPE_TONES,
    distribution_bars,
)


def make_groups(tenant, project, job, **counts):
    for action, n in counts.items():
        for _ in range(n):
            DuplicateGroup.objects.create(
                tenant=tenant, project=project, analysis_job=job, recommended_action=action
            )
    return DuplicateGroup.objects.filter(analysis_job=job)


@pytest.mark.django_db
class TestDistributionBars:
    def test_heights_are_relative_to_the_tallest_bar(self, tenant, project, analysis_job):
        groups = make_groups(tenant, project, analysis_job, merge=10, review=5)

        bars = distribution_bars(groups, "recommended_action", _ACTION_TONES)

        assert [(b["label"], b["value"], b["pct"]) for b in bars] == [
            ("Fusionner", 10, 100),
            ("Vérification manuelle", 5, 50),
        ]

    def test_a_lone_item_still_gets_a_visible_bar(self, tenant, project, analysis_job):
        """Scaled honestly, 1 against 200 rounds to 0 % and the bar disappears."""
        groups = make_groups(tenant, project, analysis_job, merge=200, review=1)

        bars = distribution_bars(groups, "recommended_action", _ACTION_TONES)

        assert bars[1]["value"] == 1
        assert bars[1]["pct"] == 6

    def test_bars_follow_the_declaration_order_not_the_counts(self, tenant, project, analysis_job):
        """Ordering by count would reshuffle a card's colours between two runs."""
        groups = make_groups(tenant, project, analysis_job, review=9, merge=1)

        bars = distribution_bars(groups, "recommended_action", _ACTION_TONES)

        assert [b["label"] for b in bars] == ["Fusionner", "Vérification manuelle"]

    def test_tones_are_keyed_on_the_stored_value_not_the_label(self, tenant, project, analysis_job):
        """Colours used to be indexed by translated labels, so a wording change
        silently recoloured the card."""
        groups = make_groups(tenant, project, analysis_job, merge=1, delete_older=1, keep=1)

        tones = {
            b["label"]: b["tone"]
            for b in distribution_bars(groups, "recommended_action", _ACTION_TONES)
        }

        assert tones["Fusionner"] == "b"
        assert tones["Supprimer l'ancien"] == "e"
        assert tones["Conserver les deux (liés, pas doublons)"] == "a"

    def test_unranked_types_cycle_through_the_tones(self, tenant, project, analysis_job):
        for gap_type in ("missing_topic", "low_coverage", "stale_area"):
            GapReport.objects.create(
                tenant=tenant, project=project, analysis_job=analysis_job, gap_type=gap_type
            )
        gaps = GapReport.objects.filter(analysis_job=analysis_job)

        bars = distribution_bars(gaps, "gap_type", _GAP_TYPE_TONES)

        assert [b["tone"] for b in bars] == ["c", "b", "c"]

    def test_no_rows_means_no_bars(self, analysis_job):
        groups = DuplicateGroup.objects.filter(analysis_job=analysis_job)

        assert distribution_bars(groups, "recommended_action", _ACTION_TONES) == []
