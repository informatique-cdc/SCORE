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


def contradiction_chart_data(job):
    """Return chart JSON strings for contradictions by severity and classification."""
    from analysis.models import ContradictionPair

    contras = ContradictionPair.objects.filter(analysis_job=job)
    return {
        "contra_by_severity_json": count_by_display(contras, "severity"),
        "contra_by_class_json": count_by_display(contras, "classification"),
    }


def gap_chart_data(job):
    """Return chart JSON strings for gaps by type and severity."""
    from analysis.models import GapReport

    gaps = GapReport.objects.filter(analysis_job=job)
    return {
        "gap_by_type_json": count_by_display(gaps, "gap_type"),
        "gap_by_severity_json": count_by_display(gaps, "severity"),
    }


def hallucination_chart_data(job):
    """Return chart JSON strings for hallucination risks by type and severity."""
    from analysis.models import HallucinationReport

    reports = HallucinationReport.objects.filter(analysis_job=job)
    return {
        "hallu_by_type_json": count_by_display(reports, "risk_type"),
        "hallu_by_severity_json": count_by_display(reports, "severity"),
    }


def duplicate_chart_data(job):
    """Return chart JSON string for duplicate groups by recommended action."""
    from analysis.models import DuplicateGroup

    groups = DuplicateGroup.objects.filter(analysis_job=job)
    return {"dup_by_action_json": count_by_display(groups, "recommended_action")}
