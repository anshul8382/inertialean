from __future__ import annotations

import re
from datetime import datetime

from iis.config import settings
from iis.models.artifacts import CompanyInfo, MarketQuote

BSE_TO_NSE: dict[str, str] = {
    "500114": "TITAN",
    "500209": "INFY",
    "500325": "RELIANCE",
    "532540": "TCS",
    "500180": "HDFCBANK",
}


def _normalize_nse_ticker(ticker: str | None) -> str | None:
    if not ticker:
        return None
    text = str(ticker).upper().strip()
    if ":" in text:
        text = text.split(":")[-1]
    text = re.sub(r"[^A-Z0-9&-]", "", text)
    return text or None


def resolve_nse_ticker(company: CompanyInfo | None, estimates_summary: dict | None = None) -> str | None:
    if company and company.nse_ticker:
        return _normalize_nse_ticker(company.nse_ticker)
    if estimates_summary and estimates_summary.get("nse_ticker"):
        return _normalize_nse_ticker(str(estimates_summary["nse_ticker"]))
    if company and company.bse_code and company.bse_code in BSE_TO_NSE:
        return BSE_TO_NSE[company.bse_code]
    return None


def _yfinance_symbols(ticker: str | None, bse_code: str | None) -> list[tuple[str, str]]:
    symbols: list[tuple[str, str]] = []
    if ticker:
        symbols.append((f"{ticker}.NS", "NSE"))
        symbols.append((f"{ticker}.BO", "BSE"))
    if bse_code:
        bse_sym = f"{bse_code}.BO"
        if not any(s[0] == bse_sym for s in symbols):
            symbols.append((bse_sym, "BSE"))
    return symbols


def _read_yfinance_price(fast_info) -> float | None:
    for key in ("last_price", "lastPrice", "regularMarketPrice"):
        val = getattr(fast_info, key, None)
        if val is None and hasattr(fast_info, "get"):
            val = fast_info.get(key)
        price = _safe_float(val)
        if price and price > 0:
            return price
    return None


def _fetch_yfinance_price(ticker: str | None, bse_code: str | None) -> MarketQuote | None:
    try:
        import yfinance as yf
    except ImportError:
        return None

    for symbol, exchange in _yfinance_symbols(ticker, bse_code):
        try:
            price = _read_yfinance_price(yf.Ticker(symbol).fast_info)
            if not price:
                continue
            return MarketQuote(
                symbol=symbol,
                exchange=exchange,
                price=round(price, 2),
                currency="INR",
                source="yfinance",
                confidence="high",
                disclaimer="Price from Yahoo Finance — verify independently before trading.",
                source_urls=[f"https://finance.yahoo.com/quote/{symbol}"],
                fetched_at=datetime.utcnow(),
            )
        except Exception:
            continue
    return None


RELIABLE_PRICE_SOURCES = ("price_sheet", "yfinance")
PRICE_UNAVAILABLE = "price unavailable"


def is_reliable_quote(quote: MarketQuote | None) -> bool:
    return bool(quote and quote.price and quote.source in RELIABLE_PRICE_SOURCES)


def _fetch_sheet_price(company: CompanyInfo | None, ticker: str | None) -> MarketQuote | None:
    from iis.finance.price_sheet import find_listed, sheet_price

    symbol = ticker
    if not symbol and company:
        listed = find_listed(company.name)
        symbol = listed.symbol if listed else None
    if not symbol:
        return None
    price = sheet_price(symbol)
    if not price:
        return None
    return MarketQuote(
        symbol=symbol,
        exchange="NSE",
        price=round(price, 2),
        currency="INR",
        source="price_sheet",
        confidence="high",
        disclaimer="Price from the Inertia price sheet (GOOGLEFINANCE, may be delayed ~15-20 min).",
        source_urls=[f"https://docs.google.com/spreadsheets/d/{settings.price_sheet_id}"],
        fetched_at=datetime.utcnow(),
    )


def fetch_live_price(
    company: CompanyInfo | None,
    estimates_summary: dict | None = None,
    llm=None,
    *,
    force_refresh: bool = False,
) -> MarketQuote | None:
    """Price sheet → yfinance (NSE, then BSE) → None (caller shows "price unavailable")."""
    del llm, force_refresh
    if not settings.fetch_live_prices:
        return None

    ticker = resolve_nse_ticker(company, estimates_summary)
    bse = company.bse_code if company else None

    try:
        quote = _fetch_sheet_price(company, ticker)
    except Exception:
        quote = None
    if quote:
        return quote
    return _fetch_yfinance_price(ticker, bse)


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("₹", "").strip())
    except (ValueError, TypeError):
        return None
