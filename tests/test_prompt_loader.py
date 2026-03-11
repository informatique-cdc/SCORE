"""Tests for llm/prompt_loader.py — language-aware prompt dispatch."""
import pytest
from unittest.mock import patch

from llm.prompt_loader import get_prompt, _modules


@pytest.fixture(autouse=True)
def clear_module_cache():
    """Clear the cached modules before each test."""
    _modules.clear()
    yield
    _modules.clear()


class TestGetPrompt:
    def test_loads_french_prompt(self):
        with patch("django.utils.translation.get_language", return_value="fr"):
            prompt = get_prompt("CLAIM_EXTRACTION")
            assert isinstance(prompt, str)
            assert len(prompt) > 0

    def test_loads_english_prompt(self):
        with patch("django.utils.translation.get_language", return_value="en"):
            prompt = get_prompt("CLAIM_EXTRACTION")
            assert isinstance(prompt, str)
            assert len(prompt) > 0

    def test_fallback_to_french(self):
        """If a prompt doesn't exist in English, falls back to French."""
        with patch("django.utils.translation.get_language", return_value="en"):
            prompt = get_prompt("CHAT_QA_SYSTEM")
            assert isinstance(prompt, str)

    def test_missing_prompt_raises(self):
        with patch("django.utils.translation.get_language", return_value="fr"):
            with pytest.raises(AttributeError, match="not found"):
                get_prompt("THIS_PROMPT_DOES_NOT_EXIST_ANYWHERE")

    def test_none_language_defaults_to_french(self):
        with patch("django.utils.translation.get_language", return_value=None):
            prompt = get_prompt("CLAIM_EXTRACTION")
            assert isinstance(prompt, str)

    def test_caches_modules(self):
        with patch("django.utils.translation.get_language", return_value="fr"):
            get_prompt("CLAIM_EXTRACTION")
            assert "prompts_fr" in _modules

    def test_rag_prompts_accessible(self):
        """RAG-specific prompts should be found through the loader."""
        with patch("django.utils.translation.get_language", return_value="fr"):
            prompt = get_prompt("CHAT_QA_SYSTEM")
            assert isinstance(prompt, str)
            assert len(prompt) > 0
