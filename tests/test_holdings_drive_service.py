"""Holdings Drive helpers (unit, no live Drive)."""
import pytest

from services.reconciliation import holdings_drive_service as hds

pytestmark = pytest.mark.no_app


def test_upload_statement_if_missing_skips_without_drive(monkeypatch):
    monkeypatch.setattr(hds, "_client_drive_root", lambda cid: None)
    out = hds.upload_statement_if_missing(1, filename="a.csv", data=b"x,y\n1,2\n")
    assert out.get("skipped") is True
    assert out.get("needs_drive") is True


def test_upload_skips_when_name_exists(monkeypatch):
    monkeypatch.setattr(hds, "_client_drive_root", lambda cid: "root123")
    monkeypatch.setattr(
        hds,
        "ensure_holdings_folder",
        lambda cid: {"ok": True, "folder_id": "hold123", "root_folder_id": "root123"},
    )
    monkeypatch.setattr(hds, "_discover_holdings_folders", lambda root: [])

    def fake_find(folder_id, name, **kw):
        return {"id": "existing", "name": name, "webViewLink": "https://drive/x"}

    monkeypatch.setattr(
        "services.client_google_drive_service.find_file_by_name", fake_find
    )
    out = hds.upload_statement_if_missing(1, filename="stmt.csv", data=b"abc")
    assert out["ok"] is True
    assert out["already_present"] is True
    assert out["file_id"] == "existing"


def test_list_finds_holding_statement_when_holdings_empty(monkeypatch):
    monkeypatch.setattr(hds, "_client_drive_root", lambda cid: "root123")
    monkeypatch.setattr(
        hds,
        "_discover_holdings_folders",
        lambda root: [
            {"id": "empty_holdings", "name": "Holdings", "path": "Holdings"},
            {
                "id": "stmt",
                "name": "Holding Statement",
                "path": "Holding Statement",
            },
            {
                "id": "nested",
                "name": "Holdings",
                "path": "Holding Statement/Holdings",
            },
        ],
    )

    def fake_list(folder_id, **kw):
        if folder_id == "empty_holdings":
            return {"ok": True, "files": []}
        if folder_id == "stmt":
            return {
                "ok": True,
                "files": [
                    {
                        "id": "a",
                        "name": "subhodeep Oct 2026 holding data .xlsx",
                        "modifiedTime": "2026-10-01",
                    },
                    {"id": "skip", "name": "notes.txt", "modifiedTime": "2026-10-02"},
                ],
            }
        if folder_id == "nested":
            return {
                "ok": True,
                "files": [
                    {
                        "id": "b",
                        "name": "holdings-TV1822.xlsx",
                        "modifiedTime": "2026-10-03",
                    }
                ],
            }
        return {"ok": True, "files": []}

    monkeypatch.setattr(
        "services.client_google_drive_service.list_folder_files", fake_list
    )
    out = hds.list_client_holdings_files(1)
    assert out["ok"] is True
    names = {f["name"] for f in out["files"]}
    assert "subhodeep Oct 2026 holding data .xlsx" in names
    assert "holdings-TV1822.xlsx" in names
    assert "notes.txt" not in names
    assert out["count"] == 2


def test_is_holdings_folder_name_aliases():
    assert hds._is_holdings_folder_name("Holding Statement")
    assert hds._is_holdings_folder_name("Holdings")
    assert not hds._is_holdings_folder_name("Trade details")
    assert not hds._is_holdings_folder_name("Emails")
