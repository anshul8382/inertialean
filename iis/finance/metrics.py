from __future__ import annotations

from iis.finance.dcf_sanity import normalize_margin
from iis.finance.growth import compute_revenue_cagr
from iis.finance.ratios import build_period_metrics, compute_period_ratios, get_latest_ratio_summary
from iis.models.artifacts import FinancialLineItem
from iis.models.enums import PeriodType
from iis.parsers.capital_iq.financials import get_latest_actual, query_financials


def _latest_with_value(items: list[FinancialLineItem], *needles: str) -> FinancialLineItem | None:
    matches: list[FinancialLineItem] = []
    for needle in needles:
        matches.extend(query_financials(items, line_item_contains=needle))
    matches = [m for m in matches if m.value is not None]
    if not matches:
        return None
    matches.sort(key=lambda x: (x.period_end or "", x.period_label), reverse=True)
    return matches[0]


def get_shares_outstanding_mn(items: list[FinancialLineItem]) -> float | None:
    matches = [
        i
        for i in items
        if ("shares out" in i.line_item_raw.lower() or i.line_item == "shares_outstanding_mn")
        and i.value is not None
        and i.value > 0
    ]
    actuals = [m for m in matches if m.period_type == PeriodType.ACTUAL]
    pool = actuals or matches
    pool.sort(key=lambda x: x.period_end or "", reverse=True)
    return float(pool[0].value) if pool else None


def get_net_debt_mn(items: list[FinancialLineItem]) -> float | None:
    from iis.finance.dcf_sanity import get_line_item_for_period, pick_latest_reporting_period

    period = pick_latest_reporting_period(items)
    if period:
        aligned = get_line_item_for_period(items, "net_debt", period)
        if aligned and aligned.value is not None and aligned.statement == "balance_sheet":
            return float(aligned.value)

    candidates = [
        i
        for i in items
        if i.line_item == "net_debt" or i.line_item_raw.strip().lower() == "net debt"
    ]
    candidates = [c for c in candidates if c.value is not None and c.statement == "balance_sheet"]
    if candidates:
        from iis.finance.dcf_sanity import is_estimate_period_label

        actuals = [c for c in candidates if not is_estimate_period_label(c.period_label)]
        pool = actuals or candidates
        pool.sort(key=lambda x: x.period_end or "", reverse=True)
        return float(pool[0].value)

    total_debt = _latest_with_value(items, "total_debt", "total debt")
    cash = _latest_with_value(items, "cash_and_equivalents", "cash and equivalents")
    if total_debt and cash:
        return float(total_debt.value) - float(cash.value)
    return None


def compute_historical_fcf_to_ebitda(
    items: list[FinancialLineItem],
    *,
    years: int = 3,
) -> tuple[float | None, str]:
    """
    Average FCF ÷ EBITDA over recent fiscal years with material revenue.
    Returns (conversion ratio as decimal, rationale text).
    """
    from iis.finance.dcf_sanity import clamp_dcf_fcf_conversion

    periods = build_period_metrics(items)
    conversions: list[tuple[str, float]] = []
    for period in sorted(periods.keys(), reverse=True):
        pm = periods[period]
        ebitda = pm.get("ebitda")
        ratios = compute_period_ratios(pm)
        fcf = ratios.get("fcf_mn")
        if ebitda is None or ebitda <= 0 or fcf is None:
            continue
        conv = fcf / ebitda
        if conv <= 0 or conv > 1.2:
            continue
        conversions.append((period, conv))
        if len(conversions) >= years:
            break

    if not conversions:
        return None, "No reliable historical FCF/EBITDA — using 65% default"

    avg = sum(c for _, c in conversions) / len(conversions)
    clamped = clamp_dcf_fcf_conversion(avg)
    detail = ", ".join(f"FY{p[:4]} {c:.0%}" for p, c in conversions)
    return clamped, f"Historical {len(conversions)}Y avg FCF/EBITDA ({detail})"


def get_key_metrics_summary(items: list[FinancialLineItem]) -> dict[str, float | None]:
    revenue = get_latest_actual(items, "total_revenue")
    ebitda = get_latest_actual(items, "ebitda")
    if revenue and ebitda and ebitda.period_end != revenue.period_end:
        same = next((i for i in items if i.line_item == "ebitda" and i.period_end == revenue.period_end), None)
        if same:
            ebitda = same
    net_income = get_latest_actual(items, "net_income")

    margin = None
    if revenue and ebitda and revenue.value and revenue.period_end == ebitda.period_end:
        margin = ebitda.value / revenue.value if revenue.value else None
    margin = normalize_margin(margin)

    ratios = get_latest_ratio_summary(items)
    ratio_margin = normalize_margin(ratios.get("ebitda_margin"))
    if margin is None and ratio_margin is not None:
        margin = ratio_margin

    return {
        "revenue_mn": revenue.value if revenue else None,
        "ebitda_mn": ebitda.value if ebitda else None,
        "net_income_mn": net_income.value if net_income else None,
        "ebitda_margin": margin if margin is not None else ratio_margin,
        "gross_margin": ratios.get("gross_margin"),
        "net_margin": ratios.get("net_margin"),
        "roe": ratios.get("roe"),
        "roa": ratios.get("roa"),
        "roce": ratios.get("roce"),
        "current_ratio": ratios.get("current_ratio"),
        "net_debt_to_ebitda": ratios.get("net_debt_to_ebitda"),
        "interest_coverage": ratios.get("interest_coverage"),
        "shares_outstanding_mn": get_shares_outstanding_mn(items),
        "net_debt_mn": get_net_debt_mn(items),
        "revenue_cagr_3y": compute_revenue_cagr(items, years=3),
        "fcf_to_ebitda": compute_historical_fcf_to_ebitda(items)[0],
    }
