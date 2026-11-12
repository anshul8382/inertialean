"""Deterministic multi-year metric series from Capital IQ line items (no LLM)."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median
from typing import Any

from iis.models.artifacts import FinancialLineItem
from iis.models.enums import PeriodType
from iis.parsers.capital_iq.financials import query_financials


METRIC_CANONICAL: dict[str, str] = {
    "capex": "capital_expenditure",
    "capital expenditure": "capital_expenditure",
    "capital_expenditure": "capital_expenditure",
    "total_capital_expenditure": "capital_expenditure",
    "free cash flow": "free_cash_flow",
    "fcf": "free_cash_flow",
    "free_cash_flow": "free_cash_flow",
    "ebitda": "ebitda",
    "revenue": "total_revenue",
    "total revenue": "total_revenue",
    "total_revenue": "total_revenue",
    "net income": "net_income",
    "debt": "total_debt",
    "total debt": "total_debt",
    "cash": "cash_and_equivalents",
}


@dataclass
class SeriesPoint:
    period_end: str
    period_label: str
    value_mn: float
    is_estimate: bool = False


def _dedupe_by_period(items: list[FinancialLineItem]) -> list[SeriesPoint]:
    by_pe: dict[str, FinancialLineItem] = {}
    for item in items:
        if item.value is None or not item.period_end:
            continue
        pe = item.period_end
        existing = by_pe.get(pe)
        if existing is None:
            by_pe[pe] = item
            continue
        # Prefer income/cash-flow statement over key stats when available
        rank = {"cash_flow": 0, "income_statement": 0, "balance_sheet": 1, "key_stats": 2}
        if rank.get(item.statement, 9) <= rank.get(existing.statement, 9):
            by_pe[pe] = item
    out: list[SeriesPoint] = []
    for pe in sorted(by_pe.keys()):
        it = by_pe[pe]
        is_est = it.period_type == PeriodType.ESTIMATE or "E" in (it.period_label or "")
        out.append(
            SeriesPoint(
                period_end=pe,
                period_label=it.period_label or pe,
                value_mn=float(it.value),
                is_estimate=is_est,
            )
        )
    return out


def _is_actualish(item: FinancialLineItem) -> bool:
    """CIQ often leaves period_type as UNKNOWN for historical FY columns."""
    label = item.period_label or ""
    if "E" in label and "Restated" not in label:
        # e.g. "FY 2027E" — treat as estimate
        if any(tok in label for tok in ("E ", "E)", "E,", "2026E", "2027E", "2028E", "2029E", "2030E")):
            return False
        if label.rstrip().endswith("E"):
            return False
    if item.period_type == PeriodType.ESTIMATE:
        return False
    if item.period_type in (PeriodType.ACTUAL, PeriodType.UNKNOWN, PeriodType.PRESS_RELEASE):
        return True
    return item.period_type == PeriodType.ACTUAL


def get_metric_series(
    items: list[FinancialLineItem],
    metric: str,
    *,
    actuals_only: bool = True,
    max_points: int = 10,
) -> list[SeriesPoint]:
    canonical = METRIC_CANONICAL.get(metric.lower().strip(), metric.lower().replace(" ", "_"))
    rows = query_financials(items, line_item=canonical)
    if not rows:
        # Prefer total_* variants (e.g. total_capital_expenditure)
        rows = query_financials(items, line_item=f"total_{canonical}")
    if not rows:
        rows = query_financials(items, line_item_contains=canonical.replace("_", " "))
    if actuals_only:
        rows = [r for r in rows if _is_actualish(r)]
    series = _dedupe_by_period(rows)
    if actuals_only:
        series = [p for p in series if not p.is_estimate]
    return series[-max_points:]


def get_revenue_aligned(items: list[FinancialLineItem], period_ends: list[str]) -> dict[str, float]:
    rev = {p.period_end: p.value_mn for p in get_metric_series(items, "total_revenue", max_points=20)}
    return {pe: rev[pe] for pe in period_ends if pe in rev}


def build_capex_analysis(items: list[FinancialLineItem], *, recent_years: int = 3) -> dict[str, Any] | None:
    """Capex history, intensity vs revenue, recent vs longer-term average."""
    capex = get_metric_series(items, "capital_expenditure", max_points=12)
    if not capex:
        # Some CIQ exports use Capex as signed outflow (negative)
        return None

    # Capex often stored negative (cash outflow) — report spend as positive magnitude
    for p in capex:
        p.value_mn = abs(p.value_mn)

    revs = get_revenue_aligned(items, [p.period_end for p in capex])
    rows = []
    intensities: list[float] = []
    for p in capex:
        rev = revs.get(p.period_end)
        pct = (p.value_mn / rev) if rev and rev > 0 else None
        if pct is not None:
            intensities.append(pct)
        rows.append(
            {
                "period": p.period_label,
                "period_end": p.period_end,
                "capex_mn": round(p.value_mn, 1),
                "revenue_mn": round(rev, 1) if rev is not None else None,
                "capex_pct_revenue": round(pct, 4) if pct is not None else None,
            }
        )

    recent = rows[-recent_years:] if len(rows) >= recent_years else rows
    longer = rows
    recent_vals = [r["capex_mn"] for r in recent]
    longer_vals = [r["capex_mn"] for r in longer]
    recent_int = [r["capex_pct_revenue"] for r in recent if r["capex_pct_revenue"] is not None]
    longer_int = [r["capex_pct_revenue"] for r in longer if r["capex_pct_revenue"] is not None]

    return {
        "type": "capex_analysis",
        "title": "Capital expenditure (Capital IQ)",
        "rows": rows,
        "recent_years": recent_years,
        "recent_avg_mn": round(mean(recent_vals), 1) if recent_vals else None,
        "long_term_avg_mn": round(mean(longer_vals), 1) if longer_vals else None,
        "recent_avg_pct_sales": round(mean(recent_int), 4) if recent_int else None,
        "long_term_avg_pct_sales": round(mean(longer_int), 4) if longer_int else None,
        "long_term_median_pct_sales": round(median(longer_int), 4) if longer_int else None,
    }


def format_capex_analysis(payload: dict[str, Any]) -> str:
    lines = [f"**{payload.get('title', 'Capex')}**", ""]
    lines.append("| Period | Capex (₹ mn) | Revenue (₹ mn) | Capex / Sales |")
    lines.append("|---|---:|---:|---:|")
    for r in payload.get("rows") or []:
        pct = f"{r['capex_pct_revenue']:.1%}" if r.get("capex_pct_revenue") is not None else "—"
        rev = f"{r['revenue_mn']:,.0f}" if r.get("revenue_mn") is not None else "—"
        lines.append(f"| {r['period'][:28]} | {r['capex_mn']:,.0f} | {rev} | {pct} |")

    n = payload.get("recent_years") or 3
    if payload.get("recent_avg_mn") is not None:
        lines.append("")
        lines.append(
            f"**Last {n} years avg capex:** ₹{payload['recent_avg_mn']:,.0f} mn"
            + (
                f" ({payload['recent_avg_pct_sales']:.1%} of sales)"
                if payload.get("recent_avg_pct_sales") is not None
                else ""
            )
        )
    if payload.get("long_term_avg_mn") is not None:
        lines.append(
            f"**Longer history avg capex:** ₹{payload['long_term_avg_mn']:,.0f} mn"
            + (
                f" ({payload['long_term_avg_pct_sales']:.1%} of sales)"
                if payload.get("long_term_avg_pct_sales") is not None
                else ""
            )
        )
    if (
        payload.get("recent_avg_pct_sales") is not None
        and payload.get("long_term_avg_pct_sales") is not None
    ):
        delta = payload["recent_avg_pct_sales"] - payload["long_term_avg_pct_sales"]
        direction = "above" if delta > 0.002 else ("below" if delta < -0.002 else "in line with")
        lines.append(
            f"**vs long-term intensity:** recent capex/sales is **{direction}** "
            f"history (Δ {delta:+.1%} pts)."
        )
    lines.append("")
    lines.append("_Source: Capital IQ Financials cash-flow / capex lines (deterministic, no LLM)._")
    return "\n".join(lines)
