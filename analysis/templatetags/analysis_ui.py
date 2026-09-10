"""Barre d'onglets partagée par tous les écrans d'une analyse.

Les compteurs sont calculés ici plutôt que dans chacune des dix vues concernées :
les vues de rapport ne reçoivent que leur propre jeu de données.
"""

from django import template
from django.utils.translation import gettext_lazy as _

from analysis.models import (
    ContradictionPair,
    DuplicateGroup,
    GapReport,
    HallucinationReport,
    TopicCluster,
)

register = template.Library()


def _open_count(model, job):
    """Findings non résolus — c'est ce que l'onglet doit annoncer."""
    return model.objects.filter(analysis_job=job).exclude(resolution="resolved").count()


@register.filter
def percent(value):
    """Ratio 0-1 en pourcentage entier, pour une largeur de barre."""
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return 0


@register.filter
def duplicate_severity(value):
    """Palier de couleur d'un score de similarité.

    Barème inversé par rapport au SCORE : ici une valeur haute est un problème.
    """
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "none"
    if score >= 0.9:
        return "e"
    if score >= 0.7:
        return "d"
    return "c"


@register.filter
def gap_terms(gap):
    """Termes que la preuve rattache à la lacune, avec l'intitulé qui les décrit.

    Les trois formes proviennent de détecteurs différents et ne désignent pas la
    même chose : un îlot conceptuel liste ce qui est coupé du reste, un pont
    fragile ses deux extrémités, un sujet manquant les voisins qui l'encadrent.
    Un intitulé unique ferait lire les trois comme des manques.
    """
    evidence = gap.evidence or {}
    for key, label in (
        ("concepts", _("Concepts isolés")),
        ("bridge", _("Concepts reliés")),
        ("adjacent_clusters", _("Sujets voisins")),
    ):
        items = evidence.get(key)
        if items:
            return {"label": label, "items": [str(i) for i in items][:8]}
    return None


@register.filter
def severity_level(value):
    """Normalise les libellés de sévérité vers high / medium / low."""
    key = str(value or "").lower()
    if key in {"high", "critical", "haute", "élevée"}:
        return "high"
    if key in {"medium", "moyenne", "moyen"}:
        return "medium"
    return "low"


@register.inclusion_tag("analysis/_tabs.html")
def analysis_tabs(job, current=""):
    tabs = [
        ("overview", _("Vue d'ensemble"), "analysis-detail", None),
        (
            "duplicates",
            _("Doublons"),
            "analysis-duplicates",
            DuplicateGroup.objects.filter(analysis_job=job).count(),
        ),
        (
            "contradictions",
            _("Contradictions"),
            "analysis-contradictions",
            _open_count(ContradictionPair, job),
        ),
        ("gaps", _("Lacunes"), "analysis-gaps", _open_count(GapReport, job)),
        (
            "hallucinations",
            _("Risques d'hallucination"),
            "analysis-hallucinations",
            _open_count(HallucinationReport, job),
        ),
        (
            "clusters",
            _("Clusters"),
            "analysis-clusters",
            TopicCluster.objects.filter(analysis_job=job, level=0).count(),
        ),
        ("audit", _("Audit RAG"), "analysis-audit-overview", None),
        ("trace", _("Trace du pipeline"), "analysis-trace", None),
    ]
    return {
        "job": job,
        "current": current,
        "tabs": [
            {"key": key, "label": label, "url_name": url_name, "count": count}
            for key, label, url_name, count in tabs
        ],
    }
