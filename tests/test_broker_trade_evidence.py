"""Broker contract-note parse + unexecuted reco detection (unit)."""
from datetime import date

import pytest

from services.reconciliation.broker_trade_evidence import (
    _parse_fills_from_text,
    analyze_reco_vs_broker_and_books,
    build_booking_gap_story,
    build_reco_trade_map_rows,
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
    u0 = row["unexecuted_recos"][0]
    assert u0["verdict"] == "EXECUTED_NOT_RECORDED"
    assert u0.get("pipeline")
    assert any(s["key"] == "trade_books" and s["ok"] is False for s in u0["pipeline"])
    assert "books" in (u0.get("headline") or u0["detail"]).lower()
    assert "fill" not in (u0.get("headline") or "").lower()
    assert out.get("trade_map")
    assert out["trade_map"][0]["symbol"] == "MARICO"
    assert out["trade_map"][0]["reco_found"] is True

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


def test_ather_partial_of_june_30_skips_draft_buy_5(monkeypatch):
    """Holdings +3 belong to June BUY 30, not August draft BUY 5."""
    import services.reconciliation.broker_trade_evidence as be

    monkeypatch.setattr(
        be,
        "collect_broker_fills_from_drive",
        lambda cid, symbols=None, max_files=40: {
            "ok": True,
            "files_scanned": 1,
            "fills": [],
            "screenshots": [],
            "folder_id": "x",
            "decrypt_ok": 0,
            "decrypt_fail": 0,
            "passwords_configured": True,
        },
    )
    monkeypatch.setattr(be, "_system_trades_for_symbols", lambda cid, syms: {"ATHERENERG": []})
    monkeypatch.setattr(
        be,
        "_recos_for_symbols",
        lambda cid, syms: {
            "ATHERENERG": [
                {
                    "sent_at": "2026-06-16",
                    "when_label": "2026-06-16",
                    "action": "buy",
                    "quantity": 30.0,
                    "status": "sent",
                    "executed_at": None,
                    "session_name": "Portfolio Recommendations - June 2026",
                    "emailed": True,
                    "id": 7487,
                },
                {
                    "sent_at": None,
                    "when_label": "Portfolio Recommendations - August 2026",
                    "action": "buy",
                    "quantity": 5.0,
                    "status": "pending",
                    "executed_at": None,
                    "session_name": "Portfolio Recommendations - August 2026",
                    "emailed": False,
                    "id": 9500,
                },
            ]
        },
    )
    out = analyze_reco_vs_broker_and_books(
        99,
        symbols=["ATHERENERG"],
        break_context={
            "ATHERENERG": {"statement_qty": 3, "system_qty": 0, "delta": 3}
        },
    )
    rows = out["per_symbol"][0]["missing_from_books"]
    assert len(rows) == 1
    assert rows[0]["quantity"] == 30.0
    assert rows[0].get("partial") is True
    assert rows[0].get("held_qty") == 3.0
    assert "5" not in (rows[0].get("headline") or "")
    assert "partial" in (rows[0].get("headline") or "").lower()


def test_booking_gap_story_plain_language_when_not_in_trade_details():
    story = build_booking_gap_story(
        symbol="ATHERENERG",
        action="buy",
        quantity=30,
        when="2026-06-16",
        book_has_trade=False,
        trade_details_has=False,
        trade_folder_linked=True,
        holdings_ok=True,
    )
    assert story["verdict"] == "EXECUTED_NOT_RECORDED"
    assert "fill" not in story["headline"].lower()
    assert story["pipeline"][0]["key"] == "recommended" and story["pipeline"][0]["ok"] is True
    assert story["pipeline"][1]["key"] == "trade_books" and story["pipeline"][1]["ok"] is False
    assert story["pipeline"][2]["key"] == "trade_statement" and story["pipeline"][2]["ok"] is False
    assert story["pipeline"][3]["key"] == "holding" and story["pipeline"][3]["ok"] is True
    assert any("client" in s.lower() for s in story["next_steps"])


def test_booking_gap_story_when_found_in_trade_details():
    story = build_booking_gap_story(
        symbol="MARICO",
        action="buy",
        quantity=35,
        when="2026-05-08",
        book_has_trade=False,
        trade_details_has=True,
        trade_details_file="may-contract.pdf",
        holdings_ok=True,
    )
    assert "may-contract.pdf" in (story["evidence_note"] or "")
    assert any("add" in s.lower() for s in story["next_steps"])


def test_group_booking_gap_stories_coarse_dedupe():
    from services.reconciliation.broker_trade_evidence import (
        filter_hvr_hits_against_broker,
        group_booking_gap_stories,
        slim_remaining_notes_against_broker,
    )

    stories = [
        build_booking_gap_story(
            symbol="AAA",
            action="buy",
            quantity=10,
            when="2026-06-01",
            book_has_trade=False,
            trade_details_has=False,
            trade_folder_linked=True,
            holdings_ok=True,
        ),
        build_booking_gap_story(
            symbol="BBB",
            action="buy",
            quantity=20,
            when="2026-06-02",
            book_has_trade=False,
            trade_details_has=False,
            trade_folder_linked=True,
            holdings_ok=True,
        ),
        # Duplicate AAA row must collapse
        build_booking_gap_story(
            symbol="AAA",
            action="buy",
            quantity=10,
            when="2026-06-01",
            book_has_trade=False,
            trade_details_has=False,
            trade_folder_linked=True,
            holdings_ok=True,
        ),
        build_booking_gap_story(
            symbol="CCC",
            action="buy",
            quantity=5,
            when="2026-05-01",
            book_has_trade=False,
            trade_details_has=True,
            trade_details_file="note.pdf",
            holdings_ok=True,
        ),
    ]
    groups = group_booking_gap_stories(stories)
    # Two cards: Trade-details miss vs hit (not one per name)
    assert len(groups) == 2
    by_count = {g["count"]: g for g in groups}
    assert 2 in by_count and 1 in by_count
    assert "AAA" in by_count[2]["symbols"] and "BBB" in by_count[2]["symbols"]
    assert len(by_count[2]["next_steps"]) >= 2
    assert len(by_count[2]["item_labels"]) == 2
    assert by_count[1]["evidence_note"]  # CCC found in Trade details

    be = {"story_groups": groups}
    hvr = filter_hvr_hits_against_broker(
        {
            "hits": [
                {"symbol": "AAA", "hypothesis": "EXECUTED_NOT_RECORDED", "summary": "dup"},
                {"symbol": "ZZZ", "hypothesis": "HOLDINGS_EXCEED_RECO", "summary": "keep"},
            ],
            "summary": "2 names",
        },
        be,
    )
    assert len(hvr["hits"]) == 1
    assert hvr["hits"][0]["symbol"] == "ZZZ"

    slim = slim_remaining_notes_against_broker(
        [
            {"symbol": "AAA", "delta": 10, "summary": "long prose", "potential_issues": ["x"], "checks": ["y"]},
            {"symbol": "OTHER", "delta": 1, "summary": "keep me", "potential_issues": ["z"]},
        ],
        be,
    )
    assert slim[0]["summary"].startswith("Covered above")
    assert slim[0]["potential_issues"] == []
    assert slim[1]["summary"] == "keep me"


def test_save_and_load_link(tmp_path, monkeypatch):
    import services.reconciliation.broker_trade_evidence as be

    monkeypatch.setattr(be, "_repo_root", lambda: tmp_path)
    be.save_trade_drive_link(
        7, folder_id="16n8i0xNG72trLApjrtObXZ9o7J1ZUtfM", pdf_passwords=["ABC"]
    )
    row = be.load_trade_drive_link(7)
    assert row["folder_id"] == "16n8i0xNG72trLApjrtObXZ9o7J1ZUtfM"
    assert row["pdf_passwords"] == ["ABC"]
