"""
SharePoint Online connector.

Requires: pip install msal office365-rest-python-client
Config keys: site_url, drive_id (optional), folder_path (optional)
Credential ref: env var name containing client_secret (SHAREPOINT_CLIENT_SECRET)
"""

import logging
import os
from datetime import datetime

from .base import BaseConnector, RawDocument, register_connector

logger = logging.getLogger(__name__)


@register_connector("sharepoint")
class SharePointConnector(BaseConnector):
    """Connect to SharePoint Online via Microsoft Graph API."""

    def __init__(self, config: dict, credential: str = ""):
        super().__init__(config, credential)
        self._site_url = config.get("site_url", "")
        self._drive_id = config.get("drive_id", "")
        self._folder_path = config.get("folder_path", "/")
        self._client_id = config.get("client_id", os.environ.get("SHAREPOINT_CLIENT_ID", ""))
        self._tenant_id = config.get("tenant_id", os.environ.get("SHAREPOINT_TENANT_ID", ""))
        self._client_secret = os.environ.get(credential, "") if credential else ""
        self._access_token: str | None = None

    def _authenticate(self):
        """Obtain access token via MSAL client credentials flow."""
        try:
            import msal
        except ImportError:
            raise ImportError("Install sharepoint extras: pip install score[sharepoint]")

        authority = f"https://login.microsoftonline.com/{self._tenant_id}"
        app = msal.ConfidentialClientApplication(
            self._client_id,
            authority=authority,
            client_credential=self._client_secret,
        )
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" in result:
            self._access_token = result["access_token"]
        else:
            raise ConnectionError(
                f"SharePoint auth failed: {result.get('error_description', 'unknown')}"
            )

    def _graph_request(self, endpoint: str) -> dict:
        """Make a Microsoft Graph API request."""
        import httpx

        if not self._access_token:
            self._authenticate()

        url = f"https://graph.microsoft.com/v1.0{endpoint}"
        resp = httpx.get(url, headers={"Authorization": f"Bearer {self._access_token}"}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def test_connection(self) -> bool:
        try:
            self._authenticate()
            return True
        except (ConnectionError, TimeoutError, OSError, ValueError) as e:
            logger.warning("SharePoint connection test failed: %s", e)
            return False

    def list_documents(self) -> list[dict]:
        """List files in the configured SharePoint drive/folder."""
        endpoint = f"/sites/{self._site_url}/drive/root:/{self._folder_path.strip('/')}:/children"
        if self._drive_id:
            endpoint = f"/drives/{self._drive_id}/root:/{self._folder_path.strip('/')}:/children"

        data = self._graph_request(endpoint)
        docs = []
        for item in data.get("value", []):
            if "file" not in item:
                continue  # skip folders
            docs.append(
                {
                    "source_id": item["id"],
                    "title": item["name"],
                    "source_version": item.get("eTag", ""),
                    "source_url": item.get("webUrl", ""),
                    "source_modified_at": item.get("lastModifiedDateTime", ""),
                    "author": item.get("lastModifiedBy", {}).get("user", {}).get("displayName", ""),
                    "content_type": item.get("file", {}).get("mimeType", ""),
                    "size": item.get("size", 0),
                }
            )
        return docs

    def fetch_document(self, source_id: str) -> RawDocument:
        """Download file content from SharePoint."""
        import httpx

        if not self._access_token:
            self._authenticate()

        # Get item metadata
        meta = self._graph_request(f"/drives/{self._drive_id}/items/{source_id}")

        # Download content
        download_url = meta.get("@microsoft.graph.downloadUrl", "")
        if not download_url:
            download_url = f"https://graph.microsoft.com/v1.0/drives/{self._drive_id}/items/{source_id}/content"

        resp = httpx.get(
            download_url,
            headers={"Authorization": f"Bearer {self._access_token}"},
            timeout=120,
            follow_redirects=True,
        )
        resp.raise_for_status()

        modified_at = None
        if meta.get("lastModifiedDateTime"):
            modified_at = datetime.fromisoformat(
                meta["lastModifiedDateTime"].replace("Z", "+00:00")
            )

        return RawDocument(
            source_id=source_id,
            title=meta.get("name", ""),
            content=resp.content,
            content_type=meta.get("file", {}).get("mimeType", "application/octet-stream"),
            source_url=meta.get("webUrl", ""),
            author=meta.get("lastModifiedBy", {}).get("user", {}).get("displayName", ""),
            path=meta.get("parentReference", {}).get("path", ""),
            source_version=meta.get("eTag", ""),
            source_modified_at=modified_at,
        )
