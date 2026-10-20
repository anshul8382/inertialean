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
        lambda cid: {"ok": True, "folder_id": "hold123"},
    )

    def fake_find(folder_id, name, **kw):
        return {"id": "existing", "name": name, "webViewLink": "https://drive/x"}

    monkeypatch.setattr(
        "services.client_google_drive_service.find_file_by_name", fake_find
    )
    out = hds.upload_statement_if_missing(1, filename="stmt.csv", data=b"abc")
    assert out["ok"] is True
    assert out["already_present"] is True
    assert out["file_id"] == "existing"


def test_list_filters_extensions(monkeypatch):
    monkeypatch.setattr(
        hds,
        "ensure_holdings_folder",
        lambda cid: {"ok": True, "folder_id": "hold123"},
    )
    monkeypatch.setattr(
        "services.client_google_drive_service.list_folder_files",
        lambda folder_id, **kw: {
            "ok": True,
            "files": [
                {"id": "1", "name": "a.xlsx", "modifiedTime": "2026-01-01"},
                {"id": "2", "name": "notes.txt", "modifiedTime": "2026-01-02"},
                {"id": "3", "name": "b.pdf", "modifiedTime": "2026-01-03"},
            ],
        },
    )
    out = hds.list_client_holdings_files(1)
    assert out["ok"] is True
    names = {f["name"] for f in out["files"]}
    assert names == {"a.xlsx", "b.pdf"}
