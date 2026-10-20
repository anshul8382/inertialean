"""Parser + as-of guess tests (no app / DB)."""
from pathlib import Path

import pytest

from services.reconciliation.as_of_guess import guess_from_filename, merge_as_of
from services.reconciliation.parsers import detect_and_parse
from services.reconciliation.rules_loader import clear_rules_cache, load_rules

pytestmark = pytest.mark.no_app

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "holdings_statements"


def test_rules_load():
    clear_rules_cache()
    rules = load_rules()
    assert rules.get("version")
    assert "zerodha_kite_csv" in (rules.get("formats") or {})


def test_guess_filename_day_and_month():
    d, src = guess_from_filename("Stocks_Holdings_Statement_2948739231_06-09-2026.xlsx")
    assert d and d.isoformat() == "2026-09-06"
    assert src == "filename_day"
    d2, src2 = guess_from_filename("Devyani August 2026 holding statement .xlsx")
    assert d2 and d2.month == 8 and d2.year == 2026
    assert src2 == "filename_month"


def test_zerodha_csv():
    r = detect_and_parse(FIXTURES / "zerodha_sample.csv")
    assert r.format_id == "zerodha_kite_csv"
    assert len(r.lines) >= 20
    assert any(ln.nse_symbol == "HDFCBANK" for ln in r.lines)


def test_zerodha_kite_xlsx():
    r = detect_and_parse(
        FIXTURES / "zerodha_kite_sample.xlsx",
        filename="Aug 2026 holding .xlsx",
    )
    assert r.format_id == "zerodha_kite_xlsx"
    assert len(r.lines) >= 3
    assert any(ln.nse_symbol == "HDFCBANK" for ln in r.lines)
    assert any(ln.nse_symbol == "APLAPOLLO" for ln in r.lines)
    # as-of from filename month
    assert r.as_of and r.as_of.month == 8 and r.as_of.year == 2026


def test_axis_direct_holdings_xlsx():
    r = detect_and_parse(FIXTURES / "axis_direct_holdings_sample.xlsx")
    assert r.format_id == "axis_direct_holdings_xlsx"
    assert r.as_of and r.as_of.isoformat() == "2026-09-01"
    assert len(r.lines) >= 3
    assert any(ln.isin == "INE040A01034" for ln in r.lines)
    assert any("Ather" in (ln.raw_name or "") for ln in r.lines)


def test_groww_xlsx():
    r = detect_and_parse(FIXTURES / "groww_sample.xlsx")
    assert r.format_id == "groww_holdings_xlsx"
    assert r.as_of and r.as_of.isoformat() == "2026-09-06"
    assert len(r.lines) >= 5
    assert any(ln.isin and ln.isin.startswith("INE") for ln in r.lines)


def test_groww_style_subhodeep():
    r = detect_and_parse(FIXTURES / "groww_style_subhodeep.xlsx")
    assert r.format_id == "groww_holdings_xlsx"
    assert r.as_of and r.as_of.isoformat() == "2026-10-06"
    assert any(ln.nse_symbol == "APLAPOLLO" for ln in r.lines)


def test_cdsl_demat():
    r = detect_and_parse(FIXTURES / "cdsl_demat_raghu.xlsx")
    assert r.format_id == "cdsl_demat_xlsx"
    assert r.as_of and r.as_of.isoformat() == "2026-09-13"
    assert len(r.lines) >= 10


def test_broker_shortcode():
    r = detect_and_parse(
        FIXTURES / "broker_shortcode_devyani.xlsx",
        filename="Devyani August 2026 holding statement .xlsx",
    )
    assert r.format_id == "broker_shortcode_xlsx"
    assert any(ln.broker_code for ln in r.lines)
    assert any(ln.isin for ln in r.lines)
    # filename month guess after merge
    assert r.as_of and r.as_of.month == 8


def test_angel_dp_pdf():
    r = detect_and_parse(FIXTURES / "angel_dp_sachin.pdf")
    assert r.format_id == "angel_dp_pdf"
    assert len(r.lines) >= 3
    assert all(ln.isin and ln.qty > 0 for ln in r.lines)
    # period end preferred
    assert r.as_of is not None


def test_merge_prefers_header():
    from datetime import date

    d, src, conf = merge_as_of(
        date(2026, 9, 13), "header", "high", "Raghu Sep 2026.xlsx", None
    )
    assert d.isoformat() == "2026-09-13"
    assert src == "header"
    assert conf == "high"
