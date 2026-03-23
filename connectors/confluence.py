"""
Confluence connector.

Requires: pip install atlassian-python-api
Config keys: url, space_key, username (optional — can use credential_ref for API token)
"""

import logging
import os
from datetime import datetime

from .base import BaseConnector, RawDocument, register_connector

logger = logging.getLogger(__name__)


@register_connector("confluence")
class ConfluenceConnector(BaseConnector):
    """Connect to Confluence Cloud or Server via REST API."""

    def __init__(self, config: dict, credential: str = ""):
        super().__init__(config, credential)
        self._url = config.get("url", os.environ.get("CONFLUENCE_URL", ""))
        self._space_key = config.get("space_key", "")
        self._username = config.get("username", os.environ.get("CONFLUENCE_USERNAME", ""))
        self._api_token = credential or os.environ.get("CONFLUENCE_API_TOKEN", "")
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from atlassian import Confluence
            except ImportError:
                raise ImportError("Install confluence extras: pip install score[confluence]")
            self._client = Confluence(
                url=self._url,
                username=self._username,
                password=self._api_token,
                cloud=True,
            )
        return self._client

    def test_connection(self) -> bool:
        try:
            client = self._get_client()
            client.get_space(self._space_key)
            return True
        except (ConnectionError, TimeoutError, OSError, ValueError) as e:
            logger.warning("Confluence connection test failed: %s", e)
            return False

    def list_documents(self) -> list[dict]:
        """List all pages in the configured Confluence space."""
        client = self._get_client()
        docs = []
        start = 0
        limit = 50

        while True:
            results = client.get_all_pages_from_space(
                self._space_key,
                start=start,
                limit=limit,
                expand="version,history.lastUpdated",
            )
            if not results:
                break
            for page in results:
                version = page.get("version", {})
                history = page.get("history", {})
                last_updated = history.get("lastUpdated", {})
                docs.append(
                    {
                        "source_id": page["id"],
                        "title": page["title"],
                        "source_version": str(version.get("number", "")),
                        "source_url": f"{self._url}/wiki/spaces/{self._space_key}/pages/{page['id']}",
                        "source_modified_at": last_updated.get("when", ""),
                        "author": last_updated.get("by", {}).get("displayName", ""),
                        "content_type": "text/html",
                    }
                )
            if len(results) < limit:
                break
            start += limit

        return docs

    def fetch_document(self, source_id: str) -> RawDocument:
        """Fetch a Confluence page with its body content."""
        client = self._get_client()
        page = client.get_page_by_id(
            source_id,
            expand="body.storage,version,history.lastUpdated,ancestors",
        )

        body_html = page.get("body", {}).get("storage", {}).get("value", "")
        version = page.get("version", {})
        history = page.get("history", {})
        last_updated = history.get("lastUpdated", {})

        modified_at = None
        when = last_updated.get("when", "")
        if when:
            try:
                modified_at = datetime.fromisoformat(when.replace("Z", "+00:00"))
            except ValueError:
                pass

        # Build path from ancestors
        ancestors = page.get("ancestors", [])
        path = " > ".join(a.get("title", "") for a in ancestors)

        return RawDocument(
            source_id=source_id,
            title=page.get("title", ""),
            content=body_html,
            content_type="text/html",
            source_url=f"{self._url}/wiki/spaces/{self._space_key}/pages/{source_id}",
            author=last_updated.get("by", {}).get("displayName", ""),
            path=path,
            doc_type="confluence_page",
            source_version=str(version.get("number", "")),
            source_modified_at=modified_at,
        )
