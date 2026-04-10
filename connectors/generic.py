"""
Generic file/HTTP connector.

Supports:
  - Local filesystem directory
  - HTTP/HTTPS URLs (single file or index page)

Config keys:
  - source_type: "filesystem" or "http"
  - base_path: directory path (filesystem) or base URL (http)
  - file_patterns: list of glob patterns to include (default: ["*"])
  - recursive: whether to recurse into subdirectories (default: True)
"""

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .base import BaseConnector, RawDocument, register_connector

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".txt",
    ".md",
    ".html",
    ".htm",
    ".pdf",
    ".docx",
    ".pptx",
    ".csv",
    ".json",
    ".xml",
    ".rst",
    ".yaml",
    ".yml",
}


@register_connector("generic")
class GenericConnector(BaseConnector):
    """Generic file system and HTTP connector."""

    def __init__(self, config: dict, credential: str = ""):
        super().__init__(config, credential)
        self._source_type = config.get("source_type", "filesystem")
        self._base_path = config.get("base_path", "")
        self._file_patterns = config.get("file_patterns", ["*"])
        self._recursive = config.get("recursive", True)

    def test_connection(self) -> bool:
        if self._source_type == "filesystem":
            return Path(self._base_path).is_dir()
        elif self._source_type == "http":
            try:
                resp = httpx.head(self._base_path, timeout=10, follow_redirects=True)
                return resp.status_code < 400
            except (httpx.HTTPError, OSError):
                return False
        return False

    def list_documents(self) -> list[dict]:
        if self._source_type == "filesystem":
            return self._list_filesystem()
        elif self._source_type == "http":
            return self._list_http()
        return []

    def _list_filesystem(self) -> list[dict]:
        """List files from local filesystem."""
        base = Path(self._base_path)
        docs = []

        if self._recursive:
            files = base.rglob("*")
        else:
            files = base.glob("*")

        for fp in files:
            if not fp.is_file():
                continue
            if fp.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue

            stat = fp.stat()
            rel_path = fp.relative_to(base)
            modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

            docs.append(
                {
                    "source_id": str(rel_path),
                    "title": fp.name,
                    "source_version": f"{stat.st_mtime:.0f}-{stat.st_size}",
                    "source_url": str(fp),
                    "source_modified_at": modified.isoformat(),
                    "content_type": self._guess_content_type(fp.suffix),
                    "path": str(rel_path.parent),
                }
            )

        return docs

    def _list_http(self) -> list[dict]:
        """List documents from HTTP source. Returns single doc for direct URLs."""
        # For a direct file URL, return it as a single document
        docs = [
            {
                "source_id": self._base_path,
                "title": self._base_path.split("/")[-1] or "document",
                "source_version": "",
                "source_url": self._base_path,
                "content_type": "",
            }
        ]
        return docs

    def fetch_document(self, source_id: str) -> RawDocument:
        if self._source_type == "filesystem":
            return self._fetch_filesystem(source_id)
        elif self._source_type == "http":
            return self._fetch_http(source_id)
        raise ValueError(f"Unknown source_type: {self._source_type}")

    def _fetch_filesystem(self, source_id: str) -> RawDocument:
        """Read a file from the local filesystem."""
        fp = Path(self._base_path) / source_id
        stat = fp.stat()
        content = fp.read_bytes()
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

        return RawDocument(
            source_id=source_id,
            title=fp.name,
            content=content,
            content_type=self._guess_content_type(fp.suffix),
            source_url=str(fp),
            path=str(Path(source_id).parent),
            source_version=f"{stat.st_mtime:.0f}-{stat.st_size}",
            source_modified_at=modified,
        )

    def _fetch_http(self, source_id: str) -> RawDocument:
        """Fetch a document via HTTP."""
        resp = httpx.get(source_id, timeout=60, follow_redirects=True)
        resp.raise_for_status()

        if not resp.content:
            raise ValueError(
                f"Empty response from {source_id} (HTTP {resp.status_code})"
            )

        content_type = resp.headers.get("content-type", "").split(";")[0].strip()
        etag = resp.headers.get("etag", "")
        version = etag or hashlib.sha256(resp.content).hexdigest()[:16]

        return RawDocument(
            source_id=source_id,
            title=source_id.split("/")[-1] or "document",
            content=resp.content if "text" not in content_type else resp.text,
            content_type=content_type,
            source_url=source_id,
            source_version=version,
        )

    @staticmethod
    def _guess_content_type(suffix: str) -> str:
        mapping = {
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".html": "text/html",
            ".htm": "text/html",
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".csv": "text/csv",
            ".json": "application/json",
            ".xml": "application/xml",
        }
        return mapping.get(suffix.lower(), "application/octet-stream")
