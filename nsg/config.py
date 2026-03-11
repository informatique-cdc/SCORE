"""Default configuration for the Neural Semantic Graph pipeline."""

from dataclasses import dataclass, field


@dataclass
class NSGConfig:
    """All tuneable knobs live here."""

    # --- Embedding ---
    embedding_model: str = "all-MiniLM-L6-v2"

    # --- Chunking ---
    chunk_max_chars: int = 800

    # --- Query defaults ---
    top_k: int = 12
    hops: int = 2
    max_nodes: int = 80

    # --- Evidence ---
    evidence_cap: int = 5  # max evidence snippets stored per edge

    # --- Optional synonym merging ---
    synonym_threshold: float = 0.85  # cosine similarity above which two concepts merge

    # --- spaCy model ---
    spacy_model: str = "en_core_web_sm"
