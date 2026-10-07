"""Tests for SharePointConnector.list_documents — Microsoft Graph paging."""

from unittest.mock import MagicMock, patch

from connectors.sharepoint import SharePointConnector

GRAPH = "https://graph.microsoft.com/v1.0"
NEXT_LINK = f"{GRAPH}/drives/drive-1/root:/Procedures:/children?$skiptoken=page2"


def _file(item_id, name):
    return {
        "id": item_id,
        "name": name,
        "eTag": f'"{{{item_id}}},1"',
        "file": {"mimeType": "application/pdf"},
    }


def _response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _connector():
    connector = SharePointConnector(
        config={"drive_id": "drive-1", "folder_path": "/Procedures"}, credential="secret"
    )
    connector._access_token = "token"  # skip MSAL
    return connector


class TestListDocumentsPaging:
    def test_follows_odata_next_link(self):
        pages = {
            f"{GRAPH}/drives/drive-1/root:/Procedures:/children": {
                "value": [_file("a", "Note-1.pdf"), _file("b", "Note-2.pdf")],
                "@odata.nextLink": NEXT_LINK,
            },
            NEXT_LINK: {"value": [_file("c", "Note-3.pdf")]},
        }

        with patch("httpx.get", side_effect=lambda url, **kw: _response(pages[url])) as get:
            docs = _connector().list_documents()

        assert [d["source_id"] for d in docs] == ["a", "b", "c"]
        assert get.call_count == 2
        assert get.call_args_list[1].args[0] == NEXT_LINK
        assert get.call_args_list[1].kwargs["headers"] == {"Authorization": "Bearer token"}

    def test_single_page(self):
        page = {"value": [_file("a", "Note-1.pdf"), {"id": "dir", "name": "Archives"}]}

        with patch("httpx.get", return_value=_response(page)) as get:
            docs = _connector().list_documents()

        assert [d["source_id"] for d in docs] == ["a"]
        assert get.call_count == 1
