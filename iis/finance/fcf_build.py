"""Unlevered FCF build-up for DCF: EBITDA → tax → capex → Δworking capital.

Low spot FCF during high growth often reflects inventory / WC build (common in
jewellery, retail). The model projects WC investment from revenue growth and
uses a lower WC drag once growth moderates toward terminal rate.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

from iis.models.artifacts import FinancialLineItem
from iis.models.enums import PeriodType
from iis.finance.dcf_sanity import is_estimate_period_label
from iis.finance.ratios import build_period_metrics, compute_period_ratios

_EXCLUDED_WC_PERIOD_TYPES = frozenset({PeriodType.ESTIMATE, PeriodType.PRESS_RELEASE})


@dataclass
class FCFDrivers:
    tax_rate: float
    ebit_margin: float
    da_pct_revenue: float
    capex_pct_revenue: float
    nwc_pct_revenue: float
    wc_investment_rate: float
    rationale: str
    capex_hist_median: float | None = None


def _safe_div(n: float | None, d: float | None) -> float | None:
    if n is None or d is None or d == 0:
        return None
    return n / d


def _is_reported_growth_period(items: list[FinancialLineItem], period: str) -> bool:
    """Exclude estimates / press-release columns from historical WC investment rates."""
    rev_rows = [
        i
        for i in items
        if i.line_item == "total_revenue"
        and i.statement in ("income_statement", "key_stats")
        and (i.period_end == period or i.period_label == period)
    ]
    if not rev_rows:
        return True
    rev_rows.sort(key=lambda x: 0 if x.period_type == PeriodType.ACTUAL else 1)
    rev = rev_rows[0]
    if rev.period_type in _EXCLUDED_WC_PERIOD_TYPES:
        return False
    if is_estimate_period_label(rev.period_label):
        return False
    return True


def _trimmed_wc_investment_rate(rates: list[float], nwc_pct: float) -> float | None:
    """Drop spike years (inventory build) before taking median ΔNWC/ΔRev."""
    if not rates:
        return None
    cap = min(1.0, max(0.55, nwc_pct * 1.25))
    filtered = [r for r in rates if 0 < r <= cap]
    if len(filtered) < 2 and len(rates) >= 3:
        ordered = sorted(rates)
        filtered = ordered[1:-1]
    if not filtered:
        filtered = rates
    return median(filtered)


def extract_fcf_drivers(items: list[FinancialLineItem]) -> FCFDrivers:
    """Derive DCF drivers from reported financials (same-period, material revenue)."""
    from iis.finance.dcf_sanity import get_line_item_for_period, pick_latest_reporting_period
    from iis.parsers.capital_iq.financials import get_latest_actual

    period = pick_latest_reporting_period(items)
    periods = build_period_metrics(items)
    sorted_periods = sorted(periods.keys())

    # WC investment rate: ΔNWC / ΔRevenue in growth years
    wc_rates: list[float] = []
    prev_rev: float | None = None
    for p in sorted_periods:
        if not _is_reported_growth_period(items, p):
            continue
        pm = periods[p]
        rev = pm.get("total_revenue")
        if not rev or rev < 1_000:
            prev_rev = rev
            continue
        if prev_rev and rev > prev_rev:
            d_rev = rev - prev_rev
            d_nwc = next(
                (i.value for i in items if i.line_item == "change_in_net_working_capital" and i.period_end == p),
                None,
            )
            if d_nwc is not None and d_rev > 0:
                rate = d_nwc / d_rev
                if 0 < rate <= 1.5:
                    wc_rates.append(rate)
        prev_rev = rev

    latest_pm = periods.get(period or "", periods[sorted_periods[-1]] if sorted_periods else None)
    if latest_pm is None and sorted_periods:
        latest_pm = periods[sorted_periods[-1]]

    rev = latest_pm.get("total_revenue") if latest_pm else None
    inv = (latest_pm or {}).get("inventory") or 0
    ar = (latest_pm or {}).get("accounts_receivable") or 0
    ap = (latest_pm or {}).get("accounts_payable") or 0
    nwc_pct = _safe_div(inv + ar - ap, rev) or 0.35

    raw_median = median(wc_rates) if wc_rates else None
    trimmed = _trimmed_wc_investment_rate(wc_rates, nwc_pct) if wc_rates else None
    wc_investment_rate = trimmed if trimmed is not None else nwc_pct
    wc_investment_rate = min(max(wc_investment_rate, 0.05), 1.0)

    # Per-period ratios for capex, margins
    capex_pcts: list[float] = []
    ebit_margins: list[float] = []
    da_pcts: list[float] = []
    tax_rates: list[float] = []

    for p in sorted(periods.keys(), reverse=True)[:6]:
        pm = periods[p]
        r = pm.get("total_revenue")
        if not r or r < 1_000:
            continue
        ratios = compute_period_ratios(pm)
        capex = pm.get("capital_expenditure")
        if capex is not None:
            capex_pcts.append(abs(capex) / r)
        if ratios.get("ebit_margin") is not None:
            ebit_margins.append(ratios["ebit_margin"])
        da = pm.get("depreciation_amortization", "depreciation_amort_total", "depreciation_amort")
        if da is not None and da > 0:
            da_pcts.append(da / r)
        ebit = pm.get("ebit")
        ebt = pm.get("ebt_incl_unusual_items", "ebt_excl_unusual_items")
        tax = pm.get("income_tax_expense", "income_tax", "provision_for_income_taxes")
        tax_base = ebt if ebt and ebt > 0 else ebit
        if tax_base and tax_base > 0 and tax is not None:
            tax_rates.append(abs(tax) / tax_base)

    revenue = get_latest_actual(items, "total_revenue")
    ebitda = get_latest_actual(items, "ebitda")
    ebit_item = get_line_item_for_period(items, "ebit", period) if period else get_latest_actual(items, "ebit")

    ebit_margin = median(ebit_margins) if ebit_margins else None
    if ebit_margin is None and ebit_item and revenue and revenue.value:
        ebit_margin = ebit_item.value / revenue.value
    if ebit_margin is None and ebitda and revenue and revenue.value:
        ebit_margin = (ebitda.value / revenue.value) * 0.95
    ebit_margin = min(max(ebit_margin or 0.08, 0.03), 0.40)

    da_pct = median(da_pcts) if da_pcts else 0.011
    da_pct = min(max(da_pct, 0.003), 0.15)

    capex_hist = median(capex_pcts) if capex_pcts else None
    # Steady-state default: historical intensity capped at 8% (expansion phases inflate it),
    # but never below D&A — a growing business cannot reinvest less than it depreciates.
    capex_pct = min(max(capex_hist if capex_hist is not None else 0.015, 0.003), 0.08)
    capex_pct = max(capex_pct, da_pct)

    tax_rate = median(tax_rates) if tax_rates else 0.25
    tax_rate = min(max(tax_rate, 0.15), 0.35)

    detail_parts = [
        f"NWC {nwc_pct:.0%} of revenue",
        f"WC investment {wc_investment_rate:.0%} of incremental revenue (growth years)",
        f"capex {capex_pct:.1%} of sales"
        + (f" (historical median {capex_hist:.1%}; D&A {da_pct:.1%})" if capex_hist is not None else ""),
    ]
    if wc_rates:
        detail_parts.append(f"historical ΔNWC/ΔRev: {', '.join(f'{x:.0%}' for x in wc_rates[-4:])}")
        if raw_median is not None and trimmed is not None and abs(raw_median - trimmed) > 0.05:
            detail_parts.append(
                f"WC investment trimmed {raw_median:.0%} → {trimmed:.0%} (excluded spike years > {min(1.0, max(0.55, nwc_pct * 1.25)):.0%})"
            )

    return FCFDrivers(
        tax_rate=round(tax_rate, 4),
        ebit_margin=round(ebit_margin, 4),
        da_pct_revenue=round(da_pct, 4),
        capex_pct_revenue=round(capex_pct, 4),
        nwc_pct_revenue=round(nwc_pct, 4),
        wc_investment_rate=round(wc_investment_rate, 4),
        rationale="FCF build-up from financials — " + "; ".join(detail_parts),
        capex_hist_median=round(capex_hist, 4) if capex_hist is not None else None,
    )


def _fade_growth(explicit_growth: float, terminal_growth: float, year: int, years: int) -> float:
    """Fade explicit-period revenue growth toward terminal by final forecast year."""
    if years <= 1:
        return explicit_growth
    t = (year - 1) / max(years - 1, 1)
    return explicit_growth + t * (terminal_growth - explicit_growth)


def project_fcf_years(
    base_revenue: float,
    *,
    revenue_growth_y1_5: float,
    terminal_growth: float,
    ebitda_margin: float,
    drivers: FCFDrivers,
    years: int = 5,
    fade_growth: bool = True,
) -> list[dict[str, float]]:
    """Project unlevered FCF with explicit WC and capex each year."""
    rows: list[dict[str, float]] = []
    rev = base_revenue
    for yr in range(1, years + 1):
        g = (
            _fade_growth(revenue_growth_y1_5, terminal_growth, yr, years)
            if fade_growth
            else revenue_growth_y1_5
        )
        prev_rev = rev
        rev = rev * (1 + g)
        delta_rev = rev - prev_rev

        ebitda = rev * ebitda_margin
        ebit = rev * drivers.ebit_margin
        nopat = ebit * (1 - drivers.tax_rate)
        da = rev * drivers.da_pct_revenue
        capex = rev * drivers.capex_pct_revenue

        # High growth → WC build; fades as g approaches terminal
        if g > terminal_growth + 0.01:
            delta_nwc = delta_rev * drivers.wc_investment_rate
        else:
            delta_nwc = delta_rev * drivers.nwc_pct_revenue

        ufcf = nopat + da - capex - delta_nwc
        rows.append(
            {
                "year": yr,
                "revenue_mn": round(rev, 1),
                "growth": round(g, 4),
                "ebitda_mn": round(ebitda, 1),
                "nopat_mn": round(nopat, 1),
                "capex_mn": round(capex, 1),
                "delta_nwc_mn": round(delta_nwc, 1),
                "ufcf_mn": round(ufcf, 1),
            }
        )
    return rows


def dcf_equity_from_fcf_build(
    base_revenue: float,
    *,
    discount_rate: float,
    terminal_growth: float,
    revenue_growth_y1_5: float,
    ebitda_margin: float,
    drivers: FCFDrivers,
    shares_mn: float,
    net_debt_mn: float = 0,
    years: int = 5,
) -> tuple[float | None, list[dict[str, float]]]:
    """DCF from explicit FCF build-up → equity value per share."""
    if shares_mn <= 0 or base_revenue <= 0 or discount_rate <= terminal_growth:
        return None, []

    stream = project_fcf_years(
        base_revenue,
        revenue_growth_y1_5=revenue_growth_y1_5,
        terminal_growth=terminal_growth,
        ebitda_margin=ebitda_margin,
        drivers=drivers,
        years=years,
    )
    if not stream:
        return None, stream

    fcfs = [r["ufcf_mn"] for r in stream]
    terminal = fcfs[-1] * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv = sum(f / (1 + discount_rate) ** (i + 1) for i, f in enumerate(fcfs))
    ev = pv + terminal / (1 + discount_rate) ** years
    per_share = round((ev - net_debt_mn) / shares_mn, 2)
    return per_share, stream


def steady_state_fcf_pct_of_ebitda(
    terminal_growth: float,
    ebitda_margin: float,
    drivers: FCFDrivers,
) -> float:
    """Illustrative FCF/EBITDA once growth has moderated to terminal rate."""
    rev = 100_000.0
    ebit = rev * drivers.ebit_margin
    nopat = ebit * (1 - drivers.tax_rate)
    da = rev * drivers.da_pct_revenue
    capex = rev * drivers.capex_pct_revenue
    delta_nwc = rev * terminal_growth * drivers.nwc_pct_revenue
    ufcf = nopat + da - capex - delta_nwc
    ebitda = rev * ebitda_margin
    return ufcf / ebitda if ebitda else 0.0


def drivers_to_assumption_dict(drivers: FCFDrivers) -> dict[str, float]:
    return {
        "tax_rate": drivers.tax_rate,
        "ebit_margin": drivers.ebit_margin,
        "capex_pct_revenue": drivers.capex_pct_revenue,
        "nwc_pct_revenue": drivers.nwc_pct_revenue,
        "wc_investment_rate": drivers.wc_investment_rate,
        "da_pct_revenue": drivers.da_pct_revenue,
    }
