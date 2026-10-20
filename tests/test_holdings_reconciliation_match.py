"""Match engine unit tests (no DB — monkeypatch portfolio)."""
from datetime import date

import pytest

from services.reconciliation import match_engine as me

pytestmark = pytest.mark.no_app


def test_match_detects_break_and_timing(monkeypatch):
    def fake_port(client_id, as_of):
        # On as_of: HDFC 10; one day later: 15 (statement has 15)
        if as_of == date(2026, 9, 10):
            qty = 10.0
        else:
            qty = 15.0
        return {
            "holdings": [
                {"security_id": 1, "symbol": "HDFCBANK", "quantity": qty},
            ]
        }

    monkeypatch.setattr(me, "_portfolio_qty_map", lambda cid, as_of: {
        h["security_id"]: {
            "security_id": h["security_id"],
            "symbol": h["symbol"],
            "qty": float(h["quantity"]),
        }
        for h in fake_port(cid, as_of)["holdings"]
    })
    monkeypatch.setattr(me, "settle_lag_days", lambda: 2)
    monkeypatch.setattr(me, "timing_batch_clear_ratio", lambda: 0.5)
    monkeypatch.setattr(me, "qty_abs_tolerance", lambda: 0.001)

    def fake_diag(
        *,
        client_id,
        as_of,
        breaks,
        timing=None,
        matched_count=0,
        matched_rows=None,
        statement_security_count=0,
        progress_cb=None,
        **_kwargs,
    ):
        out = []
        for b in breaks:
            out.append({**b, "diagnosis": {"top_hypothesis": "TEST"}})
        return out, {"headlines": ["test overall"], "break_count": len(out)}

    monkeypatch.setattr(
        "services.reconciliation.diagnosis_service.diagnose_breaks", fake_diag
    )
    monkeypatch.setattr(
        "services.client_email_archive_service.ensure_emails_ready_for_investigation",
        lambda cid: {
            "ok": True,
            "skipped": False,
            "reason": "synced_for_investigation",
            "imap_added": 2,
            "emaillog_added": 0,
            "total_archived": 2,
        },
    )

    lines = [{"security_id": 1, "security_symbol": "HDFCBANK", "qty": 15}]
    result = me.run_position_match(1, date(2026, 9, 10), lines)
    assert result["break_count"] == 1
    assert result["timing"]["applied"] is True
    assert result["timing"]["breaks_after_lag"] == []
    assert result["breaks"][0]["diagnosis"]["top_hypothesis"] == "TEST"
    assert result["overall"]["headlines"]
    assert result["overall"]["email_sync"]["reason"] == "synced_for_investigation"
    # Sync success is FYI, not an investigation headline
    assert any("Email sync" in f for f in (result["overall"].get("fyi") or []))
    assert "matched_rows" in result
