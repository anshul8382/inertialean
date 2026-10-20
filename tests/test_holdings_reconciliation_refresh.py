"""Refresh investigation uses uploaded holdings; emails skip when fresh."""
from datetime import date

import pytest

from services.reconciliation import run_service as rs

pytestmark = pytest.mark.no_app


def test_mapped_rows_prefers_confirmed():
    run = {
        "confirmed_rows": [{"security_id": 1, "qty": 10}],
        "review_rows": [{"security_id": 2, "qty": 5, "status": "mapped"}],
    }
    rows = rs._mapped_rows_from_run(run)
    assert len(rows) == 1
    assert rows[0]["security_id"] == 1


def test_refresh_investigation_rebuilds(monkeypatch):
    store = {
        "id": "r1",
        "status": "matched",
        "client_id": 37,
        "as_of": "2026-09-13",
        "confirmed_rows": [{"security_id": 1, "qty": 10, "security_symbol": "INFY"}],
        "match_result": {"stale": True},
        "breaks": [{"old": 1}],
    }

    def load(rid):
        return dict(store)

    def save(run):
        store.clear()
        store.update(run)

    monkeypatch.setattr(rs, "load_run", load)
    monkeypatch.setattr(rs, "save_run", save)

    def fake_match(cid, as_of, rows, progress_cb=None, **_kwargs):
        assert cid == 37
        assert as_of == date(2026, 9, 13)
        assert rows and rows[0]["security_id"] == 1
        if progress_cb:
            progress_cb(50, "mid", "test")
        return {
            "matched_count": 0,
            "break_count": 1,
            "breaks": [{"security_id": 1}],
            "overall": {"headlines": ["EMAIL (start here): test"]},
            "email_sync": {
                "ok": True,
                "skipped": True,
                "reason": "archive_fresh",
                "total_archived": 47,
            },
        }

    def fake_reload(run, progress_cb=None):
        # Unit test: skip Drive/local re-parse; keep confirmed holdings.
        if progress_cb:
            progress_cb(6, "reload_skip", "reload_statement")
        run = dict(run)
        run["match_result"] = None
        run["breaks"] = []
        return run

    monkeypatch.setattr(rs, "run_position_match", fake_match)
    monkeypatch.setattr(rs, "reload_statement_fresh", fake_reload)
    monkeypatch.setattr(rs, "set_run_progress", lambda *a, **k: None)
    out = rs.refresh_investigation("r1")
    assert out["status"] == "matched"
    assert out["match_result"]["break_count"] == 1
    assert out["match_result"].get("stale") is None
    assert out["last_email_sync"]["reason"] == "archive_fresh"
    assert out.get("investigation_refreshed_at")
