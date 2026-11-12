"""Multi-year (up to 10) financial snapshot from CIQ actuals on file."""
from __future__ import annotations

from typing import Any

from iis.finance.ratios import build_period_metrics, compute_period_ratios
from iis.models.artifacts import FinancialLineItem

# Rows shown in the 10Y snapshot (label, period-metric key, kind)
_ROWS: list[tuple[str, str, str]] = [
    ("Revenue", "total_revenue", "mn"),
    ("EBITDA", "ebitda", "mn"),
    ("EBIT", "ebit", "mn"),
    ("PAT", "net_income", "mn"),
    ("CFO", "cash_from_ops", "mn"),
    ("Capex", "capital_expenditure", "mn"),
    ("Gross margin", "gross_margin", "pct"),
    ("EBITDA margin", "ebitda_margin", "pct"),
    ("ROE", "roe", "pct"),
    ("ROCE", "roce", "pct"),
    ("Net debt / EBITDA", "net_debt_to_ebitda", "x"),
]


def _fy_label(period: str) -> str:
    """'2026-03-31' → 'FY26'; keep short so 10 columns fit."""
    pe = (period or "")[:10]
    if len(pe) >= 4 and pe[:4].isdigit():
        y = int(pe[:4])
        # Indian Mar YE: period_end year is the FY label (Mar-2026 = FY26)
        return f"FY{str(y)[-2:]}"
    return (period or "?")[:8]


def build_ten_year_snapshot(
    items: list[FinancialLineItem],
    *,
    years: int = 10,
) -> dict[str, Any]:
    """Up to ``years`` fiscal actuals as a metric × year table.

    Returns empty metrics when nothing is on file. ``coverage`` explains how many
    years were found vs requested (CIQ exports are often 5Y unless re-downloaded).
    """
    periods = build_period_metrics(items)
    ordered = sorted(periods.keys())  # oldest → newest
    # Prefer annual YE (Mar/Dec); drop odd mid-year stubs if annuals exist
    annual = [p for p in ordered if p[5:7] in ("03", "12", "06", "09") or len(p) < 7]
    use = annual if annual else ordered
    use = use[-years:]
    if not use:
        return {
            "years_requested": years,
            "years_on_file": 0,
            "period_ends": [],
            "period_labels": [],
            "rows": [],
            "coverage": "No annual actuals on file yet — upload Capital IQ Financials.",
        }

    labels = [_fy_label(p) for p in use]
    ratio_cache = {p: compute_period_ratios(periods[p]) for p in use}
    rows: list[dict[str, Any]] = []
    for label, key, kind in _ROWS:
        values: list[float | None] = []
        for p in use:
            if kind == "mn":
                values.append(periods[p].get(key))
            else:
                values.append(ratio_cache[p].get(key))
        if all(v is None for v in values):
            continue
        rows.append({"label": label, "key": key, "kind": kind, "values": values})

    n = len(use)
    if n >= years:
        coverage = f"{n} fiscal years on file (full {years}Y window)."
    else:
        coverage = (
            f"{n} of {years} fiscal years on file "
            f"({labels[0]}–{labels[-1]}). "
            f"Re-download Capital IQ Financials with a longer history for a full {years}Y snapshot."
        )

    return {
        "years_requested": years,
        "years_on_file": n,
        "period_ends": use,
        "period_labels": labels,
        "rows": rows,
        "coverage": coverage,
    }


def snapshot_markdown(snap: dict[str, Any], *, title: str | None = None) -> list[str]:
    """Markdown table for reports."""
    if not snap.get("rows"):
        return []
    labels = snap["period_labels"]
    lines = [
        title or f"### {snap['years_on_file']}-year financial snapshot (CIQ actuals, ₹ mn)",
        "",
        f"_{snap['coverage']}_",
        "",
        "| Metric | " + " | ".join(labels) + " |",
        "|---|" + "|".join(["---:"] * len(labels)) + "|",
    ]
    for row in snap["rows"]:
        cells = []
        for v in row["values"]:
            if v is None:
                cells.append("—")
            elif row["kind"] == "pct":
                cells.append(f"{v * 100:.1f}%")
            elif row["kind"] == "x":
                cells.append(f"{v:.2f}x")
            else:
                cells.append(f"{v:,.0f}")
        lines.append(f"| {row['label']} | " + " | ".join(cells) + " |")
    lines.append("")
    return lines
