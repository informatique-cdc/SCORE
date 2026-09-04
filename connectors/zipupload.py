"""
Zip upload connector.

Allows users to upload a .zip file containing documents.
The zip is stored via Django's pluggable storage backend (local filesystem
by default, Azure Blob / S3 in production) and its contents are ingested
like any other connector.

Config keys (stored in ConnectorConfig.config):
  - upload_path: path to the uploaded zip in the "uploads" storage backend
  - original_filename: original name of the uploaded zip file
"""

import hashlib
import logging
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import PurePosixPath

from django.core.files.storage import storages

from .base import BaseConnector, RawDocument, register_connector
from .generic import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)


def _get_upload_storage():
    """Return the configured 'uploads' storage backend."""
    return storages["uploads"]


@register_connector("zipupload")
class ZipUploadConnector(BaseConnector):
    """Connector that reads documents from an uploaded zip file."""

    def __init__(self, config: dict, credential: str = ""):
        super().__init__(config, credential)
        self._upload_path = config.get("upload_path", "")
        self._original_filename = config.get("original_filename", "")

    def test_connection(self) -> bool:
        """Check that the zip file exists in storage."""
        if not self._upload_path:
            return False
        try:
            storage = _get_upload_storage()
            return storage.exists(self._upload_path)
        except Exception:
            logger.exception("Failed to check zip file existence: %s", self._upload_path)
            return False

    def _open_zip(self) -> zipfile.ZipFile:
        """Open the zip file from storage and return a ZipFile object."""
        storage = _get_upload_storage()
        f = storage.open(self._upload_path, "rb")
        return zipfile.ZipFile(BytesIO(f.read()))

    def list_documents(self) -> list[dict]:
        """List all supported files inside the zip archive."""
        docs = []
        try:
            with self._open_zip() as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    path = PurePosixPath(info.filename)
                    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                        continue
                    # Skip macOS resource fork files
                    if any(part.startswith("__MACOSX") for part in path.parts):
                        continue

                    modified = datetime(*info.date_time, tzinfo=timezone.utc)
                    content_hash = hashlib.md5(
                        f"{info.filename}:{info.file_size}:{info.CRC}".encode()
                    ).hexdigest()

                    docs.append(
                        {
                            "source_id": info.filename,
                            "title": path.name,
                            "source_version": content_hash,
                            "source_url": "",
                            "source_modified_at": modified.isoformat(),
                            "content_type": self._guess_content_type(path.suffix),
                            "path": str(path.parent) if str(path.parent) != "." else "",
                        }
                    )
        except Exception:
            logger.exception("Failed to list documents from zip: %s", self._upload_path)
        return docs

    def fetch_document(self, source_id: str) -> RawDocument:
        """Extract a single file from the zip archive."""
        with self._open_zip() as zf:
            info = zf.getinfo(source_id)
            content = zf.read(source_id)
            path = PurePosixPath(source_id)
            modified = datetime(*info.date_time, tzinfo=timezone.utc)
            content_hash = hashlib.md5(
                f"{info.filename}:{info.file_size}:{info.CRC}".encode()
            ).hexdigest()

            return RawDocument(
                source_id=source_id,
                title=path.name,
                content=content,
                content_type=self._guess_content_type(path.suffix),
                path=str(path.parent) if str(path.parent) != "." else "",
                source_version=content_hash,
                source_modified_at=modified,
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
            ".rst": "text/x-rst",
            ".yaml": "text/yaml",
            ".yml": "text/yaml",
        }
        return mapping.get(suffix.lower(), "application/octet-stream")
