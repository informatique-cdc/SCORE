"""Presenter functions for chart data preparation.

Extract chart data from querysets into JSON-serializable structures,
keeping presentation logic out of view functions.
"""

import json


def _group_by_display(queryset, field_name):
    """Group queryset items by a display field and return name/value pairs.

    Parameters
    ----------
    queryset : QuerySet
        Django queryset to iterate over.
    field_name : str
        Name of the model field whose ``get_<field>_display()`` will be used.

    Returns
    -------
    str
        JSON string of ``[{"name": ..., "value": ...}, ...]``.
    """
    counts = {}
    getter = f"get_{field_name}_display"
    for obj in queryset:
        label = str(getattr(obj, getter)())
        counts[label] = counts.get(label, 0) + 1
    return json.dumps([{"name": k, "value": v} for k, v in counts.items()])


def contradiction_chart_data(job):
    """Return chart JSON strings for contradictions by severity and classification."""
    from analysis.models import ContradictionPair

    contras = ContradictionPair.objects.filter(analysis_job=job)
    return {
        "contra_by_severity_json": _group_by_display(contras, "severity"),
        "contra_by_class_json": _group_by_display(contras, "classification"),
    }


def gap_chart_data(job):
    """Return chart JSON strings for gaps by type and severity."""
    from analysis.models import GapReport

    gaps = GapReport.objects.filter(analysis_job=job)
    return {
        "gap_by_type_json": _group_by_display(gaps, "gap_type"),
        "gap_by_severity_json": _group_by_display(gaps, "severity"),
    }


def hallucination_chart_data(job):
    """Return chart JSON strings for hallucination risks by type and severity."""
    from analysis.models import HallucinationReport

    reports = HallucinationReport.objects.filter(analysis_job=job)
    return {
        "hallu_by_type_json": _group_by_display(reports, "risk_type"),
        "hallu_by_severity_json": _group_by_display(reports, "severity"),
    }
