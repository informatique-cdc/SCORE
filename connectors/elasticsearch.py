"""
Elasticsearch connector.

Requires: pip install elasticsearch>=8.0  (or pip install score[elasticsearch])

Config keys:
  - hosts: Elasticsearch URL(s), e.g. "https://localhost:9200" (required)
  - cloud_id: Elastic Cloud ID (alternative to hosts)
  - index: Index name or pattern to read from (required)
  - auth_method: "api_key" | "basic_auth" | "bearer_token" (default: "api_key")
  - username: Username for basic_auth
  - verify_certs: Whether to verify TLS certificates (default: True)
  - ca_certs: Path to CA bundle for TLS verification
  - content_field: Document field containing the main text (default: "content")
  - title_field: Document field containing the title (default: "title")
  - author_field: Document field containing the author (default: "author")
  - date_field: Document field containing the modification date (default: "updated_at")
  - query: Optional Elasticsearch query DSL (JSON) to filter documents
  - batch_size: Number of documents per scroll/search_after page (default: 500)

Credential: API key, password, or bearer token (via get_secret / credential_ref).
"""

import json
import logging
from datetime import UTC, datetime

from .base import BaseConnector, RawDocument, register_connector

logger = logging.getLogger(__name__)

# Fields to request from Elasticsearch by default (if _source filtering is used)
_META_FIELDS = ("_id", "_version", "_seq_no", "_primary_term")


def _parse_datetime(value) -> datetime | None:
    """Try to parse a datetime from an Elasticsearch field value."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Epoch millis (Elasticsearch default for date fields)
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        # Last resort: fromisoformat
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


@register_connector("elasticsearch")
class ElasticsearchConnector(BaseConnector):
    """Connect to an Elasticsearch cluster and retrieve documents from an index."""

    def __init__(self, config: dict, credential: str = ""):
        super().__init__(config, credential)

        # Connection
        self._hosts = config.get("hosts", "")
        self._cloud_id = config.get("cloud_id", "")
        self._index = config.get("index", "")
        self._verify_certs = config.get("verify_certs", True)
        if isinstance(self._verify_certs, str):
            self._verify_certs = self._verify_certs.lower() not in ("false", "0", "no")
        self._ca_certs = config.get("ca_certs", "")

        # Authentication
        self._auth_method = config.get("auth_method", "api_key")
        self._username = config.get("username", "")

        # Field mapping
        self._content_field = config.get("content_field", "content")
        self._title_field = config.get("title_field", "title")
        self._author_field = config.get("author_field", "author")
        self._date_field = config.get("date_field", "updated_at")

        # Query filter
        query_raw = config.get("query", "")
        self._query = self._parse_query(query_raw)

        # Pagination
        self._batch_size = int(config.get("batch_size", 500))

        self._client = None

    @staticmethod
    def _parse_query(query_raw) -> dict:
        """Parse a query from config (could be dict or JSON string)."""
        if not query_raw:
            return {"match_all": {}}
        if isinstance(query_raw, dict):
            return query_raw
        try:
            return json.loads(query_raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid Elasticsearch query, falling back to match_all: %s", query_raw)
            return {"match_all": {}}

    def _get_client(self):
        """Lazily create and return the Elasticsearch client."""
        if self._client is not None:
            return self._client

        try:
            from elasticsearch import Elasticsearch
        except ImportError:
            raise ImportError("Install elasticsearch extras: pip install score[elasticsearch]")

        kwargs = {}

        # TLS
        kwargs["verify_certs"] = self._verify_certs
        if not self._verify_certs:
            kwargs["ssl_show_warn"] = False
        if self._ca_certs:
            kwargs["ca_certs"] = self._ca_certs

        # Authentication
        secret = self.credential
        if self._auth_method == "api_key":
            if secret:
                kwargs["api_key"] = secret
        elif self._auth_method == "basic_auth":
            if self._username and secret:
                kwargs["basic_auth"] = (self._username, secret)
        elif self._auth_method == "bearer_token":
            if secret:
                kwargs["bearer_auth"] = secret

        # Connection target
        if self._cloud_id:
            kwargs["cloud_id"] = self._cloud_id
            self._client = Elasticsearch(**kwargs)
        else:
            hosts = [h.strip() for h in self._hosts.split(",") if h.strip()]
            if not hosts:
                raise ValueError("Elasticsearch connector requires 'hosts' or 'cloud_id' in config")
            self._client = Elasticsearch(hosts, **kwargs)

        return self._client

    def test_connection(self) -> bool:
        """Verify connection by pinging the cluster and checking the index exists."""
        try:
            client = self._get_client()
            if not client.ping():
                logger.warning("Elasticsearch ping failed")
                return False
            if self._index and not client.indices.exists(index=self._index):
                logger.warning("Elasticsearch index '%s' does not exist", self._index)
                return False
            return True
        except Exception as e:
            logger.warning("Elasticsearch connection test failed: %s", e)
            return False

    def list_documents(self) -> list[dict]:
        """
        List all documents in the configured index using search_after + PIT
        for consistent, memory-efficient pagination.
        """
        client = self._get_client()

        if not self._index:
            raise ValueError("Elasticsearch connector requires an 'index' config key")

        # Determine which source fields to fetch for listing (lightweight)
        source_fields = [self._title_field, self._author_field, self._date_field]
        source_fields = [f for f in source_fields if f]

        docs = []
        pit = None

        try:
            # Open a Point in Time for consistent reads
            pit_resp = client.open_point_in_time(index=self._index, keep_alive="2m")
            pit_id = pit_resp["id"]
            pit = {"id": pit_id, "keep_alive": "2m"}

            search_after = None

            while True:
                body = {
                    "size": self._batch_size,
                    "query": self._query,
                    "sort": [{"_shard_doc": "asc"}],
                    "_source": source_fields,
                    "pit": pit,
                    "track_total_hits": False,
                }
                if search_after is not None:
                    body["search_after"] = search_after

                resp = client.search(body=body)
                hits = resp["hits"]["hits"]
                if not hits:
                    break

                # Update PIT id (may change between requests)
                pit["id"] = resp.get("pit_id", pit["id"])

                for hit in hits:
                    source = hit.get("_source", {})
                    version = str(hit.get("_version", hit.get("_seq_no", "")))

                    docs.append(
                        {
                            "source_id": hit["_id"],
                            "title": source.get(self._title_field, hit["_id"]),
                            "source_version": version,
                            "source_modified_at": source.get(self._date_field, ""),
                            "author": source.get(self._author_field, ""),
                            "content_type": "text/plain",
                        }
                    )

                search_after = hits[-1]["sort"]

        except Exception:
            # If PIT is not supported (e.g. older ES), fall back to scroll via helpers.scan
            logger.info("PIT not available, falling back to helpers.scan()")
            docs = self._list_documents_scan(client, source_fields)
        finally:
            if pit:
                try:
                    client.close_point_in_time(id=pit["id"])
                except Exception:
                    pass

        return docs

    def _list_documents_scan(self, client, source_fields: list[str]) -> list[dict]:
        """Fallback: list documents using helpers.scan() (scroll API)."""
        from elasticsearch.helpers import scan

        docs = []
        for hit in scan(
            client,
            index=self._index,
            query={"query": self._query, "_source": source_fields},
            scroll="2m",
            size=self._batch_size,
            preserve_order=False,
        ):
            source = hit.get("_source", {})
            version = str(hit.get("_version", hit.get("_seq_no", "")))
            docs.append(
                {
                    "source_id": hit["_id"],
                    "title": source.get(self._title_field, hit["_id"]),
                    "source_version": version,
                    "source_modified_at": source.get(self._date_field, ""),
                    "author": source.get(self._author_field, ""),
                    "content_type": "text/plain",
                }
            )
        return docs

    def fetch_document(self, source_id: str) -> RawDocument:
        """Fetch full document content by _id."""
        client = self._get_client()
        doc = client.get(index=self._index, id=source_id)

        source = doc.get("_source", {})
        version = str(doc.get("_version", doc.get("_seq_no", "")))

        # Extract content — try the configured field, fall back to concatenating all text fields
        content = source.get(self._content_field, "")
        if not content:
            content = self._extract_text_from_source(source)

        title = source.get(self._title_field, source_id)
        author = source.get(self._author_field, "")
        modified_at = _parse_datetime(source.get(self._date_field))

        # Determine content type
        content_type = "text/plain"
        if isinstance(content, str) and content.strip().startswith("<"):
            content_type = "text/html"

        # Build a stable source_url
        hosts = self._hosts or ""
        base_url = hosts.split(",")[0].strip().rstrip("/") if hosts else ""
        source_url = f"{base_url}/{self._index}/_doc/{source_id}" if base_url else ""

        return RawDocument(
            source_id=source_id,
            title=title,
            content=content,
            content_type=content_type,
            source_url=source_url,
            author=author,
            doc_type="elasticsearch_doc",
            source_version=version,
            source_modified_at=modified_at,
            metadata={
                "index": doc.get("_index", self._index),
                "seq_no": doc.get("_seq_no"),
                "primary_term": doc.get("_primary_term"),
            },
        )

    @staticmethod
    def _extract_text_from_source(source: dict) -> str:
        """
        Concatenate all string fields from the document source as a fallback
        when the configured content_field is empty or missing.
        """
        parts = []
        for key, value in source.items():
            if isinstance(value, str) and len(value) > 20:
                parts.append(value)
        return "\n\n".join(parts)
