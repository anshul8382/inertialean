"""Transparent ratio / check formulas (FinanceToolkit-style documentation).

All formulas are computed elsewhere from Capital IQ line items.
This module is the single place that documents *how* each metric is defined.
"""
from __future__ import annotations

from typing import Any

# Canonical formula registry — keep in sync with iis.finance.ratios.RATIO_CATALOG
FORMULA_CATALOG: dict[str, dict[str, str]] = {
    "gross_margin": {
        "label": "Gross Margin",
        "category": "Profitability",
        "unit": "percent",
        "formula": "Gross Profit / Total Revenue",
        "ciq_inputs": "gross_profit, total_revenue",
    },
    "ebitda_margin": {
        "label": "EBITDA Margin",
        "category": "Profitability",
        "unit": "percent",
        "formula": "EBITDA / Total Revenue",
        "ciq_inputs": "ebitda, total_revenue",
    },
    "ebit_margin": {
        "label": "EBIT Margin",
        "category": "Profitability",
        "unit": "percent",
        "formula": "EBIT / Total Revenue",
        "ciq_inputs": "ebit, total_revenue",
    },
    "net_margin": {
        "label": "Net Margin",
        "category": "Profitability",
        "unit": "percent",
        "formula": "Net Income / Total Revenue",
        "ciq_inputs": "net_income, total_revenue",
    },
    "roe": {
        "label": "Return on Equity (ROE)",
        "category": "Profitability",
        "unit": "percent",
        "formula": "Net Income / Book Value of Common Equity",
        "ciq_inputs": "net_income, book_value_of_common_equity",
    },
    "roa": {
        "label": "Return on Assets (ROA)",
        "category": "Profitability",
        "unit": "percent",
        "formula": "Net Income / Total Assets",
        "ciq_inputs": "net_income, total_assets",
    },
    "roce": {
        "label": "Return on Capital Employed (ROCE)",
        "category": "Profitability",
        "unit": "percent",
        "formula": "EBIT / Total Capital (CIQ; else equity + total debt)",
        "ciq_inputs": "ebit, total_capital | total_common_equity + total_debt",
    },
    "current_ratio": {
        "label": "Current Ratio",
        "category": "Liquidity",
        "unit": "x",
        "formula": "Current Assets / Current Liabilities (reported Key Stats when available)",
        "ciq_inputs": "current_ratio | total_current_assets, total_current_liabilities",
    },
    "quick_ratio": {
        "label": "Quick Ratio",
        "category": "Liquidity",
        "unit": "x",
        "formula": "(Current Assets − Inventory) / Current Liabilities",
        "ciq_inputs": "total_current_assets, inventory, total_current_liabilities",
    },
    "debt_to_equity": {
        "label": "Debt / Equity",
        "category": "Leverage",
        "unit": "x",
        "formula": "Total Debt / Book Equity",
        "ciq_inputs": "total_debt, book_value_of_common_equity",
    },
    "debt_to_assets": {
        "label": "Debt / Assets",
        "category": "Leverage",
        "unit": "x",
        "formula": "Total Debt / Total Assets",
        "ciq_inputs": "total_debt, total_assets",
    },
    "net_debt_to_ebitda": {
        "label": "Net Debt / EBITDA",
        "category": "Leverage",
        "unit": "x",
        "formula": "(Total Debt − Cash) / EBITDA",
        "ciq_inputs": "total_debt, cash_and_equivalents, ebitda",
    },
    "interest_coverage": {
        "label": "Interest Coverage",
        "category": "Leverage",
        "unit": "x",
        "formula": "EBIT / Interest Expense",
        "ciq_inputs": "ebit, interest_expense",
    },
    "asset_turnover": {
        "label": "Asset Turnover",
        "category": "Efficiency",
        "unit": "x",
        "formula": "Total Revenue / Total Assets",
        "ciq_inputs": "total_revenue, total_assets",
    },
    "inventory_turnover": {
        "label": "Inventory Turnover",
        "category": "Efficiency",
        "unit": "x",
        "formula": "COGS / Inventory",
        "ciq_inputs": "cost_of_goods_sold, inventory",
    },
    "receivable_days": {
        "label": "Receivable Days",
        "category": "Efficiency",
        "unit": "days",
        "formula": "(Accounts Receivable / Total Revenue) × 365",
        "ciq_inputs": "accounts_receivable, total_revenue",
    },
    "payable_days": {
        "label": "Payable Days",
        "category": "Efficiency",
        "unit": "days",
        "formula": "(Accounts Payable / COGS) × 365",
        "ciq_inputs": "accounts_payable, cost_of_goods_sold",
    },
    "inventory_days": {
        "label": "Inventory Days",
        "category": "Efficiency",
        "unit": "days",
        "formula": "(Inventory / COGS) × 365",
        "ciq_inputs": "inventory, cost_of_goods_sold",
    },
    "fcf_margin": {
        "label": "FCF Margin",
        "category": "Cash Flow",
        "unit": "percent",
        "formula": "Free Cash Flow / Total Revenue  (FCF ≈ CFO − |Capex|)",
        "ciq_inputs": "cash_from_ops, capital_expenditure, total_revenue",
    },
    "capex_to_sales": {
        "label": "Capex / Sales",
        "category": "Cash Flow",
        "unit": "percent",
        "formula": "|Capital Expenditure| / Total Revenue",
        "ciq_inputs": "capital_expenditure, total_revenue",
    },
    "cfo_to_ebitda": {
        "label": "CFO / EBITDA",
        "category": "Cash Flow",
        "unit": "x",
        "formula": "Cash from Operations / EBITDA",
        "ciq_inputs": "cash_from_ops, ebitda",
    },
    "cfo_to_ni": {
        "label": "CFO / Net Income",
        "category": "Cash Flow",
        "unit": "x",
        "formula": "Cash from Operations / Net Income  (cash conversion)",
        "ciq_inputs": "cash_from_ops, net_income",
    },
}


def formulas_markdown(keys: list[str] | None = None) -> list[str]:
    """Render formula appendix for the analysis pack."""
    lines = [
        "## Formula definitions (transparent)",
        "",
        "_Computed from Capital IQ line items in this session. No third-party data feed._",
        "",
        "| Metric | Formula | CIQ inputs |",
        "|---|---|---|",
    ]
    items = FORMULA_CATALOG.items()
    if keys:
        wanted = set(keys)
        items = [(k, v) for k, v in FORMULA_CATALOG.items() if k in wanted]
    for _key, meta in items:
        lines.append(
            f"| {meta['label']} | `{meta['formula']}` | {meta.get('ciq_inputs', '—')} |"
        )
    lines.append("")
    return lines


def formula_lookup(name: str) -> dict[str, str] | None:
    return FORMULA_CATALOG.get(name)


def catalog_as_list() -> list[dict[str, Any]]:
    return [{"name": k, **v} for k, v in FORMULA_CATALOG.items()]
