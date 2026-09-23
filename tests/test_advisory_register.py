"""Unit tests for advisory register formatting, cutover detection, and email parse."""
from __future__ import annotations

from datetime import date, timedelta

from services.advisory_register_service import (
    detect_gmail_cutover_date,
    format_nature_and_products,
    parse_client_name_from_subject,
)
from services.gmail_sent_imap_service import parse_reco_email_body


def test_format_nature_and_products_buy_sell():
    nature, products = format_nature_and_products(
        [
            {
                "action": "buy",
                "symbol": "RELIANCE",
                "name": "Reliance",
                "amount": 250000,
                "asset_class": "Equity",
            },
            {
                "action": "sell",
                "symbol": "INFY",
                "name": "Infosys",
                "amount": 100000,
                "asset_class": "Equity",
            },
            {
                "action": "buy",
                "symbol": "GSEC",
                "name": "GSec",
                "amount": 500000,
                "asset_class": "Debt",
            },
            {"action": "hold", "symbol": "TCS", "amount": 1, "asset_class": "Equity"},
        ]
    )
    assert "Equity:" in nature
    assert "Debt:" in nature
    assert "BUY: RELIANCE" in products
    assert "GSEC" in products
    assert "SELL: INFY" in products
    assert "TCS" not in products


def test_parse_client_name_from_subject():
    name = parse_client_name_from_subject(
        "Portfolio Investment Recos for Anil Sharma Nov 2025"
    )
    assert name == "Anil Sharma"


def test_detect_gmail_cutover_requires_full_window():
    start = date(2025, 10, 1)
    msgs = []
    flags = []
    # 10 days all matched — not enough
    for i in range(10):
        msgs.append({"advice_date": start + timedelta(days=i)})
        flags.append(True)
    assert detect_gmail_cutover_date(msgs, flags) is None

    # Extend to 14 days all matched
    for i in range(10, 14):
        msgs.append({"advice_date": start + timedelta(days=i)})
        flags.append(True)
    cut = detect_gmail_cutover_date(msgs, flags)
    assert cut == start + timedelta(days=13)


def test_detect_gmail_cutover_fails_if_unmatched_in_window():
    start = date(2025, 11, 1)
    msgs = [{"advice_date": start + timedelta(days=i)} for i in range(14)]
    flags = [True] * 14
    flags[5] = False
    assert detect_gmail_cutover_date(msgs, flags) is None


def test_detect_gmail_cutover_empty_window_does_not_count():
    # One matched mail on 6/1: earliest 14-day window ending 6/14 contains that mail → cutover C=6/14
    msgs = [
        {"advice_date": date(2025, 6, 1)},
        {"advice_date": date(2025, 6, 20)},
    ]
    flags = [True, True]
    cut = detect_gmail_cutover_date(msgs, flags)
    assert cut == date(2025, 6, 14)


def test_gmail_import_allowed_fy_policy():
    from services.advisory_register_service import gmail_import_allowed_for_fy

    assert gmail_import_allowed_for_fy(date(2025, 4, 1)) is True  # FY 2025-26
    assert gmail_import_allowed_for_fy(date(2026, 4, 1)) is False  # FY 2026-27+
    assert gmail_import_allowed_for_fy(date(2027, 4, 1)) is False


def test_parse_reco_email_html_buy_sell():
    html = """
    <table>
      <tr><td colspan="5">Buy</td></tr>
      <tr>
        <td>Reliance Industries</td><td>RELIANCE</td><td>10</td><td>2500</td><td>25000</td>
      </tr>
      <tr><td colspan="4">Total Buy Recos</td><td>25000</td></tr>
    </table>
    <table>
      <tr><td colspan="5">Sell</td></tr>
      <tr>
        <td>Infosys</td><td>INFY</td><td>5</td><td>1500</td><td>7500</td>
      </tr>
    </table>
    """
    parsed = parse_reco_email_body(html)
    assert "RELIANCE" in parsed["products"]
    assert "INFY" in parsed["products"]
    assert "BUY:" in parsed["products"]
    assert "SELL:" in parsed["products"]
    assert "RELIANCE" in parsed["symbols"]
