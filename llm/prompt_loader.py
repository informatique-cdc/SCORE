"""
Language-aware prompt loader.

Usage:
    from llm.prompt_loader import get_prompt
    prompt = get_prompt("CHAT_QA_SYSTEM")

Returns the prompt string for the currently active Django language.
"""

from django.utils import translation

# Lazy-import modules to avoid circular imports at module level.
_modules = {}


def _get_module(name):
    if name not in _modules:
        if name == "prompts_fr":
            import llm.prompts as mod
        elif name == "prompts_en":
            import llm.prompts_en as mod
        elif name == "prompts_rag_fr":
            import llm.prompts_rag as mod
        elif name == "prompts_rag_en":
            import llm.prompts_rag_en as mod
        else:
            raise ValueError(f"Unknown module: {name}")
        _modules[name] = mod
    return _modules[name]


def get_prompt(name: str) -> str:
    """Return the prompt constant *name* in the active language.

    Looks up the constant in prompts.py / prompts_en.py (or the _rag variants)
    depending on ``translation.get_language()``.  Falls back to French if the
    requested constant is missing in the target language module.
    """
    lang = (translation.get_language() or "fr")[:2]

    # Determine which pair of modules to search
    # Try main prompts first, then RAG prompts
    for base in ("prompts", "prompts_rag"):
        mod_key = f"{base}_{lang}" if lang == "en" else f"{base}_fr"
        fallback_key = f"{base}_fr"

        mod = _get_module(mod_key)
        val = getattr(mod, name, None)
        if val is not None:
            return val

        # Fallback to French
        if mod_key != fallback_key:
            fallback = _get_module(fallback_key)
            val = getattr(fallback, name, None)
            if val is not None:
                return val

    raise AttributeError(f"Prompt '{name}' not found in any prompt module")
