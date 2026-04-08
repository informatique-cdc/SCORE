"""Tests for the Elasticsearch connector."""

import json
import sys
import types
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from connectors.elasticsearch import ElasticsearchConnector, _parse_datetime


@pytest.fixture()
def mock_es_module():
    """
    Inject a fake ``elasticsearch`` package into sys.modules so that the lazy
    ``from elasticsearch import Elasticsearch`` inside _get_client() picks up
    our mock instead of requiring the real package to be installed.
    """
    mock_es_class = MagicMock()

    # Create a fake top-level module
    fake_es = types.ModuleType("elasticsearch")
    fake_es.Elasticsearch = mock_es_class

    # Create a fake helpers sub-module
    fake_helpers = types.ModuleType("elasticsearch.helpers")
    fake_helpers.scan = MagicMock()
    fake_es.helpers = fake_helpers

    originals = {
        "elasticsearch": sys.modules.get("elasticsearch"),
        "elasticsearch.helpers": sys.modules.get("elasticsearch.helpers"),
    }
    sys.modules["elasticsearch"] = fake_es
    sys.modules["elasticsearch.helpers"] = fake_helpers

    yield mock_es_class, fake_helpers.scan

    # Restore original state
    for key, val in originals.items():
        if val is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = val


# ---------------------------------------------------------------------------
# _parse_datetime utility
# ---------------------------------------------------------------------------


class TestParseDatetime:
    def test_iso_format(self):
        dt = _parse_datetime("2024-06-15T10:30:00Z")
        assert isinstance(dt, datetime)
        assert dt.year == 2024
        assert dt.month == 6

    def test_iso_format_with_timezone(self):
        dt = _parse_datetime("2024-06-15T10:30:00+02:00")
        assert isinstance(dt, datetime)

    def test_epoch_millis(self):
        dt = _parse_datetime(1718448600000)
        assert isinstance(dt, datetime)

    def test_date_only(self):
        dt = _parse_datetime("2024-06-15")
        assert isinstance(dt, datetime)
        assert dt.day == 15

    def test_none(self):
        assert _parse_datetime(None) is None

    def test_empty_string(self):
        assert _parse_datetime("") is None

    def test_garbage_string(self):
        assert _parse_datetime("not-a-date") is None


# ---------------------------------------------------------------------------
# Connector init & config parsing
# ---------------------------------------------------------------------------


class TestElasticsearchConnectorInit:
    def test_default_config(self):
        connector = ElasticsearchConnector(
            config={"hosts": "https://localhost:9200", "index": "my-index"},
            credential="my-api-key",
        )
        assert connector._hosts == "https://localhost:9200"
        assert connector._index == "my-index"
        assert connector._auth_method == "api_key"
        assert connector._content_field == "content"
        assert connector._title_field == "title"
        assert connector._verify_certs is True
        assert connector._batch_size == 500

    def test_custom_field_mapping(self):
        connector = ElasticsearchConnector(
            config={
                "hosts": "http://es:9200",
                "index": "articles",
                "content_field": "body",
                "title_field": "headline",
                "author_field": "writer",
                "date_field": "published_at",
                "batch_size": "100",
            },
        )
        assert connector._content_field == "body"
        assert connector._title_field == "headline"
        assert connector._author_field == "writer"
        assert connector._date_field == "published_at"
        assert connector._batch_size == 100

    def test_verify_certs_string_false(self):
        connector = ElasticsearchConnector(
            config={"hosts": "http://es:9200", "index": "x", "verify_certs": "false"},
        )
        assert connector._verify_certs is False

    def test_verify_certs_string_true(self):
        connector = ElasticsearchConnector(
            config={"hosts": "http://es:9200", "index": "x", "verify_certs": "true"},
        )
        assert connector._verify_certs is True

    def test_query_from_json_string(self):
        q = json.dumps({"term": {"status": "published"}})
        connector = ElasticsearchConnector(
            config={"hosts": "http://es:9200", "index": "x", "query": q},
        )
        assert connector._query == {"term": {"status": "published"}}

    def test_query_from_dict(self):
        connector = ElasticsearchConnector(
            config={
                "hosts": "http://es:9200",
                "index": "x",
                "query": {"match": {"status": "active"}},
            },
        )
        assert connector._query == {"match": {"status": "active"}}

    def test_invalid_query_falls_back(self):
        connector = ElasticsearchConnector(
            config={"hosts": "http://es:9200", "index": "x", "query": "invalid{json"},
        )
        assert connector._query == {"match_all": {}}

    def test_empty_query_defaults_to_match_all(self):
        connector = ElasticsearchConnector(
            config={"hosts": "http://es:9200", "index": "x"},
        )
        assert connector._query == {"match_all": {}}


# ---------------------------------------------------------------------------
# Client creation
# ---------------------------------------------------------------------------


class TestGetClient:
    def test_api_key_auth(self, mock_es_module):
        mock_es_class, _ = mock_es_module
        connector = ElasticsearchConnector(
            config={"hosts": "https://es:9200", "index": "x", "auth_method": "api_key"},
            credential="my-secret-api-key",
        )
        connector._get_client()
        mock_es_class.assert_called_once()
        call_kwargs = mock_es_class.call_args[1]
        assert call_kwargs["api_key"] == "my-secret-api-key"

    def test_basic_auth(self, mock_es_module):
        mock_es_class, _ = mock_es_module
        connector = ElasticsearchConnector(
            config={
                "hosts": "https://es:9200",
                "index": "x",
                "auth_method": "basic_auth",
                "username": "elastic",
            },
            credential="password123",
        )
        connector._get_client()
        call_kwargs = mock_es_class.call_args[1]
        assert call_kwargs["basic_auth"] == ("elastic", "password123")

    def test_bearer_token(self, mock_es_module):
        mock_es_class, _ = mock_es_module
        connector = ElasticsearchConnector(
            config={"hosts": "https://es:9200", "index": "x", "auth_method": "bearer_token"},
            credential="token-value",
        )
        connector._get_client()
        call_kwargs = mock_es_class.call_args[1]
        assert call_kwargs["bearer_auth"] == "token-value"

    def test_cloud_id(self, mock_es_module):
        mock_es_class, _ = mock_es_module
        connector = ElasticsearchConnector(
            config={"cloud_id": "my-deployment:abc123", "index": "x"},
            credential="api-key",
        )
        connector._get_client()
        call_kwargs = mock_es_class.call_args[1]
        assert call_kwargs["cloud_id"] == "my-deployment:abc123"
        # Should NOT be called with hosts as positional arg
        assert mock_es_class.call_args[0] == ()

    def test_verify_certs_false_disables_warnings(self, mock_es_module):
        mock_es_class, _ = mock_es_module
        connector = ElasticsearchConnector(
            config={"hosts": "https://es:9200", "index": "x", "verify_certs": False},
        )
        connector._get_client()
        call_kwargs = mock_es_class.call_args[1]
        assert call_kwargs["verify_certs"] is False
        assert call_kwargs["ssl_show_warn"] is False

    def test_no_hosts_or_cloud_id_raises(self, mock_es_module):
        connector = ElasticsearchConnector(config={"index": "x"})
        with pytest.raises(ValueError, match="requires 'hosts' or 'cloud_id'"):
            connector._get_client()

    def test_multiple_hosts(self, mock_es_module):
        mock_es_class, _ = mock_es_module
        connector = ElasticsearchConnector(
            config={"hosts": "https://es1:9200, https://es2:9200", "index": "x"},
        )
        connector._get_client()
        hosts_arg = mock_es_class.call_args[0][0]
        assert hosts_arg == ["https://es1:9200", "https://es2:9200"]


# ---------------------------------------------------------------------------
# test_connection (inject mock client directly)
# ---------------------------------------------------------------------------


class TestTestConnection:
    def _make_connector(self, mock_client):
        connector = ElasticsearchConnector(
            config={"hosts": "https://es:9200", "index": "docs"},
        )
        connector._client = mock_client
        return connector

    def test_success(self):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.indices.exists.return_value = True
        assert self._make_connector(mock_client).test_connection() is True

    def test_ping_fails(self):
        mock_client = MagicMock()
        mock_client.ping.return_value = False
        assert self._make_connector(mock_client).test_connection() is False

    def test_index_not_found(self):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.indices.exists.return_value = False
        assert self._make_connector(mock_client).test_connection() is False

    def test_exception(self):
        mock_client = MagicMock()
        mock_client.ping.side_effect = Exception("connection refused")
        assert self._make_connector(mock_client).test_connection() is False


# ---------------------------------------------------------------------------
# fetch_document (inject mock client directly)
# ---------------------------------------------------------------------------


class TestFetchDocument:
    def _make_connector(self, mock_client, **config_overrides):
        config = {"hosts": "https://es:9200", "index": "articles"}
        config.update(config_overrides)
        connector = ElasticsearchConnector(config=config)
        connector._client = mock_client
        return connector

    def test_basic_fetch(self):
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "_id": "doc-1",
            "_index": "articles",
            "_version": 3,
            "_seq_no": 42,
            "_primary_term": 1,
            "_source": {
                "title": "My Article",
                "content": "This is the body of the article.",
                "author": "Alice",
                "updated_at": "2024-06-15T10:00:00Z",
            },
        }
        connector = self._make_connector(mock_client)
        doc = connector.fetch_document("doc-1")

        assert doc.source_id == "doc-1"
        assert doc.title == "My Article"
        assert doc.content == "This is the body of the article."
        assert doc.author == "Alice"
        assert doc.content_type == "text/plain"
        assert doc.source_version == "3"
        assert doc.doc_type == "elasticsearch_doc"
        assert doc.source_modified_at is not None
        assert doc.metadata["index"] == "articles"

    def test_html_content_detection(self):
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "_id": "doc-2",
            "_version": 1,
            "_source": {
                "title": "HTML Doc",
                "content": "<h1>Hello World</h1><p>Some content</p>",
            },
        }
        doc = self._make_connector(mock_client).fetch_document("doc-2")
        assert doc.content_type == "text/html"

    def test_fallback_content_extraction(self):
        """When the content field is missing, concatenate long string fields."""
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "_id": "doc-3",
            "_version": 1,
            "_source": {
                "title": "Fallback Doc",
                "description": "A short description.",
                "body_text": "This is a long body text that should be picked up as fallback content for the document.",
            },
        }
        doc = self._make_connector(mock_client).fetch_document("doc-3")
        assert "long body text" in doc.content

    def test_custom_field_mapping(self):
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "_id": "doc-4",
            "_version": 1,
            "_source": {
                "headline": "Custom Title",
                "body": "Custom body content here.",
                "writer": "Bob",
            },
        }
        connector = self._make_connector(
            mock_client,
            content_field="body",
            title_field="headline",
            author_field="writer",
        )
        doc = connector.fetch_document("doc-4")
        assert doc.title == "Custom Title"
        assert doc.content == "Custom body content here."
        assert doc.author == "Bob"


# ---------------------------------------------------------------------------
# list_documents (inject mock client directly)
# ---------------------------------------------------------------------------


class TestListDocuments:
    def _make_connector(self, mock_client):
        connector = ElasticsearchConnector(
            config={"hosts": "https://es:9200", "index": "docs"},
        )
        connector._client = mock_client
        return connector

    def test_list_with_pit(self):
        mock_client = MagicMock()
        mock_client.open_point_in_time.return_value = {"id": "pit-123"}
        mock_client.search.side_effect = [
            {
                "pit_id": "pit-123",
                "hits": {
                    "hits": [
                        {
                            "_id": "doc-1",
                            "_version": 1,
                            "_source": {"title": "Doc 1", "author": "A", "updated_at": ""},
                            "sort": [1],
                        },
                        {
                            "_id": "doc-2",
                            "_version": 2,
                            "_source": {"title": "Doc 2", "author": "B", "updated_at": ""},
                            "sort": [2],
                        },
                    ]
                },
            },
            {"pit_id": "pit-123", "hits": {"hits": []}},
        ]
        docs = self._make_connector(mock_client).list_documents()

        assert len(docs) == 2
        assert docs[0]["source_id"] == "doc-1"
        assert docs[0]["title"] == "Doc 1"
        assert docs[1]["source_id"] == "doc-2"
        mock_client.close_point_in_time.assert_called_once()

    def test_list_empty_index(self):
        mock_client = MagicMock()
        mock_client.open_point_in_time.return_value = {"id": "pit-123"}
        mock_client.search.return_value = {"pit_id": "pit-123", "hits": {"hits": []}}

        docs = self._make_connector(mock_client).list_documents()
        assert docs == []

    def test_list_requires_index(self):
        connector = ElasticsearchConnector(config={"hosts": "https://es:9200"})
        connector._client = MagicMock()
        with pytest.raises(ValueError, match="requires an 'index'"):
            connector.list_documents()

    def test_fallback_to_scan(self, mock_es_module):
        """When PIT fails, should fall back to helpers.scan()."""
        _, mock_scan = mock_es_module
        mock_scan.return_value = iter(
            [
                {
                    "_id": "doc-1",
                    "_version": 1,
                    "_source": {"title": "Fallback Doc"},
                }
            ]
        )

        mock_client = MagicMock()
        mock_client.open_point_in_time.side_effect = Exception("PIT not supported")

        docs = self._make_connector(mock_client).list_documents()
        assert len(docs) == 1
        assert docs[0]["source_id"] == "doc-1"
        mock_scan.assert_called_once()


# ---------------------------------------------------------------------------
# Model integration (Django DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestElasticsearchConnectorModel:
    def test_create_elasticsearch_connector(self, tenant, project):
        from connectors.models import ConnectorConfig

        connector = ConnectorConfig.objects.create(
            tenant=tenant,
            project=project,
            name="ES Production",
            connector_type="elasticsearch",
            config={
                "hosts": "https://es-prod:9200",
                "index": "documents",
                "auth_method": "api_key",
                "content_field": "body",
            },
            credential_ref="ES_API_KEY",
        )
        assert connector.connector_type == "elasticsearch"
        assert connector.config["index"] == "documents"
        assert connector.get_connector_type_display() == "Elasticsearch"
