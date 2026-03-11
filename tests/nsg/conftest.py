"""Skip all NSG tests when the spaCy model is not available."""

import pytest


def _spacy_model_available(model: str = "en_core_web_sm") -> bool:
    try:
        import spacy
        spacy.load(model)
        return True
    except Exception:
        return False


requires_spacy = pytest.mark.skipif(
    not _spacy_model_available(),
    reason="spaCy model 'en_core_web_sm' not installed",
)


def pytest_collection_modifyitems(items):
    """Auto-apply the skip marker to every test in this directory."""
    for item in items:
        if "tests/nsg" in str(item.fspath) or "tests\\nsg" in str(item.fspath):
            item.add_marker(requires_spacy)
