"""Broker contract-note parse + unexecuted reco detection (unit)."""
from datetime import date

import pytest

from services.reconciliation.broker_trade_evidence import (
    _parse_fills_from_text,
    analyze_reco_vs_broker_and_books,
)

pytestmark = pytest.mark.no_app


def test_parse_marico_contract_line():
    text = """
INE196A01026 MARICO 10 706.6000 0.0000 706.6000 7066.00
1200000034060601 11:21:36 403215215 11:21:37 MARICO-EQ/INE196A01026 B NSE 10 0.0000 706.6 (7066.00)
"""
    fills = _parse_fills_from_text(
        text, source_name="24-07-2025.pdf", trade_date=date(2025, 7, 24)
    )
    assert any(f["symbol"] == "MARICO" and f["quantity"] == 10 for f in fills)


def test_unexecuted_reco_when_no_book_no_broker(monkeypatch):
    import services.reconciliation.broker_trade_evidence as be

    monkeypatch.setattr(
        be,
        "collect_broker_fills_from_drive",
        lambda cid, symbols=None, max_files=40: {
            "ok": True,
            "files_scanned": 4,
            "decrypt_ok": 4,
            "decrypt_fail": 0,
            "fills": [
                # June note has BAJAJCON only — no MARICO
                {
                    "symbol": "BAJAJCON",
                    "quantity": 70,
                    "side": "sell",
                    "trade_date": "2026-06-17",
                    "source_file": "june.pdf",
                }
            ],
            "screenshots": [],
            "passwords_configured": True,
            "folder_id": "x",
        },
    )
    monkeypatch.setattr(
        be,
        "_system_trades_for_symbols",
        lambda cid, syms: {
            "MARICO": [
                {
                    "date": "2024-09-24",
                    "type": "BUY",
                    "quantity": 14.0,
                    "price": 700.0,
                    "id": 1,
                },
                # Sep/Oct 2026 buys exist but not May 35
                {
                    "date": "2026-09-11",
                    "type": "BUY",
                    "quantity": 8.0,
                    "price": 802.0,
                    "id": 2,
                },
            ]
        },
    )
    monkeypatch.setattr(
        be,
        "_recos_for_symbols",
        lambda cid, syms: {
            "MARICO": [
                {
                    "sent_at": "2026-05-08",
                    "action": "buy",
                    "quantity": 35.0,
                    "status": "sent",
                    "executed_at": None,
                    "session_name": "May 2026",
                    "id": 9,
                }
            ]
        },
    )

    # Holdings ahead of books → executed but not recorded (not "not executed")
    out = analyze_reco_vs_broker_and_books(
        99,
        symbols=["MARICO"],
        break_context={
            "MARICO": {
                "statement_qty": 105,
                "system_qty": 70,
                "delta": 35,
            }
        },
    )
    assert out["findings"]
    assert any("35" in f and "MARICO" in f for f in out["findings"])
    row = out["per_symbol"][0]
    assert row["unexecuted_recos"]
    assert row["unexecuted_recos"][0]["verdict"] == "EXECUTED_NOT_RECORDED"
    assert "missed recording" in row["unexecuted_recos"][0]["detail"].lower()

    # No holdings context / not ahead → may still be not executed
    out2 = analyze_reco_vs_broker_and_books(
        99,
        symbols=["MARICO"],
        break_context={
            "MARICO": {"statement_qty": 70, "system_qty": 70, "delta": 0}
        },
    )
    assert out2["per_symbol"][0]["unexecuted_recos"][0]["verdict"] == "RECO_NOT_EXECUTED"


def test_split_fills_count_as_executed(monkeypatch):
    import services.reconciliation.broker_trade_evidence as be

    monkeypatch.setattr(
        be,
        "collect_broker_fills_from_drive",
        lambda cid, symbols=None, max_files=40: {
            "ok": True,
            "files_scanned": 1,
            "fills": [
                {
                    "symbol": "PAYTM",
                    "quantity": 24.0,
                    "side": "buy",
                    "trade_date": "2026-04-10",
                    "source_file": "apr.xlsx",
                },
                {
                    "symbol": "PAYTM",
                    "quantity": 6.0,
                    "side": "buy",
                    "trade_date": "2026-04-10",
                    "source_file": "apr.xlsx",
                },
            ],
            "screenshots": [],
            "folder_id": "x",
            "decrypt_ok": 1,
            "decrypt_fail": 0,
            "passwords_configured": True,
        },
    )
    monkeypatch.setattr(
        be,
        "_system_trades_for_symbols",
        lambda cid, syms: {
            "PAYTM": [
                {
                    "date": "2026-04-09",
                    "type": "BUY",
                    "quantity": 24.0,
                    "id": 1,
                },
                {
                    "date": "2026-04-09",
                    "type": "BUY",
                    "quantity": 6.0,
                    "id": 2,
                },
            ]
        },
    )
    monkeypatch.setattr(
        be,
        "_recos_for_symbols",
        lambda cid, syms: {
            "PAYTM": [
                {
                    "sent_at": "2026-04-10",
                    "action": "buy",
                    "quantity": 30.0,
                    "status": "sent",
                    "executed_at": None,
                    "id": 3,
                }
            ]
        },
    )
    out = analyze_reco_vs_broker_and_books(99, symbols=["PAYTM"])
    assert out["findings"] == []
    assert out["per_symbol"][0]["unexecuted_recos"] == []


def test_save_and_load_link(tmp_path, monkeypatch):
    import services.reconciliation.broker_trade_evidence as be

    monkeypatch.setattr(be, "_repo_root", lambda: tmp_path)
    be.save_trade_drive_link(
        7, folder_id="16n8i0xNG72trLApjrtObXZ9o7J1ZUtfM", pdf_passwords=["ABC"]
    )
    row = be.load_trade_drive_link(7)
    assert row["folder_id"] == "16n8i0xNG72trLApjrtObXZ9o7J1ZUtfM"
    assert row["pdf_passwords"] == ["ABC"]
