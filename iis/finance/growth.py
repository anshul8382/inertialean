"""Revenue growth and CAGR from Capital IQ financial line items."""
from __future__ import annotations

from dataclasses import dataclass

from iis.models.artifacts import FinancialLineItem
from iis.models.enums import PeriodType
from iis.parsers.capital_iq.financials import query_financials

MIN_REVENUE_MN = 50_000  # filter out ratio rows mis-tagged as revenue


@dataclass
class RevenuePoint:
    period_end: str
    period_label: str
    value_mn: float
    period_type: str
    is_estimate: bool
    yoy_growth: float | None = None


def get_revenue_series(items: list[FinancialLineItem]) -> list[RevenuePoint]:
    """Deduplicated revenue by fiscal year-end, preferring income statement rows."""
    candidates = [
        i
        for i in items
        if i.line_item == "total_revenue"
        and i.value is not None
        and i.value >= MIN_REVENUE_MN
        and i.period_end
    ]

    by_period: dict[str, FinancialLineItem] = {}
    statement_rank = {"income_statement": 0, "key_stats": 1}

    for item in candidates:
        pe = item.period_end
        is_est = "E" in (item.period_label or "") or item.period_type == PeriodType.ESTIMATE
        existing = by_period.get(pe)
        if existing is None:
            by_period[pe] = item
            continue
        existing_est = "E" in (existing.period_label or "")
        rank_new = statement_rank.get(item.statement, 9)
        rank_old = statement_rank.get(existing.statement, 9)
        if rank_new < rank_old or (rank_new == rank_old and item.value > existing.value):
            by_period[pe] = item
        elif existing_est and not is_est:
            by_period[pe] = item

    series: list[RevenuePoint] = []
    for pe in sorted(by_period.keys()):
        item = by_period[pe]
        is_est = "E" in (item.period_label or "") or item.period_type == PeriodType.ESTIMATE
        series.append(
            RevenuePoint(
                period_end=pe,
                period_label=item.period_label,
                value_mn=float(item.value),
                period_type=item.period_type.value,
                is_estimate=is_est,
            )
        )

    for i in range(1, len(series)):
        prev, curr = series[i - 1], series[i]
        if prev.value_mn > 0:
            curr.yoy_growth = curr.value_mn / prev.value_mn - 1

    return series


def get_reported_revenue_yoy(items: list[FinancialLineItem]) -> list[tuple[str, float]]:
    """Capital IQ Key Stats 'Growth Over Prior Year' immediately below Total Revenue."""
    rev_ref = next(
        (
            i
            for i in items
            if i.line_item == "total_revenue"
            and i.statement == "key_stats"
            and i.value is not None
            and i.value >= MIN_REVENUE_MN
            and i.source.row
        ),
        None,
    )
    if not rev_ref:
        return []

    growth_row = rev_ref.source.row + 1
    growth_items = [
        i
        for i in items
        if i.source.row == growth_row
        and "growth over prior year" in i.line_item_raw.lower()
        and i.value is not None
        and i.period_end
    ]
    growth_items.sort(key=lambda x: x.period_end or "")
    return [(g.period_end, float(g.value)) for g in growth_items]


def compute_cagr_from_series(series: list[RevenuePoint], years: int, *, actuals_only: bool = True) -> float | None:
    """CAGR over N fiscal years using first and last point in window."""
    points = [p for p in series if not p.is_estimate] if actuals_only else series
    # LTM / interim points sit off the fiscal-year grid and distort the year count.
    points = [p for p in points if "ltm" not in (p.period_label or "").lower() and p.period_type != "press_release"]
    if len(points) < 2:
        return None

    end = points[-1]
    start_idx = max(0, len(points) - years - 1)
    start = points[start_idx]
    if start.period_end == end.period_end:
        return None

    start_year = int(start.period_end[:4])
    end_year = int(end.period_end[:4])
    n = end_year - start_year
    if n <= 0 or start.value_mn <= 0:
        return None
    return (end.value_mn / start.value_mn) ** (1 / n) - 1


def compute_revenue_cagr(items: list[FinancialLineItem], years: int = 3) -> float | None:
    series = get_revenue_series(items)
    return compute_cagr_from_series(series, years, actuals_only=True)


def build_growth_summary(items: list[FinancialLineItem]) -> dict:
    series = get_revenue_series(items)
    actuals = [p for p in series if not p.is_estimate]
    estimates = [p for p in series if p.is_estimate]
    reported_yoy = get_reported_revenue_yoy(items)

    return {
        "historical": actuals,
        "estimates": estimates,
        "cagr_3y": compute_cagr_from_series(series, 3),
        "cagr_5y": compute_cagr_from_series(series, 5),
        "reported_yoy": reported_yoy,
        "forward_yoy": [(p.period_end, p.yoy_growth) for p in estimates if p.yoy_growth is not None],
    }
