"""Trade statement parsers against Drive fixtures / OCR snippets."""
from pathlib import Path

import pytest

from services.reconciliation.trade_parsers import detect_and_parse_trade
from services.reconciliation.trade_parsers.order_book_screenshot import (
    parse_order_book_ocr_text,
)
from services.reconciliation.trade_parsers.zerodha_contract_note import (
    parse_zerodha_contract_text,
)

pytestmark = pytest.mark.no_app

FIX = Path(__file__).resolve().parent / "fixtures" / "trade_statements"
PASSWORDS = ["AECPA6630G", "AQUPD7523P"]


def _raw(name: str) -> Path:
    p = FIX / "_raw" / name
    if p.is_file():
        return p
    # fallback typed dirs from local copy
    for sub in ("zerodha_pdf", "zerodha_xlsx", "screenshots"):
        for cand in (FIX / sub).glob(f"*{name}*"):
            return cand
    raise FileNotFoundError(name)


def test_zerodha_contract_marico_paytm():
    data = _raw("05_24-07-2025-contract-notes_UDZ743 _2_.pdf").read_bytes()
    r = detect_and_parse_trade(
        data, filename="24-07-2025-contract-notes.pdf", passwords=PASSWORDS
    )
    assert r.format_id == "zerodha_contract_note"
    syms = {f.symbol for f in r.fills}
    assert "MARICO" in syms
    assert "PAYTM" in syms
    assert not any(s[0].isdigit() for s in syms)


def test_zerodha_contract_bajajcon_sell():
    data = _raw("02_17-06-2026-contract-notes_UDZ743 _1_.pdf").read_bytes()
    r = detect_and_parse_trade(
        data, filename="17-06-2026.pdf", passwords=PASSWORDS
    )
    assert any(
        f.symbol == "BAJAJCON" and f.side == "sell" and f.quantity == 70
        for f in r.fills
    )


def test_zerodha_tradebook_xlsx():
    data = _raw("17_April second trade 2026.xlsx").read_bytes()
    r = detect_and_parse_trade(data, filename="April second trade 2026.xlsx")
    assert r.format_id == "zerodha_tradebook_xlsx"
    assert len(r.fills) >= 20
    assert "PAYTM" in {f.symbol for f in r.fills} or "LIQUIDBEES" in {
        f.symbol for f in r.fills
    }


def test_soa_skipped():
    data = _raw("16_Sep 2026 Trade data.pdf").read_bytes()
    r = detect_and_parse_trade(data, filename="Sep 2026 Trade data.pdf", passwords=PASSWORDS)
    assert r.format_id == "zerodha_holdings_soa"
    assert r.fills == []


def test_angel_ocr_snippet():
    text = (FIX / "text_snippets" / "angel_order_book_ocr.txt").read_text()
    r = parse_order_book_ocr_text(text, source_name="angel.jpeg")
    by_broker = {(f.broker_symbol or f.symbol): f for f in r.fills}
    assert "SUPREMEIND" in by_broker
    # Qty 3/3 → filled 3 (not ordered remainder)
    assert by_broker["SUPREMEIND"].quantity == 3
    assert by_broker["SUPREMEIND"].price == 4215.25
    assert by_broker["SUPREMEIND"].amount == round(3 * 4215.25, 2)
    assert by_broker["SUPREMEIND"].status == "executed"
    assert by_broker["MARUTI"].quantity == 2
    assert by_broker["MARUTI"].price == 15810.0
    # LTIM is old ticker → canonical LTM
    assert "LTIM" in by_broker
    assert by_broker["LTIM"].symbol == "LTM"
    assert by_broker["LTIM"].broker_symbol == "LTIM"
    assert by_broker["LTIM"].quantity == 11
    assert by_broker["LTIM"].price == 5120.65
    assert by_broker["LTIM"].side == "buy"


def test_groww_order_report_ocr():
    text = (
        "Order Report\n"
        "11 Aug 2026\n"
        "AUROPHARMA 1,658.00\n"
        "BUY DEL NSE © EXECUTED at 3:29PM\n"
        "5/5 Shares ORDER AGAIN\n"
        "ETERNAL 318.00\n"
        "BUY DEL NSE @ EXECUTED at 3:29PM\n"
        "20 /20 Share ORDER AGAIN\n"
        "ATHERENERG 1,528.00\n"
        "BUY DEL NSE © EXECUTED at 3:25PM\n"
        "4/4 Share ORDER AGAIN\n"
    )
    from services.reconciliation.trade_parsers.order_book_screenshot import (
        parse_order_book_ocr_text,
    )

    r = parse_order_book_ocr_text(text, source_name="groww.jpeg")
    assert r.format_id == "groww_order_report_screenshot"
    by = {f.broker_symbol or f.symbol: f for f in r.fills}
    assert by["AUROPHARMA"].quantity == 5
    assert by["AUROPHARMA"].price == 1658.0
    assert by["AUROPHARMA"].side == "buy"
    assert by["AUROPHARMA"].trade_date == "2026-08-11"
    assert by["ETERNAL"].quantity == 20
    assert by["ATHERENERG"].quantity == 4


def test_infy_block_does_not_steal_next_qty():
    """INFY Qty 75/75 must not pick up NAUKRI's 35."""
    text = (
        "Order Book\nEXECUTED\n"
        "INFY 1,443.45\nQty 75/75 30-Sep-2025 Executed\nBUY DELIVERY MARKET\n"
        "NAUKRI 1,314.50\nQty 35/35 30-Sep-2025 Executed\nBUY DELIVERY MARKET\n"
        "IEX 139.25\nQty 300/300 30-Sep-2025 Executed\nBUY DELIVERY MARKET\n"
    )
    r = parse_order_book_ocr_text(text, source_name="infy.jpeg")
    by = {f.broker_symbol or f.symbol: f for f in r.fills}
    assert by["INFY"].quantity == 75
    assert by["INFY"].price == 1443.45
    assert by["NAUKRI"].quantity == 35
    assert by["IEX"].quantity == 300
    assert by["IEX"].price == 139.25


def test_icici_ocr_snippet():
    text = (FIX / "text_snippets" / "icici_order_book_ocr.txt").read_text()
    r = parse_order_book_ocr_text(text, source_name="icici.jpeg")
    gold = [f for f in r.fills if f.symbol == "GOLDBEES"]
    assert any(f.side == "sell" and f.quantity == 2200 for f in gold)
    assert any(f.symbol == "AUROPHARMA" and f.side == "buy" for f in r.fills)
    auro = next(f for f in r.fills if f.symbol == "AUROPHARMA")
    assert auro.price == 1084.67
    assert auro.amount == round(39 * 1084.67, 2)
    g2200 = next(f for f in gold if f.quantity == 2200)
    assert g2200.price == 90.75
    assert g2200.side == "sell"


def test_spaced_symbol_in_contract_text():
    text = (
        "1200000034313858 11:22:50 403238141 11:22:50 "
        "P A Y TM-EQ/INE982J01020 B NSE 7 0.0000 1097.65 (7683.55)\n"
        "1100000038614861 11:04:55 204332626 11:04:55 "
        "DI VISLAB-EQ/INE361B01024 B NSE 1 0.0000 5997.50 (5997.50)\n"
    )
    r = parse_zerodha_contract_text(text, source_name="t.pdf")
    syms = {f.symbol for f in r.fills}
    assert "PAYTM" in syms
    assert "DIVISLAB" in syms


def test_positions_lt_piind_and_ashares_ocr():
    """Positions: LT OCR (Ly), PIIND AShares→4, no junk IN/UN."""
    text = (
        "Orders Positions\nCash SOLD FROM PORTFOLIO\n"
        "SECUREIEX EXIT ALL\n"
        "CIEINDIA -215.00\n"
        "-75Shares * Avg. $411.70 LTP 411.90 (+0.05%)\n"
        "SOLD FROM PORTFOLIO\n"
        "Ly -%2.80\n"
        "2Shares + Avg. %4,008.60 LTP 24,007.20 (-0.32%)\n"
        "BUY DEL EXIT AT MARKET\n"
        "PIIND +%21.20\n"
        "AShares + Avg. 2,478.40 LTP 22,483.70 (+0.03%)\n"
        "BUY DEL EXIT AT MARKET\n"
    )
    r = parse_order_book_ocr_text(text, source_name="pos.jpeg")
    assert r.format_id == "broker_positions_screenshot"
    by = {f.symbol: f for f in r.fills}
    assert "LT" in by
    assert by["LT"].quantity == 2
    assert by["LT"].price == 4008.6
    assert by["PIIND"].quantity == 4
    assert by["PIIND"].price == 2478.4
    assert "IN" not in by and "UN" not in by


def test_holdings_atp_strips_rupee_ocr_glue():
    text = (
        "Holdings Equity\n"
        "REDINGTON -23.40 (-0.07%)\n"
        "20 X ATP 2242.17 LTP 242.00 (-0.33%)\n"
        "LTIM -24.12 (-0.04%)\n"
        "2X ATP 25,174.56 LTP %5,172.50 (+1.64%)\n"
        "INFY 2113.30 (+0.76%)\n"
        "10 X ATP 21,486.97 LTP $1,498.30 (+4.56%)\n"
    )
    r = parse_order_book_ocr_text(text, source_name="holdings.jpeg")
    by = {f.symbol: f for f in r.fills}
    assert by["REDINGTON"].quantity == 20
    assert by["REDINGTON"].price == 242.17
    assert by["LTM"].quantity == 2
    assert abs(by["LTM"].price - 5174.56) < 0.01
    assert by["INFY"].quantity == 10
    assert abs(by["INFY"].price - 1486.97) < 0.01


def test_positions_slash_shares_with_currency_noise():
    text = (
        "Orders Positions\n"
        "PIIND %3,059.40\n"
        "2/2 Shares LTP %3,061.10 (-2.50%)\n"
        "BUY DEL\n"
        "SHREECEM ~25,410.00\n"
        "1/1 Share LTP $25,415.00 (+0.49%)\n"
        "BUY DEL\n"
        "Lia) +7.80\n"
        "2 Shares * Avg. 4,031.00 LTP 4,034.90\n"
        "BUY DEL\n"
    )
    r = parse_order_book_ocr_text(text, source_name="hist.jpeg")
    by = {f.symbol: f for f in r.fills}
    assert by["PIIND"].quantity == 2
    assert by["PIIND"].price == 3059.4
    assert by["SHREECEM"].quantity == 1
    assert by["SHREECEM"].price == 25410.0
    assert by["LT"].quantity == 2
    assert by["LT"].price == 4031.0


def test_icici_smashed_goldbees_price():
    """OCR 'GOLDBEES 90773' → ~90.77 (decimal dropped)."""
    text = (
        "eATM Orders\n"
        "GOLDBEES 90773\n"
        "Qty 2200/2200 10-Sep-2025 Executed\n"
        "SELL eATM MARKET\n"
    )
    r = parse_order_book_ocr_text(text, source_name="icici.jpeg")
    g = next(f for f in r.fills if f.symbol == "GOLDBEES")
    assert g.quantity == 2200
    assert g.side == "sell"
    assert g.price is not None
    assert 90.0 <= g.price <= 91.0


def test_groww_divislab_executed_without_qty_line():
    """Truncated last Groww row: EXECUTED but Shares line cut off — still emit."""
    text = (
        "Order Report\n16 Jul 2026\n"
        "REDINGTON 280.25\n"
        "BUY DEL NSE EXECUTED at 11:54AM\n"
        "38/38 Share ORDER AGAIN\n"
        "DIVISLAB 7,375.00\n"
        "BUY DEL NSE EXECUTED at 11:53AM\n"
    )
    r = parse_order_book_ocr_text(text, source_name="groww.jpeg")
    by = {f.symbol: f for f in r.fills}
    assert "DIVISLAB" in by
    assert by["DIVISLAB"].price == 7375.0
    assert by["DIVISLAB"].side == "buy"
    assert by["DIVISLAB"].status == "executed"
    assert by["DIVISLAB"].quantity == 0
    assert any("qty" in w.lower() for w in r.warnings)


def test_cieindia_sold_from_portfolio_is_sell():
    text = (
        "Orders Positions\n"
        "CIEINDIA +%15.00\n"
        "-75Shares - Avg. 2411.70 LTP 2411.50 (-0.05%)\n"
        "SOLD FROM PORTFOLIO BUY AGAIN\n"
        "KOTAKBANK -23.75\n"
        "25Shares * Avg. $389.65 LTP $389.50\n"
        "BUY DEL EXIT AT MARKET\n"
    )
    r = parse_order_book_ocr_text(text, source_name="pos.jpeg")
    cie = next(f for f in r.fills if f.symbol == "CIEINDIA")
    assert cie.side == "sell"
    assert cie.quantity == 75
    assert abs(cie.price - 411.7) < 0.01


def test_jiofin_price_keeps_leading_two():
    """237.79 must not be ₹-stripped to 37.79."""
    text = (
        "Orders Positions\n"
        "JIOFIN +3.36\n"
        "24 Shares * Avg. 237.79 LTP 237.93 (-2.43%)\n"
        "BUY DEL EXIT AT MARKET\n"
    )
    r = parse_order_book_ocr_text(text, source_name="jio.jpeg")
    j = next(f for f in r.fills if f.symbol == "JIOFIN")
    assert j.quantity == 24
    assert abs(j.price - 237.79) < 0.01


def test_refine_ocr_price_recovers_dropped_digit():
    from services.reconciliation.trade_parsers.order_book_screenshot import (
        refine_ocr_price,
    )

    # Over-stripped / dropped leading 2
    assert abs(refine_ocr_price(37.79, 237.50) - 237.79) < 0.01
    # ₹ glue
    assert abs(refine_ocr_price(2411.70, 410.0) - 411.70) < 0.01
    # Already correct — leave alone
    assert abs(refine_ocr_price(237.79, 237.50) - 237.79) < 0.01
