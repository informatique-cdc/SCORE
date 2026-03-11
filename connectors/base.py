"""
Base connector interface and registry.

Each connector implements `list_documents()` and `fetch_content()`.
Documents are yielded as RawDocument dataclasses for the ingestion pipeline.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class RawDocument:
    """A document fetched from a source, before text extraction."""

    source_id: str
    title: str
    content: bytes | str  # Raw content (bytes for binary, str for text/HTML)
    content_type: str  # MIME type or file extension
    source_url: str = ""
    author: str = ""
    path: str = ""
    doc_type: str = ""
    source_version: str = ""
    source_created_at: datetime | None = None
    source_modified_at: datetime | None = None
    metadata: dict = field(default_factory=dict)


class BaseConnector(ABC):
    """Abstract base for all document source connectors."""

    def __init__(self, config: dict, credential: str = ""):
        self.config = config
        self.credential = credential

    @abstractmethod
    def test_connection(self) -> bool:
        """Verify that the connection is valid. Returns True if successful."""
        ...

    @abstractmethod
    def list_documents(self) -> list[dict]:
        """
        List all available documents with basic metadata.
        Returns list of dicts with at minimum: source_id, title, source_version, source_modified_at
        Used for incremental sync — compare with stored versions.
        """
        ...

    @abstractmethod
    def fetch_document(self, source_id: str) -> RawDocument:
        """Fetch full content of a single document."""
        ...

    def list_changed_documents(
        self, known_versions: dict[str, str]
    ) -> tuple[list[dict], list[str]]:
        """
        Compare source documents against known versions.
        Returns (new_or_changed, deleted_source_ids).
        """
        current_docs = self.list_documents()
        current_ids = {d["source_id"] for d in current_docs}
        known_ids = set(known_versions.keys())

        new_or_changed = []
        for doc in current_docs:
            sid = doc["source_id"]
            if sid not in known_versions or known_versions[sid] != doc.get("source_version", ""):
                new_or_changed.append(doc)

        deleted = list(known_ids - current_ids)
        return new_or_changed, deleted


# --- Connector registry ---
_REGISTRY: dict[str, type[BaseConnector]] = {}


def register_connector(name: str):
    """Decorator to register a connector class."""

    def decorator(cls):
        _REGISTRY[name] = cls
        return cls

    return decorator


def get_connector(connector_type: str, config: dict, credential: str = "") -> BaseConnector:
    """Instantiate a connector by type name."""
    cls = _REGISTRY.get(connector_type)
    if cls is None:
        raise ValueError(
            f"Unknown connector type: {connector_type}. Available: {list(_REGISTRY.keys())}"
        )
    return cls(config=config, credential=credential)
