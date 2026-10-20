"""Drive walk_folder_tree (unit, mocked Drive API)."""
import pytest

pytestmark = pytest.mark.no_app


def test_walk_folder_tree_visits_nested_nodes(monkeypatch):
    import services.client_google_drive_service as drive

    monkeypatch.setattr(drive, "normalize_folder_id", lambda x: x)

    children = {
        "root": [
            {
                "id": "emails",
                "name": "Emails",
                "mimeType": "application/vnd.google-apps.folder",
            },
            {
                "id": "hold",
                "name": "Holdings",
                "mimeType": "application/vnd.google-apps.folder",
            },
            {
                "id": "doc1",
                "name": "Suitability.doc",
                "mimeType": "application/vnd.google-apps.document",
            },
        ],
        "emails": [
            {
                "id": "e1",
                "name": "2026-06-16_elog_477.html",
                "mimeType": "text/html",
            }
        ],
        "hold": [
            {
                "id": "h1",
                "name": "stmt.xlsx",
                "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
        ],
    }

    def fake_children(fid, **k):
        return {"ok": True, "files": children.get(fid, []), "folder_id": fid}

    monkeypatch.setattr(drive, "list_folder_children", fake_children)

    out = drive.walk_folder_tree("root", max_depth=3, max_nodes=20)
    assert out["ok"] is True
    paths = {f["path"] for f in out["files"]}
    assert "Emails/2026-06-16_elog_477.html" in paths
    assert "Holdings/stmt.xlsx" in paths
    assert "Suitability.doc" in paths
    folder_names = {f["name"] for f in out["folders"]}
    assert folder_names == {"Emails", "Holdings"}
    assert out["nodes_visited"] >= 3
