"""Unit tests for portfolio ↔ Prices sheet audit (no Google / DB)."""

from pathlib import Path

import pytest

from services import price_accuracy_prices_sheet_audit_service as mod

pytestmark = pytest.mark.no_app


def test_price_is_bad_tokens():
    assert mod._price_is_bad("#N/A")[0] is True
    assert mod._price_is_bad("")[0] is True
    assert mod._price_is_bad(None)[0] is True
    assert mod._price_is_bad(-1)[0] is True
    assert mod._price_is_bad(1250.5)[0] is False
    assert mod._price_is_bad("1,250.50")[0] is False


def test_formula_ticker_extract():
    assert mod._formula_ticker('=GOOGLEFINANCE("NSE:RELIANCE","price")') == "RELIANCE"
    assert mod._formula_ticker("=GOOGLEFINANCE(A2,\"price\")") is None
    assert mod._formula_ticker(250.0) is None


def test_sheet_missing_immediate():
    held = {"INFY": 1, "TCS": 2}
    sheet = [{"row": 2, "symbol": "INFY", "price_raw": 1500.0, "formula": "", "formula_ticker": None}]
    findings, streaks = mod.classify_sheet_rows(sheet, held, {})
    kinds = {f["kind"] for f in findings}
    assert "SHEET_MISSING" in kinds
    assert any(f["symbol"] == "TCS" for f in findings if f["kind"] == "SHEET_MISSING")
    assert not any(f["kind"] == "SHEET_PRICE_BAD" for f in findings)
    assert "TCS" not in streaks  # missing clears / never sets bad streak


def test_ticker_mismatch_immediate():
    held = {"INFY": 1}
    sheet = [
        {
            "row": 2,
            "symbol": "INFY",
            "price_raw": 1500.0,
            "formula": '=GOOGLEFINANCE("NSE:TCS","price")',
            "formula_ticker": "TCS",
        }
    ]
    findings, _ = mod.classify_sheet_rows(sheet, held, {})
    assert any(f["kind"] == "SHEET_TICKER_MISMATCH" for f in findings)


def test_sheet_price_bad_requires_five_consecutive():
    held = {"INFY": 1}
    sheet = [
        {
            "row": 2,
            "symbol": "INFY",
            "price_raw": "#N/A",
            "formula": '=GOOGLEFINANCE("NSE:INFY","price")',
            "formula_ticker": "INFY",
        }
    ]
    streaks = {}
    for i in range(1, 5):
        findings, streaks = mod.classify_sheet_rows(sheet, held, streaks)
        assert streaks["INFY"]["consecutive_bad"] == i
        assert not any(f["kind"] == "SHEET_PRICE_BAD" for f in findings)

    findings, streaks = mod.classify_sheet_rows(sheet, held, streaks)
    assert streaks["INFY"]["consecutive_bad"] == 5
    bad = [f for f in findings if f["kind"] == "SHEET_PRICE_BAD"]
    assert len(bad) == 1
    assert bad[0]["symbol"] == "INFY"


def test_good_price_resets_streak():
    held = {"INFY": 1}
    bad_sheet = [
        {
            "row": 2,
            "symbol": "INFY",
            "price_raw": "#N/A",
            "formula": "",
            "formula_ticker": None,
        }
    ]
    good_sheet = [
        {
            "row": 2,
            "symbol": "INFY",
            "price_raw": 1600.0,
            "formula": "",
            "formula_ticker": None,
        }
    ]
    streaks = {}
    for _ in range(4):
        _, streaks = mod.classify_sheet_rows(bad_sheet, held, streaks)
    assert streaks["INFY"]["consecutive_bad"] == 4
    findings, streaks = mod.classify_sheet_rows(good_sheet, held, streaks)
    assert streaks["INFY"]["consecutive_bad"] == 0
    assert not any(f["kind"] == "SHEET_PRICE_BAD" for f in findings)


def test_ops_email_only_missing():
    findings = [
        {"kind": "SHEET_MISSING", "symbol": "AAA", "detail": "x", "actionable": True},
        {
            "kind": "SHEET_PRICE_BAD",
            "symbol": "BBB",
            "detail": "y",
            "actionable": True,
            "streak": 5,
        },
    ]
    ops = mod.build_ops_email(findings, "2026-10-26 16:05 IST")
    assert ops is not None
    subject, body = ops
    assert "AAA" in body
    assert "BBB" not in body
    assert mod.SHEET_URL in body
    assert "missing" in subject.lower()


def test_ops_email_none_when_no_missing():
    findings = [
        {"kind": "SHEET_PRICE_BAD", "symbol": "BBB", "detail": "y", "actionable": True},
    ]
    assert mod.build_ops_email(findings, "stamp") is None


def test_anshul_email_includes_pa_and_sheet_kinds():
    findings = [
        {"kind": "SHEET_MISSING", "symbol": "AAA", "detail": "absent", "actionable": True},
        {
            "kind": "SHEET_TICKER_MISMATCH",
            "symbol": "BBB",
            "detail": "Symbol=BBB but GOOGLEFINANCE ticker=CCC",
            "actionable": True,
        },
        {
            "kind": "SHEET_PRICE_BAD",
            "symbol": "DDD",
            "detail": "#N/A for 5 consecutive",
            "actionable": True,
        },
    ]
    pa = [
        {
            "kind": "SPIKE",
            "symbol": "INFY",
            "detail": "2020-01-01 → 2020-01-02 (100 → 120, +20.00%)",
            "actionable": True,
        },
        {
            "kind": "MISSING",
            "symbol": "TCS",
            "detail": "3 finding(s) · e.g. 2021-01-01, 2021-01-02",
            "actionable": True,
            "raw_count": 3,
        },
    ]
    subject, body = mod.build_anshul_email(
        findings, "2026-10-26 16:05 IST", pa_findings=pa
    )
    assert "5 issue" in subject
    assert "INFY" in body and "TCS" in body
    assert "AAA" in body and "BBB" in body and "DDD" in body
    assert "SPIKE" in body
    assert "SHEET_PRICE_BAD" in body


def test_summarize_held_pa_groups_missing():
    raw = [
        {
            "kind": "SPIKE",
            "symbol": "INFY",
            "security_id": 1,
            "date_prev": "2020-01-01",
            "date_next": "2020-01-02",
            "close_prev": 100.0,
            "close_next": 120.0,
            "pct_change": 20.0,
        },
        {"kind": "MISSING", "symbol": "TCS", "security_id": 2, "missing_date": "2021-01-01"},
        {"kind": "MISSING", "symbol": "TCS", "security_id": 2, "missing_date": "2021-01-02"},
        {"kind": "MISSING", "symbol": "TCS", "security_id": 2, "missing_date": "2021-01-03"},
        {"kind": "ZERO", "symbol": "WIPRO", "security_id": 3, "missing_date": "2022-06-01"},
    ]
    out = mod.summarize_held_pa_findings(raw)
    kinds = {(f["kind"], f["symbol"]) for f in out}
    assert ("SPIKE", "INFY") in kinds
    assert ("MISSING", "TCS") in kinds
    assert ("ZERO", "WIPRO") in kinds
    tcs = next(f for f in out if f["kind"] == "MISSING")
    assert tcs["raw_count"] == 3
    assert "2021-01-01" in tcs["detail"]


def test_run_audit_with_injected_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_dir", lambda: Path(tmp_path))
    monkeypatch.setattr(
        mod,
        "send_audit_emails",
        lambda *a, **k: {"anshul_sent": True, "ops_sent": True, "errors": []},
    )
    monkeypatch.setattr(mod, "held_open_price_accuracy_findings", lambda held: [])

    held = {"INFY": 10, "GHOST": 11}
    sheet = [
        {
            "row": 2,
            "symbol": "INFY",
            "price_raw": "#N/A",
            "formula": '=GOOGLEFINANCE("NSE:INFY","price")',
            "formula_ticker": "INFY",
        }
    ]
    # Seed 4 prior bad runs via state
    state = {
        "streaks": {
            "INFY": {
                "consecutive_bad": 4,
                "last_status": "bad",
                "last_reason": "#N/A",
                "last_seen_at": "x",
            }
        },
        "last_email_ist_date": None,
    }
    mod.save_state(state)

    out = mod.run_prices_sheet_portfolio_audit(
        send_email=True,
        force_email=True,
        sheet_rows=sheet,
        held=held,
        pa_findings=[],
    )
    assert out["ok"] is True
    assert out["counts"]["SHEET_MISSING"] == 1
    assert out["counts"]["SHEET_PRICE_BAD"] == 1
    assert out["email"]["anshul_sent"] is True

    saved = mod.load_state()
    assert saved["streaks"]["INFY"]["consecutive_bad"] == 5
