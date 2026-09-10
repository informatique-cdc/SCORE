"""Marqueurs de disponibilité des modèles spaCy pour les tests NSG.

Ce module ne saute plus l'ensemble du répertoire. Le découpage, la
normalisation, le filtre de langue, l'index vectoriel et les stopwords sont du
code pur : les sauter revenait à ne jamais les exécuter, alors même que c'est
là que se joue la qualité des concepts extraits. Seules les classes qui
chargent réellement un modèle portent désormais le marqueur.
"""

import pytest


def _model_available(model: str) -> bool:
    try:
        import spacy

        spacy.load(model)
        return True
    except Exception:
        return False


def requires_model(model: str):
    """Saute la classe ou le test si le modèle spaCy demandé est absent."""
    return pytest.mark.skipif(
        not _model_available(model),
        reason=f"spaCy model '{model}' not installed",
    )


# Les tests historiques s'appuient sur le modèle anglais, valeur par défaut de
# NSGConfig ; le produit, lui, tourne en français (config.yaml).
requires_english_model = requires_model("en_core_web_sm")
requires_french_model = requires_model("fr_core_news_sm")
