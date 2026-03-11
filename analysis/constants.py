"""Display constants for analysis views and audit."""

from django.utils.translation import gettext_lazy as _lazy

# Audit axis labels (used in views and tasks)
AXIS_LABELS = {
    "hygiene": _lazy("Hygiène du corpus"),
    "structure": _lazy("Structure RAG"),
    "coverage": _lazy("Couverture sémantique"),
    "coherence": _lazy("Cohérence interne"),
    "retrievability": _lazy("Retrievability"),
    "governance": _lazy("Gouvernance & metadata"),
}

# Audit axis labels for pipeline progress messages
AUDIT_AXIS_LABELS = {
    "hygiene": "Audit : Hygiène",
    "structure": "Audit : Structure",
    "coverage": "Audit : Couverture",
    "coherence": "Audit : Cohérence",
    "retrievability": "Audit : Retrievability",
    "governance": "Audit : Gouvernance",
}

# SVG path data for axis icons
AXIS_ICONS = {
    "hygiene": "M9 2.5l-5 5v5l5 5 5-5v-5z",
    "structure": "M2 2h8v3H2zM2 7h8v3H2zM2 12h8v3H2z",
    "coverage": "M1 6a5 5 0 1010 0 5 5 0 01-10 0z",
    "coherence": "M3 3h6v6H3zM7 7h6v6H7z",
    "retrievability": "M5 1v4l-3 3 3 3v4M11 1v4l3 3-3 3v4",
    "governance": "M6 1l5 3v6l-5 3-5-3V4z",
}

AXIS_COLORS = {
    "hygiene": "#f97316",
    "structure": "#0d6efd",
    "coverage": "#34d399",
    "coherence": "#eab308",
    "retrievability": "#a78bfa",
    "governance": "#60a5fa",
}

AXIS_ORDER = ["hygiene", "structure", "coverage", "coherence", "retrievability", "governance"]

# Sub-score labels for dimension breakdowns
SUB_SCORE_LABELS = {
    "uniqueness": _lazy("Unicité"),
    "neardup": _lazy("Quasi-doublons"),
    "boilerplate": _lazy("Boilerplate"),
    "language": _lazy("Langue"),
    "pii": _lazy("Données sensibles"),
    "uniformity": _lazy("Uniformité"),
    "outliers": _lazy("Outliers"),
    "density": _lazy("Densité"),
    "readability": _lazy("Lisibilité"),
    "balance": _lazy("Équilibre"),
    "coverage": _lazy("Couverture"),
    "coherence": _lazy("Cohérence"),
    "kv_conflicts": _lazy("Conflits clé-valeur"),
    "terminology": _lazy("Terminologie"),
    "entities": _lazy("Entités"),
    "mrr": _lazy("MRR"),
    "recall_10": _lazy("Recall@10"),
    "zero_results": _lazy("Résultats non-vides"),
    "diversity": _lazy("Diversité"),
    "completeness": _lazy("Complétude"),
    "freshness": _lazy("Fraîcheur"),
    "orphans": _lazy("Orphelins"),
    "connectivity": _lazy("Connectivité"),
}

SUB_COLORS = ["#f97316", "#eab308", "#34d399", "#60a5fa", "#a78bfa"]
