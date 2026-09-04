"""Tests for the zip upload connector."""

import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from connectors.models import ConnectorConfig
from connectors.zipupload import ZipUploadConnector


def _make_zip(files: dict[str, bytes]) -> bytes:
    """Create an in-memory zip file from a dict of {filename: content}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Connector init
# ---------------------------------------------------------------------------


class TestZipUploadConnectorInit:
    def test_default_config(self):
        connector = ZipUploadConnector(config={})
        assert connector._upload_path == ""
        assert connector._original_filename == ""

    def test_config_values(self):
        connector = ZipUploadConnector(
            config={
                "upload_path": "uploads/abc123_docs.zip",
                "original_filename": "docs.zip",
            }
        )
        assert connector._upload_path == "uploads/abc123_docs.zip"
        assert connector._original_filename == "docs.zip"


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------


class TestTestConnection:
    def test_no_upload_path(self):
        connector = ZipUploadConnector(config={})
        assert connector.test_connection() is False

    def test_file_exists(self):
        connector = ZipUploadConnector(config={"upload_path": "uploads/test.zip"})
        mock_storage = MagicMock()
        mock_storage.exists.return_value = True
        with patch("connectors.zipupload._get_upload_storage", return_value=mock_storage):
            assert connector.test_connection() is True
        mock_storage.exists.assert_called_once_with("uploads/test.zip")

    def test_file_not_exists(self):
        connector = ZipUploadConnector(config={"upload_path": "uploads/missing.zip"})
        mock_storage = MagicMock()
        mock_storage.exists.return_value = False
        with patch("connectors.zipupload._get_upload_storage", return_value=mock_storage):
            assert connector.test_connection() is False

    def test_storage_error(self):
        connector = ZipUploadConnector(config={"upload_path": "uploads/test.zip"})
        mock_storage = MagicMock()
        mock_storage.exists.side_effect = Exception("storage error")
        with patch("connectors.zipupload._get_upload_storage", return_value=mock_storage):
            assert connector.test_connection() is False


# ---------------------------------------------------------------------------
# list_documents
# ---------------------------------------------------------------------------


class TestListDocuments:
    def _make_connector_with_zip(self, files: dict[str, bytes]):
        """Create a connector with a mocked storage containing the given zip."""
        zip_bytes = _make_zip(files)
        connector = ZipUploadConnector(config={"upload_path": "uploads/test.zip"})
        mock_storage = MagicMock()
        mock_file = MagicMock()
        mock_file.read.return_value = zip_bytes
        mock_storage.open.return_value = mock_file
        return connector, mock_storage

    def test_lists_supported_files(self):
        connector, mock_storage = self._make_connector_with_zip(
            {
                "readme.md": b"# Hello",
                "doc.pdf": b"fake-pdf",
                "data.csv": b"a,b,c",
                "image.png": b"fake-image",  # unsupported
            }
        )
        with patch("connectors.zipupload._get_upload_storage", return_value=mock_storage):
            docs = connector.list_documents()

        filenames = {d["source_id"] for d in docs}
        assert "readme.md" in filenames
        assert "doc.pdf" in filenames
        assert "data.csv" in filenames
        assert "image.png" not in filenames

    def test_skips_directories(self):
        zip_bytes = io.BytesIO()
        with zipfile.ZipFile(zip_bytes, "w") as zf:
            zf.writestr("folder/", "")
            zf.writestr("folder/doc.txt", "content")
        zip_bytes = zip_bytes.getvalue()

        connector = ZipUploadConnector(config={"upload_path": "test.zip"})
        mock_storage = MagicMock()
        mock_file = MagicMock()
        mock_file.read.return_value = zip_bytes
        mock_storage.open.return_value = mock_file

        with patch("connectors.zipupload._get_upload_storage", return_value=mock_storage):
            docs = connector.list_documents()

        assert len(docs) == 1
        assert docs[0]["source_id"] == "folder/doc.txt"
        assert docs[0]["path"] == "folder"

    def test_skips_macosx_resource_forks(self):
        connector, mock_storage = self._make_connector_with_zip(
            {
                "doc.txt": b"content",
                "__MACOSX/doc.txt": b"resource fork",
                "__MACOSX/._doc.txt": b"resource fork",
            }
        )
        with patch("connectors.zipupload._get_upload_storage", return_value=mock_storage):
            docs = connector.list_documents()

        assert len(docs) == 1
        assert docs[0]["source_id"] == "doc.txt"

    def test_empty_zip(self):
        connector, mock_storage = self._make_connector_with_zip({})
        with patch("connectors.zipupload._get_upload_storage", return_value=mock_storage):
            docs = connector.list_documents()
        assert docs == []

    def test_content_type_mapping(self):
        connector, mock_storage = self._make_connector_with_zip(
            {
                "doc.pdf": b"pdf",
                "page.html": b"html",
                "data.json": b"{}",
            }
        )
        with patch("connectors.zipupload._get_upload_storage", return_value=mock_storage):
            docs = connector.list_documents()

        by_id = {d["source_id"]: d for d in docs}
        assert by_id["doc.pdf"]["content_type"] == "application/pdf"
        assert by_id["page.html"]["content_type"] == "text/html"
        assert by_id["data.json"]["content_type"] == "application/json"

    def test_storage_error_returns_empty(self):
        connector = ZipUploadConnector(config={"upload_path": "bad.zip"})
        mock_storage = MagicMock()
        mock_storage.open.side_effect = FileNotFoundError("not found")
        with patch("connectors.zipupload._get_upload_storage", return_value=mock_storage):
            docs = connector.list_documents()
        assert docs == []


# ---------------------------------------------------------------------------
# fetch_document
# ---------------------------------------------------------------------------


class TestFetchDocument:
    def test_fetch_file_from_zip(self):
        zip_bytes = _make_zip({"docs/readme.md": b"# Hello World"})
        connector = ZipUploadConnector(config={"upload_path": "test.zip"})
        mock_storage = MagicMock()
        mock_file = MagicMock()
        mock_file.read.return_value = zip_bytes
        mock_storage.open.return_value = mock_file

        with patch("connectors.zipupload._get_upload_storage", return_value=mock_storage):
            doc = connector.fetch_document("docs/readme.md")

        assert doc.source_id == "docs/readme.md"
        assert doc.title == "readme.md"
        assert doc.content == b"# Hello World"
        assert doc.content_type == "text/markdown"
        assert doc.path == "docs"
        assert doc.source_version  # non-empty hash
        assert doc.source_modified_at is not None

    def test_fetch_file_at_root(self):
        zip_bytes = _make_zip({"report.pdf": b"fake-pdf"})
        connector = ZipUploadConnector(config={"upload_path": "test.zip"})
        mock_storage = MagicMock()
        mock_file = MagicMock()
        mock_file.read.return_value = zip_bytes
        mock_storage.open.return_value = mock_file

        with patch("connectors.zipupload._get_upload_storage", return_value=mock_storage):
            doc = connector.fetch_document("report.pdf")

        assert doc.path == ""
        assert doc.title == "report.pdf"

    def test_fetch_nonexistent_file_raises(self):
        zip_bytes = _make_zip({"doc.txt": b"content"})
        connector = ZipUploadConnector(config={"upload_path": "test.zip"})
        mock_storage = MagicMock()
        mock_file = MagicMock()
        mock_file.read.return_value = zip_bytes
        mock_storage.open.return_value = mock_file

        with patch("connectors.zipupload._get_upload_storage", return_value=mock_storage):
            with pytest.raises(KeyError):
                connector.fetch_document("nonexistent.txt")


# ---------------------------------------------------------------------------
# Connector registry
# ---------------------------------------------------------------------------


class TestConnectorRegistry:
    def test_zipupload_registered(self):
        from connectors.base import get_connector

        connector = get_connector(
            "zipupload",
            config={"upload_path": "test.zip"},
        )
        assert isinstance(connector, ZipUploadConnector)


# ---------------------------------------------------------------------------
# Model integration (Django DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestZipUploadConnectorModel:
    def test_create_zipupload_connector(self, tenant, project):
        connector = ConnectorConfig.objects.create(
            tenant=tenant,
            project=project,
            name="Document Archive",
            connector_type="zipupload",
            config={
                "upload_path": "uploads/abc123_docs.zip",
                "original_filename": "docs.zip",
            },
        )
        assert connector.connector_type == "zipupload"
        assert connector.config["upload_path"] == "uploads/abc123_docs.zip"
        assert connector.get_connector_type_display() == "Import ZIP"


# ---------------------------------------------------------------------------
# View integration: connector_create with file upload
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestZipUploadView:
    def _setup_request(self, rf, user, tenant, project):
        """Create a POST request factory with proper attributes."""
        from tenants.models import ProjectMembership, TenantMembership

        TenantMembership.objects.create(tenant=tenant, user=user, role=TenantMembership.Role.ADMIN)
        proj_membership = ProjectMembership.objects.create(
            project=project, user=user, role=TenantMembership.Role.ADMIN
        )
        return proj_membership

    def test_create_zipupload_connector_via_view(self, rf, user, tenant, project):
        from connectors.views import connector_create

        proj_membership = self._setup_request(rf, user, tenant, project)

        zip_content = _make_zip({"doc.txt": b"Hello"})
        zip_file = SimpleUploadedFile("docs.zip", zip_content, content_type="application/zip")

        request = rf.post(
            "/connectors/create/",
            data={
                "name": "My Zip",
                "connector_type": "zipupload",
                "zip_file": zip_file,
            },
        )
        request.user = user
        request.tenant = tenant
        request.project = project
        request.project_membership = proj_membership

        mock_storage = MagicMock()
        mock_storage.save.return_value = "uploads/test_docs.zip"

        with patch("connectors.views.storages", {"uploads": mock_storage}):
            response = connector_create(request)

        assert response.status_code == 302
        connector = ConnectorConfig.objects.get(name="My Zip")
        assert connector.connector_type == "zipupload"
        assert connector.config["upload_path"] == "uploads/test_docs.zip"
        assert connector.config["original_filename"] == "docs.zip"

    def test_create_zipupload_no_file_shows_error(self, rf, user, tenant, project):
        from connectors.views import connector_create

        proj_membership = self._setup_request(rf, user, tenant, project)

        request = rf.post(
            "/connectors/create/",
            data={
                "name": "My Zip",
                "connector_type": "zipupload",
            },
        )
        request.user = user
        request.tenant = tenant
        request.project = project
        request.project_membership = proj_membership

        response = connector_create(request)
        assert response.status_code == 200  # re-renders form

    def test_create_zipupload_invalid_zip_shows_error(self, rf, user, tenant, project):
        from connectors.views import connector_create

        proj_membership = self._setup_request(rf, user, tenant, project)

        bad_file = SimpleUploadedFile("bad.zip", b"not a zip", content_type="application/zip")

        request = rf.post(
            "/connectors/create/",
            data={
                "name": "Bad Zip",
                "connector_type": "zipupload",
                "zip_file": bad_file,
            },
        )
        request.user = user
        request.tenant = tenant
        request.project = project
        request.project_membership = proj_membership

        response = connector_create(request)
        assert response.status_code == 200  # re-renders form with error


# ---------------------------------------------------------------------------
# _guess_content_type
# ---------------------------------------------------------------------------


class TestGuessContentType:
    @pytest.mark.parametrize(
        "suffix,expected",
        [
            (".txt", "text/plain"),
            (".md", "text/markdown"),
            (".html", "text/html"),
            (".htm", "text/html"),
            (".pdf", "application/pdf"),
            (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            (".csv", "text/csv"),
            (".json", "application/json"),
            (".xml", "application/xml"),
            (".rst", "text/x-rst"),
            (".yaml", "text/yaml"),
            (".yml", "text/yaml"),
            (".unknown", "application/octet-stream"),
        ],
    )
    def test_content_type(self, suffix, expected):
        assert ZipUploadConnector._guess_content_type(suffix) == expected
