"""Filtres de présentation du SCORE.

La maquette utilise deux barèmes distincts et volontairement décalés :
`gradeOf` pour la lettre A-E affichée, `colorFor` pour la couleur des barres.
Un score de 62 porte donc une barre verte et une pastille « C ». Ne pas les fusionner.
"""

from django import template

register = template.Library()

_GRADE_THRESHOLDS = ((80, "a"), (65, "b"), (50, "c"), (35, "d"))
_COLOR_THRESHOLDS = ((70, "a"), (55, "b"), (40, "c"), (25, "d"))


def _bucket(value, thresholds):
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "none"
    for minimum, key in thresholds:
        if score >= minimum:
            return key
    return "e"


@register.filter
def score_color(value):
    """Classe de couleur d'une barre ou d'une pastille, barème colorFor."""
    return _bucket(value, _COLOR_THRESHOLDS)


@register.filter
def score_grade(value):
    """Lettre A-E d'un score, barème gradeOf."""
    return _bucket(value, _GRADE_THRESHOLDS).upper()
