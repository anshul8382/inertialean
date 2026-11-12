"""Fetch annual financials from Yahoo Finance and map to IIS FinancialLineItem."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from iis.models.artifacts import CompanyInfo, FinancialLineItem, SourceRef
from iis.models.enums import PeriodType, SourceTier

# Yahoo row label -> IIS line_item (first match wins per row)
INCOME_MAP: dict[str, str] = {
    "Total Revenue": "total_revenue",
    "Operating Revenue": "total_revenue",
    "EBITDA": "ebitda",
    "Normalized EBITDA": "ebitda",
    "Operating Income": "ebit",
    "Pretax Income": "pretax_income",
    "Net Income": "net_income",
    "Net Income Common Stockholders": "net_income",
    "Interest Expense Non Operating": "interest_expense",
    "Interest Expense": "interest_expense",
    "Tax Provision": "income_tax",
    "Reconciled Depreciation": "depreciation",
    "Reconciled Cost Of Revenue": "cost_of_goods_sold",
    "Cost Of Revenue": "cost_of_goods_sold",
    "Gross Profit": "gross_profit",
}

BALANCE_MAP: dict[str, str] = {
    "Total Assets": "total_assets",
    "Total Debt": "total_debt",
    "Cash And Cash Equivalents": "cash_and_equivalents",
    "Cash Cash Equivalents And Short Term Investments": "cash_and_equivalents",
    "Inventory": "inventory",
    "Accounts Receivable": "accounts_receivable",
    "Accounts Payable": "accounts_payable",
    "Common Stock Equity": "book_value_of_common_equity",
    "Stockholders Equity": "book_value_of_common_equity",
}

CASHFLOW_MAP: dict[str, str] = {
    "Operating Cash Flow": "cash_from_ops",
    "Capital Expenditure": "capital_expenditure",
    "Change In Working Capital": "change_in_net_working_capital",
    "Change In Inventory": "change_in_inventories",
    "Free Cash Flow": "free_cash_flow",
}


def _period_end(col: Any) -> tuple[str, str]:
    if hasattr(col, "strftime"):
        end = col.strftime("%Y-%m-%d")
        fy = col.year
        return end, f"FY{fy}"
    s = str(col)[:10]
    year = s[:4]
    return s, f"FY{year}"


def _to_mn(value: float | None, currency: str = "INR") -> float | None:
    if value is None or (isinstance(value, float) and value != value):
        return None
    # Yahoo reports INR in absolute rupees for .NS tickers
    if currency.upper() in ("INR", "₹", "RS"):
        return float(value) / 1_000_000.0
    return float(value) / 1_000_000.0


def _rows_from_df(
    df,
    statement: str,
    mapping: dict[str, str],
    symbol: str,
    currency: str,
) -> list[FinancialLineItem]:
    items: list[FinancialLineItem] = []
    if df is None or df.empty:
        return items
    for yf_label, line_item in mapping.items():
        if yf_label not in df.index:
            continue
        for col in df.columns:
            raw = df.loc[yf_label, col]
            val = _to_mn(float(raw) if raw == raw else None, currency)
            if val is None:
                continue
            period_end, period_label = _period_end(col)
            items.append(
                FinancialLineItem(
                    statement=statement,
                    line_item=line_item,
                    line_item_raw=yf_label,
                    period_end=period_end,
                    period_label=period_label,
                    period_type=PeriodType.ACTUAL,
                    value=val,
                    unit="INR_millions",
                    currency=currency or "INR",
                    source=SourceRef(file=f"yahoo:{symbol}", sheet=statement, url=f"https://finance.yahoo.com/quote/{symbol}"),
                    source_tier=SourceTier.EXTERNAL,
                )
            )
    return items


def _add_net_debt(items: list[FinancialLineItem], symbol: str) -> None:
    by_period: dict[str, dict[str, float]] = {}
    for i in items:
        if i.statement != "balance_sheet" or i.value is None:
            continue
        p = i.period_end or i.period_label
        by_period.setdefault(p, {})[i.line_item] = i.value
    for period, vals in by_period.items():
        debt = vals.get("total_debt")
        cash = vals.get("cash_and_equivalents")
        if debt is None or cash is None:
            continue
        label = next((i.period_label for i in items if i.period_end == period), period)
        items.append(
            FinancialLineItem(
                statement="balance_sheet",
                line_item="net_debt",
                line_item_raw="Net Debt (computed)",
                period_end=period,
                period_label=label,
                period_type=PeriodType.ACTUAL,
                value=debt - cash,
                unit="INR_millions",
                source=SourceRef(file=f"yahoo:{symbol}", sheet="balance_sheet"),
                source_tier=SourceTier.EXTERNAL,
            )
        )


def fetch_yahoo_financials(ticker: str) -> tuple[CompanyInfo | None, list[FinancialLineItem], dict[str, Any]]:
    """Pull up to 5Y annual statements for an NSE/BSE symbol via yfinance."""
    import yfinance as yf

    symbol = ticker if "." in ticker else f"{ticker.upper()}.NS"
    yt = yf.Ticker(symbol)
    info = yt.info or {}
    currency = info.get("currency") or "INR"

    items: list[FinancialLineItem] = []
    items.extend(_rows_from_df(yt.financials, "income_statement", INCOME_MAP, symbol, currency))
    items.extend(_rows_from_df(yt.balance_sheet, "balance_sheet", BALANCE_MAP, symbol, currency))
    items.extend(_rows_from_df(yt.cashflow, "cash_flow", CASHFLOW_MAP, symbol, currency))
    _add_net_debt(items, symbol)

    shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
    if shares:
        items.append(
            FinancialLineItem(
                statement="key_stats",
                line_item="shares_outstanding_mn",
                line_item_raw="Shares Outstanding",
                period_end=datetime.utcnow().strftime("%Y-%m-%d"),
                period_label="Latest",
                period_type=PeriodType.ACTUAL,
                value=float(shares) / 1_000_000.0,
                unit="millions",
                source=SourceRef(file=f"yahoo:{symbol}", sheet="info"),
                source_tier=SourceTier.EXTERNAL,
            )
        )

    company = CompanyInfo(
        name=info.get("longName") or info.get("shortName") or ticker,
        nse_ticker=ticker.upper().replace(".NS", "").replace(".BO", ""),
        accounting_standard="Ind AS (Yahoo)",
    )
    meta = {
        "symbol": symbol,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": currency,
        "line_item_count": len(items),
        "has_revenue": any(i.line_item == "total_revenue" for i in items),
        "has_ebitda": any(i.line_item == "ebitda" for i in items),
        "has_cashflow": any(i.line_item == "cash_from_ops" for i in items),
    }
    return company, items, meta
