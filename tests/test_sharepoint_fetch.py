"""Tests for SharePointConnector.fetch_document — site-based configuration."""

from unittest.mock import MagicMock, patch

from connectors.sharepoint import SharePointConnector

GRAPH = "https://graph.microsoft.com/v1.0"
# Graph site id: "{hostname},{site-collection-id},{web-id}"
SITE = "contoso.sharepoint.com,2c712604-1370-44e7-a1f5-426573fda80a,2d2244c3-251a-49ea-93a8-39e1c3a060fe"


def _response(payload=None, content=b""):
    resp = MagicMock()
    resp.json.return_value = payload or {}
    resp.content = content
    resp.raise_for_status.return_value = None
    return resp


def _connector(config):
    connector = SharePointConnector(config=config, credential="secret")
    connector._access_token = "token"  # skip MSAL
    return connector


ITEM = {
    "id": "item-1",
    "name": "Procédure achats.pdf",
    "eTag": '"{item-1},3"',
    "file": {"mimeType": "application/pdf"},
    "@microsoft.graph.downloadUrl": "https://contoso.sharepoint.com/download?token=abc",
}


class TestFetchDocument:
    def test_site_only_config_fetches_from_the_site_drive(self):
        """drive_id is optional: list_documents() then uses the site's default drive."""
        responses = {
            f"{GRAPH}/sites/{SITE}/drive/items/item-1": _response(ITEM),
            ITEM["@microsoft.graph.downloadUrl"]: _response(content=b"%PDF-1.7"),
        }

        with patch("httpx.get", side_effect=lambda url, **kw: responses[url]) as get:
            raw = _connector({"site_url": SITE}).fetch_document("item-1")

        assert get.call_args_list[0].args[0] == f"{GRAPH}/sites/{SITE}/drive/items/item-1"
        assert raw.title == "Procédure achats.pdf"
        assert raw.content == b"%PDF-1.7"

    def test_drive_id_config_unchanged(self):
        responses = {
            f"{GRAPH}/drives/drive-1/items/item-1": _response(ITEM),
            ITEM["@microsoft.graph.downloadUrl"]: _response(content=b"%PDF-1.7"),
        }

        with patch("httpx.get", side_effect=lambda url, **kw: responses[url]):
            raw = _connector({"site_url": SITE, "drive_id": "drive-1"}).fetch_document("item-1")

        assert raw.content == b"%PDF-1.7"
