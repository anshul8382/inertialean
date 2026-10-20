"""Client email archive helpers (no IMAP/Drive)."""
from datetime import date
from email.message import EmailMessage

import pytest

from services.client_email_archive_service import (
    known_message_ids,
    load_index,
    merge_imap_into_index,
    save_index,
    sync_window,
)
from services.gmail_sent_imap_service import (
    _imap_recipient_or_clause,
    message_matches_addresses,
)

pytestmark = pytest.mark.no_app


def test_message_matches_to_and_cc():
    msg = EmailMessage()
    msg["To"] = "other@example.com"
    msg["Cc"] = "Client Name <client@inertia.test>"
    assert message_matches_addresses(msg, ["client@inertia.test"])
    assert not message_matches_addresses(msg, ["nobody@example.com"])


def test_imap_recipient_or_clause():
    clause = _imap_recipient_or_clause(["a@x.com", "b@y.com"])
    assert 'TO "a@x.com"' in clause
    assert 'CC "b@y.com"' in clause
    assert clause.startswith("(OR ")


def test_sync_window_incremental_overlap():
    index = {"last_sent_at": "2026-09-10"}
    since, before = sync_window(index, lookback_days=365, today=date(2026, 10, 19))
    assert since == date(2026, 9, 9)
    assert before == date(2026, 10, 20)


def test_sync_window_first_run_lookback():
    since, before = sync_window({}, lookback_days=30, today=date(2026, 10, 19))
    assert since == date(2026, 9, 19)
    assert before == date(2026, 10, 20)


def test_merge_imap_skips_known(tmp_path, monkeypatch):
    import services.client_email_archive_service as arch

    monkeypatch.setattr(arch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(arch, "_ensure_drive_emails_folder", lambda *a, **k: None)

    cid = 42
    index = load_index(cid)
    rows = [
        {
            "gmail_message_id": "<abc@mail>",
            "subject": "Hello",
            "sent_at": "2026-09-01",
            "to_email": "c@x.com",
            "to_raw": "c@x.com",
            "html": "<p>hi</p>",
            "text": "hi",
            "raw_rfc822": b"From: x\r\n\r\nhi",
            "recipient_emails": ["c@x.com"],
        }
    ]
    assert merge_imap_into_index(cid, index, rows, upload_drive=False) == 1
    save_index(cid, index)
    index2 = load_index(cid)
    assert "<abc@mail>" in known_message_ids(index2)
    assert merge_imap_into_index(cid, index2, rows, upload_drive=False) == 0


def test_looks_like_email_file():
    from services.client_email_archive_service import _looks_like_email_file

    assert _looks_like_email_file("2026-06-16_elog_477.html")
    assert _looks_like_email_file("msg.eml")
    assert _looks_like_email_file("x", "text/html")
    assert not _looks_like_email_file("subhodeep Oct 2026 holding data .xlsx")
    assert not _looks_like_email_file("statement.csv")


def test_pull_emails_from_drive_tree_imports_missing(tmp_path, monkeypatch):
    import services.client_email_archive_service as arch

    monkeypatch.setattr(arch, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        arch,
        "_drive_link_status",
        lambda cid: {
            "drive_linked": True,
            "drive_folder_id": "root99",
            "drive_folder_url": "https://drive.google.com/drive/folders/root99",
            "drive_columns_ready": True,
            "needs_drive_details": False,
            "drive_flag": None,
        },
    )
    monkeypatch.setattr(
        "services.client_google_drive_service.walk_folder_tree",
        lambda root, **k: {
            "ok": True,
            "files": [
                {
                    "id": "f1",
                    "name": "2026-06-16_elog_477.html",
                    "mimeType": "text/html",
                    "path": "Emails/2026-06-16_elog_477.html",
                    "folder_path": "Emails",
                    "parent_id": "emails",
                },
                {
                    "id": "f2",
                    "name": "notes.txt",
                    "mimeType": "text/plain",
                    "path": "Misc/notes.txt",
                    "folder_path": "Misc",
                    "parent_id": "misc",
                },
                {
                    "id": "f3",
                    "name": "holdings.xlsx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "path": "Holdings/holdings.xlsx",
                    "folder_path": "Holdings",
                    "parent_id": "hold",
                },
            ],
            "folders": [{"id": "emails", "name": "Emails", "path": "Emails"}],
            "nodes_visited": 3,
        },
    )
    monkeypatch.setattr(
        "services.client_google_drive_service.download_file_bytes",
        lambda fid, **k: {
            "ok": True,
            "content": b"<html>SELL INFY 10</html>",
        },
    )

    out = arch.pull_emails_from_drive_tree(99)
    assert out["ok"] is True
    assert out["imported"] == 1  # html only; notes.txt + xlsx skipped
    assert out["scanned"] == 1
    idx = arch.load_index(99)
    mids = {m["message_id"] for m in idx["messages"]}
    assert "drive:f1" in mids
    assert "drive:f2" not in mids
    # Second pull is idempotent
    out2 = arch.pull_emails_from_drive_tree(99)
    assert out2["imported"] == 0


def test_drive_flag_when_not_linked(monkeypatch):
    import services.client_email_archive_service as arch

    monkeypatch.setattr(
        arch,
        "_drive_link_status",
        lambda cid: {
            "drive_linked": False,
            "drive_folder_id": None,
            "drive_folder_url": "",
            "drive_columns_ready": True,
            "needs_drive_details": True,
            "drive_flag": "Client Google Drive folder is not linked.",
        },
    )
    monkeypatch.setattr(arch, "client_email_addresses", lambda cid: ["a@b.com"])
    monkeypatch.setattr(arch, "load_index", lambda cid: arch._empty_index(cid))
    monkeypatch.setattr(arch, "save_index", lambda cid, idx: None)
    monkeypatch.setattr(
        "services.gmail_sent_imap_service.fetch_sent_to_addresses",
        lambda **k: [],
    )
    monkeypatch.setattr(arch, "merge_emaillog_into_index", lambda *a, **k: 0)

    out = arch.sync_client_emails(7, upload_drive=True)
    assert out["needs_drive_details"] is True
    assert out["drive_flag"]
    assert out["stored_locally"] is True
    assert out["stored_on_drive"] is False
