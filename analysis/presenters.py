"""Presenter functions for chart data preparation.

Extract chart data from querysets into JSON-serializable structures,
keeping presentation logic out of view functions.
"""

import json

from django.db.models import Count


def count_by_display(queryset, field_name):
    """Group queryset rows by a choice field and return name/value pairs.

    Le comptage est fait par la base : la variante qui itérait le queryset en
    Python chargeait la table entière pour n'en tirer que quelques totaux.

    Parameters
    ----------
    queryset : QuerySet
        Django queryset to aggregate over.
    field_name : str
        Name of the model field whose ``choices`` provide the display labels.

    Returns
    -------
    str
        JSON string of ``[{"name": ..., "value": ...}, ...]``.
    """
    labels = dict(queryset.model._meta.get_field(field_name).choices or [])
    rows = queryset.values(field_name).annotate(count=Count("id")).order_by()
    return json.dumps(
        [
            {"name": str(labels.get(row[field_name], row[field_name])), "value": row["count"]}
            for row in rows
        ]
    )


# Score-scale tones for the findings cards. Keyed on the *stored* choice value,
# never on its translation: the chart layer used display labels as colour keys,
# so changing a translation silently recoloured the card.
_SEVERITY_TONES = {"high": "e", "medium": "d", "low": "a"}
_ACTION_TONES = {"merge": "b", "delete_older": "e", "review": "d", "keep": "a"}
# Gap and risk types have no ranking, so the tone only separates the bars.
_GAP_TYPE_TONES = ("c", "b")
_RISK_TYPE_TONES = ("d", "c")


def distribution_bars(queryset, field_name, tones):
    """Break a choice field down into the mini-bars of a findings card.

    ``tones`` maps a raw choice value to a score-scale letter, or is a sequence
    cycled over the bars when the field carries no ranking.
    """
    labels = dict(queryset.model._meta.get_field(field_name).choices or [])
    counts = {
        row[field_name]: row["count"]
        for row in queryset.values(field_name).annotate(count=Count("id")).order_by()
    }
    if not counts:
        return []

    tallest = max(counts.values())
    # Declaration order, not count order: a card must not reshuffle its colours
    # between two analyses of the same corpus. Values stored outside the choices
    # still get a bar, or the breakdown would not add up to the headline count.
    ordered = [k for k in labels if k in counts] + [k for k in counts if k not in labels]
    bars = []
    for index, key in enumerate(ordered):
        count = counts[key]
        bars.append(
            {
                "label": str(labels.get(key, key)),
                "value": count,
                # Floored: one item next to two hundred still has to be visible.
                "pct": max(6, round(count / tallest * 100)),
                "tone": tones.get(key, "c")
                if isinstance(tones, dict)
                else tones[index % len(tones)],
            }
        )
    return bars


def contradiction_chart_data(job):
    """Return chart JSON strings for contradictions by severity and classification."""
    from analysis.models import ContradictionPair

    contras = ContradictionPair.objects.filter(analysis_job=job)
    return {
        "contra_bars": distribution_bars(contras, "severity", _SEVERITY_TONES),
        "contra_by_class_json": count_by_display(contras, "classification"),
    }


def gap_chart_data(job):
    """Return chart JSON strings for gaps by type and severity."""
    from analysis.models import GapReport

    gaps = GapReport.objects.filter(analysis_job=job)
    return {
        "gap_bars": distribution_bars(gaps, "gap_type", _GAP_TYPE_TONES),
        "gap_by_severity_json": count_by_display(gaps, "severity"),
    }


def hallucination_chart_data(job):
    """Return chart JSON strings for hallucination risks by type and severity."""
    from analysis.models import HallucinationReport

    reports = HallucinationReport.objects.filter(analysis_job=job)
    return {
        "hallu_bars": distribution_bars(reports, "risk_type", _RISK_TYPE_TONES),
        "hallu_by_severity_json": count_by_display(reports, "severity"),
    }


def duplicate_chart_data(job):
    """Return chart JSON string for duplicate groups by recommended action."""
    from analysis.models import DuplicateGroup

    groups = DuplicateGroup.objects.filter(analysis_job=job)
    return {"dup_bars": distribution_bars(groups, "recommended_action", _ACTION_TONES)}
