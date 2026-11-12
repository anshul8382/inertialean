"""Financial ratio library for Indian equities (INR millions unless noted).

Ratios are computed deterministically from parsed line items. When Capital IQ
Key Stats already reports a ratio (ROE, ROA, current ratio), we prefer our
calculation when inputs exist and fall back to reported values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from iis.models.artifacts import FinancialLineItem
from iis.models.enums import PeriodType


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _pct(value: float | None) -> float | None:
    """Normalize ratio that may be stored as percent (37.1) vs decimal (0.371)."""
    if value is None:
        return None
    return value / 100 if abs(value) > 1.5 else value


def _abs_val(value: float | None) -> float | None:
    if value is None:
        return None
    return abs(value)


@dataclass
class PeriodMetrics:
    period: str
    values: dict[str, float] = field(default_factory=dict)

    def get(self, key: str, *aliases: str) -> float | None:
        for k in (key, *aliases):
            if k in self.values:
                return self.values[k]
        return None


@dataclass
class RatioResult:
    name: str
    label: str
    value: float | None
    unit: str  # "ratio", "percent", "days", "x"
    category: str
    formula: str
    source: str = "computed"  # computed | reported | annual_report


def build_period_metrics(items: list[FinancialLineItem]) -> dict[str, PeriodMetrics]:
    """Group reported line items by fiscal period (excludes estimates & press releases)."""
    from iis.finance.dcf_sanity import is_estimate_period_label, is_material_revenue

    by_period: dict[str, dict[str, float]] = {}
    for item in items:
        if item.statement == "ratios":
            continue
        if item.period_type in (PeriodType.ESTIMATE, PeriodType.PRESS_RELEASE):
            continue
        if is_estimate_period_label(item.period_label):
            continue
        if item.value is None:
            continue
        period = item.period_end or item.period_label
        by_period.setdefault(period, {})[item.line_item] = float(item.value)

    filtered: dict[str, PeriodMetrics] = {}
    for period, values in by_period.items():
        if not is_material_revenue(values.get("total_revenue")):
            continue
        filtered[period] = PeriodMetrics(period=period, values=values)
    return filtered


def compute_period_ratios(m: PeriodMetrics) -> dict[str, float | None]:
    """Compute all ratios for one fiscal period."""
    revenue = m.get("total_revenue", "revenue", "as_reported_total_revenue")
    gross_profit = m.get("gross_profit")
    ebitda = m.get("ebitda")
    ebit = m.get("ebit")
    net_income = m.get("net_income")
    total_assets = m.get("total_assets", "assets")
    total_debt = m.get("total_debt")
    cash = m.get("total_cash_st_investments", "cash_short_term_investments", "cash_and_equivalents")
    equity = m.get("total_common_equity", "book_value_of_common_equity", "total_equity")
    total_capital = m.get("total_capital")
    if total_capital is None and equity is not None:
        total_capital = equity + (total_debt or 0)
    cur_assets = m.get("total_current_assets")
    cur_liab = m.get("total_current_liabilities")
    cogs = m.get("cost_of_goods_sold")
    inventory = m.get("inventory")
    ar = m.get("accounts_receivable")
    ap = m.get("accounts_payable")
    interest = _abs_val(m.get("interest_expense"))
    capex = m.get("capital_expenditure")
    cfo = m.get("cash_from_ops")
    current_ratio_rpt = m.get("current_ratio")
    roe_rpt = _pct(m.get("return_on_equity_"))
    roa_rpt = _pct(m.get("return_on_assets_"))

    net_debt = None
    if total_debt is not None and cash is not None:
        net_debt = total_debt - cash
    elif m.get("net_debt") is not None:
        net_debt = m.get("net_debt")

    fcf = None
    if cfo is not None and capex is not None:
        fcf = cfo + capex if capex < 0 else cfo - capex

    ratios: dict[str, float | None] = {
        # Profitability
        "gross_margin": _safe_div(gross_profit, revenue),
        "ebitda_margin": _safe_div(ebitda, revenue),
        "ebit_margin": _safe_div(ebit, revenue),
        "net_margin": _safe_div(net_income, revenue),
        "roe": _safe_div(net_income, equity) if equity else roe_rpt,
        "roa": _safe_div(net_income, total_assets) if total_assets else roa_rpt,
        "roce": _safe_div(ebit, total_capital) if total_capital else None,
        # Liquidity
        "current_ratio": current_ratio_rpt if current_ratio_rpt is not None else _safe_div(cur_assets, cur_liab),
        "quick_ratio": _safe_div(cur_assets - (inventory or 0), cur_liab) if cur_assets is not None and cur_liab else None,
        # Leverage
        "debt_to_equity": _safe_div(total_debt, equity) if equity else None,
        "debt_to_assets": _safe_div(total_debt, total_assets) if total_assets else None,
        "net_debt_to_ebitda": _safe_div(net_debt, ebitda) if ebitda else None,
        "interest_coverage": _safe_div(ebit, interest) if interest else None,
        # Efficiency
        "asset_turnover": _safe_div(revenue, total_assets),
        "inventory_turnover": _safe_div(cogs, inventory) if inventory else None,
        "receivable_days": _safe_div(ar, revenue) * 365 if ar and revenue else None,
        "payable_days": _safe_div(ap, cogs) * 365 if ap and cogs else None,
        "inventory_days": _safe_div(inventory, cogs) * 365 if inventory and cogs else None,
        # Cash flow
        "fcf_mn": fcf,
        "fcf_margin": _safe_div(fcf, revenue),
        "capex_to_sales": _safe_div(_abs_val(capex), revenue),
        "cfo_to_ebitda": _safe_div(cfo, ebitda),
        "cfo_to_ni": _safe_div(cfo, net_income) if net_income else None,
    }
    return ratios


# Prefer transparent definitions from formulas.py when present
try:
    from iis.finance.formulas import FORMULA_CATALOG as _FORMULAS

    RATIO_CATALOG: dict[str, dict[str, str]] = {
        k: {
            "label": v["label"],
            "category": v["category"],
            "unit": v["unit"],
            "formula": v["formula"],
        }
        for k, v in _FORMULAS.items()
        if k != "fcf_mn"
    }
except Exception:
    RATIO_CATALOG = {
        "gross_margin": {"label": "Gross Margin", "category": "Profitability", "unit": "percent", "formula": "Gross Profit / Revenue"},
        "ebitda_margin": {"label": "EBITDA Margin", "category": "Profitability", "unit": "percent", "formula": "EBITDA / Revenue"},
        "ebit_margin": {"label": "EBIT Margin", "category": "Profitability", "unit": "percent", "formula": "EBIT / Revenue"},
        "net_margin": {"label": "Net Margin", "category": "Profitability", "unit": "percent", "formula": "Net Income / Revenue"},
        "roe": {"label": "Return on Equity (ROE)", "category": "Profitability", "unit": "percent", "formula": "Net Income / Shareholders' Equity"},
        "roa": {"label": "Return on Assets (ROA)", "category": "Profitability", "unit": "percent", "formula": "Net Income / Total Assets"},
        "roce": {"label": "Return on Capital Employed (ROCE)", "category": "Profitability", "unit": "percent", "formula": "EBIT / Total Capital"},
        "current_ratio": {"label": "Current Ratio", "category": "Liquidity", "unit": "x", "formula": "Current Assets / Current Liabilities"},
        "quick_ratio": {"label": "Quick Ratio (proxy)", "category": "Liquidity", "unit": "x", "formula": "(Cash + Receivables) / Payables"},
        "debt_to_equity": {"label": "Debt / Equity", "category": "Leverage", "unit": "x", "formula": "Total Debt / Equity"},
        "debt_to_assets": {"label": "Debt / Assets", "category": "Leverage", "unit": "x", "formula": "Total Debt / Total Assets"},
        "net_debt_to_ebitda": {"label": "Net Debt / EBITDA", "category": "Leverage", "unit": "x", "formula": "Net Debt / EBITDA"},
        "interest_coverage": {"label": "Interest Coverage", "category": "Leverage", "unit": "x", "formula": "EBIT / Interest Expense"},
        "asset_turnover": {"label": "Asset Turnover", "category": "Efficiency", "unit": "x", "formula": "Revenue / Total Assets"},
        "inventory_turnover": {"label": "Inventory Turnover", "category": "Efficiency", "unit": "x", "formula": "COGS / Inventory"},
        "receivable_days": {"label": "Receivable Days", "category": "Efficiency", "unit": "days", "formula": "(AR / Revenue) × 365"},
        "payable_days": {"label": "Payable Days", "category": "Efficiency", "unit": "days", "formula": "(AP / COGS) × 365"},
        "inventory_days": {"label": "Inventory Days", "category": "Efficiency", "unit": "days", "formula": "(Inventory / COGS) × 365"},
        "fcf_margin": {"label": "FCF Margin", "category": "Cash Flow", "unit": "percent", "formula": "Free Cash Flow / Revenue"},
        "capex_to_sales": {"label": "Capex / Sales", "category": "Cash Flow", "unit": "percent", "formula": "Capital Expenditure / Revenue"},
        "cfo_to_ebitda": {"label": "CFO / EBITDA", "category": "Cash Flow", "unit": "x", "formula": "Cash from Operations / EBITDA"},
        "cfo_to_ni": {"label": "CFO / Net Income", "category": "Cash Flow", "unit": "x", "formula": "Cash from Operations / Net Income"},
    }


def compute_all_ratios(items: list[FinancialLineItem]) -> dict[str, Any]:
    """Compute ratios for all periods; return latest snapshot + history."""
    from iis.finance.dcf_sanity import pick_latest_reporting_period

    periods = build_period_metrics(items)
    sorted_periods = sorted(periods.keys(), reverse=True)
    by_period: dict[str, dict[str, float | None]] = {}
    for period in sorted_periods:
        by_period[period] = compute_period_ratios(periods[period])

    latest_period = pick_latest_reporting_period(items)
    if not latest_period or latest_period not in by_period:
        latest_period = sorted_periods[0] if sorted_periods else None
    latest_ratios = by_period.get(latest_period, {}) if latest_period else {}

    catalog = []
    for key, meta in RATIO_CATALOG.items():
        val = latest_ratios.get(key)
        catalog.append(
            RatioResult(
                name=key,
                label=meta["label"],
                value=val,
                unit=meta["unit"],
                category=meta["category"],
                formula=meta["formula"],
            )
        )

    return {
        "latest_period": latest_period,
        "latest_ratios": latest_ratios,
        "ratios_by_period": by_period,
        "catalog": [r.__dict__ for r in catalog if r.value is not None],
        "periods": sorted_periods,
    }


def compute_ratios_from_ar_statements(
    income: dict[str, float | None],
    balance: dict[str, float | None],
    cashflow: dict[str, float | None] | None = None,
) -> dict[str, float | None]:
    """Compute ratios from annual-report-extracted statement line items."""
    merged = {**balance, **income, **(cashflow or {})}
    period = PeriodMetrics(period="annual_report", values={k: v for k, v in merged.items() if v is not None})
    return compute_period_ratios(period)


def format_ratio_value(name: str, value: float | None) -> str:
    if value is None:
        return "N/A"
    meta = RATIO_CATALOG.get(name, {})
    unit = meta.get("unit", "ratio")
    if unit == "percent":
        return f"{value:.1%}"
    if unit == "days":
        return f"{value:.0f} days"
    if unit == "x":
        return f"{value:.2f}x"
    return f"{value:,.2f}"


def find_ratio_by_keyword(question: str) -> str | None:
    """Map natural language to ratio key."""
    lower = question.lower()
    aliases = {
        "gross_margin": ["gross margin"],
        "ebitda_margin": ["ebitda margin"],
        "net_margin": ["net margin", "profit margin"],
        "roe": ["return on equity", "roe"],
        "roa": ["return on assets", "roa"],
        "roce": ["return on capital", "roce"],
        "current_ratio": ["current ratio"],
        "debt_to_equity": ["debt to equity", "debt/equity", "leverage"],
        "net_debt_to_ebitda": ["net debt", "nd/ebitda"],
        "interest_coverage": ["interest coverage"],
        "asset_turnover": ["asset turnover"],
        "inventory_turnover": ["inventory turnover"],
        "receivable_days": ["receivable days", "debtor days"],
        "payable_days": ["payable days", "creditor days"],
        "fcf_margin": ["fcf margin", "free cash flow margin"],
        "capex_to_sales": ["capex to sales", "capex/sales"],
    }
    for key, terms in aliases.items():
        if any(t in lower for t in terms):
            return key
    if "ratio" in lower or "margin" in lower:
        return "ebitda_margin"
    return None


def get_latest_ratio_summary(items: list[FinancialLineItem]) -> dict[str, float | None]:
    """Flat dict of latest ratios for metrics.py and MoS."""
    result = compute_all_ratios(items)
    return result.get("latest_ratios", {})
