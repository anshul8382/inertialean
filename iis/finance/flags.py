"""Verifiable data flags — plain-language anomalies with cited figures.

No composite scores (Beneish, Piotroski, etc.). Each flag is a fact the analyst
can check in Capital IQ or the annual report.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from iis.finance.ratios import build_period_metrics, compute_period_ratios
from iis.models.artifacts import FinancialLineItem, GuidanceRecord
from iis.models.enums import PeriodType
from iis.models.session import ResearchSession
from iis.parsers.capital_iq.financials import get_latest_actual, query_financials


@dataclass
class DataFlag:
    id: str
    category: str
    severity: str  # review | info
    headline: str
    explanation: str
    figures: list[dict[str, Any]] = field(default_factory=list)
    verify: list[str] = field(default_factory=list)
    source: dict[str, Any] | None = None


def _yoy_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return ((current - previous) / abs(previous)) * 100


def _fmt_mn(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"₹{value:,.0f} mn"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.1f}%"


def _period_pair(sorted_periods: list[str]) -> tuple[str, str] | None:
    if len(sorted_periods) < 2:
        return None
    return sorted_periods[0], sorted_periods[1]


def _get_actual_series(items: list[FinancialLineItem], line_item: str) -> list[tuple[str, float]]:
    rows = [
        i
        for i in query_financials(items, line_item=line_item, period_type=PeriodType.ACTUAL)
        if i.value is not None and i.period_end
    ]
    by_period: dict[str, float] = {}
    for r in rows:
        pe = r.period_end or ""
        if pe not in by_period or r.statement == "income_statement":
            by_period[pe] = float(r.value)
    return sorted(by_period.items(), key=lambda x: x[0], reverse=True)


def _normalize_guidance_metric(metric: str) -> str:
    m = metric.strip()
    for suffix in (
        " Actual/Latest",
        " Actual",
        " Consensus Estimate",
        " Guidance",
    ):
        if m.endswith(suffix):
            return m[: -len(suffix)].strip()
    return m


def pivot_guidance(records: list[GuidanceRecord]) -> dict[tuple[str, str], dict[str, Any]]:
    """Group Guidance sheet rows by base metric and fiscal year."""
    pivoted: dict[tuple[str, str], dict[str, Any]] = {}
    for rec in records:
        base = _normalize_guidance_metric(rec.metric)
        key = (base, rec.fiscal_year)
        bucket = pivoted.setdefault(
            key,
            {
                "metric": base,
                "fiscal_year": rec.fiscal_year,
                "guidance": None,
                "consensus": None,
                "actual": None,
                "source": rec.source.model_dump() if rec.source else None,
            },
        )
        if rec.guidance is not None:
            bucket["guidance"] = rec.guidance
        if rec.consensus is not None:
            bucket["consensus"] = rec.consensus
        if rec.actual is not None:
            bucket["actual"] = rec.actual
        if "guidance" in rec.metric.lower() and rec.guidance is None:
            # Parser may store value in wrong field for odd row labels
            val = rec.guidance or rec.consensus or rec.actual
            if val is not None:
                bucket["guidance"] = val
    return pivoted


def _is_growth_metric(name: str) -> bool:
    lower = name.lower()
    return any(k in lower for k in ("growth", "margin", "rate", "%", "change"))


def _add_flag(flags: list[DataFlag], flag: DataFlag) -> None:
    if not any(f.id == flag.id for f in flags):
        flags.append(flag)


def _cash_and_working_capital_flags(
    items: list[FinancialLineItem],
    *,
    thresholds: Any | None = None,
) -> list[DataFlag]:
    from iis.finance.checklist import ChecklistThresholds

    th = thresholds if isinstance(thresholds, ChecklistThresholds) else ChecklistThresholds()
    flags: list[DataFlag] = []
    periods = build_period_metrics(items)
    sorted_periods = sorted(periods.keys(), reverse=True)
    pair = _period_pair(sorted_periods)
    if not pair:
        return flags

    curr_p, prev_p = pair
    curr_m, prev_m = periods[curr_p], periods[prev_p]
    curr_r = compute_period_ratios(curr_m)
    prev_r = compute_period_ratios(prev_m)

    rev_curr = curr_m.get("total_revenue", "revenue")
    rev_prev = prev_m.get("total_revenue", "revenue")
    rev_yoy = _yoy_pct(rev_curr, rev_prev)

    cfo_curr = curr_m.get("cash_from_ops")
    cfo_prev = prev_m.get("cash_from_ops")
    cfo_yoy = _yoy_pct(cfo_curr, cfo_prev)

    ni_curr = curr_m.get("net_income")
    ni_prev = prev_m.get("net_income")

    if rev_yoy is not None and rev_yoy >= th.rev_growth_min_for_ocf_check and cfo_yoy is not None:
        if cfo_yoy < rev_yoy - th.ocf_lag_vs_rev_pp or cfo_yoy <= 0:
            _add_flag(
                flags,
                DataFlag(
                    id="ocf_lags_revenue",
                    category="cash_flow",
                    severity="review",
                    headline="Operating cash flow is not keeping up with revenue growth",
                    explanation=(
                        f"Revenue grew {_fmt_pct(rev_yoy)} ({prev_p} → {curr_p}) but cash from operations "
                        f"grew only {_fmt_pct(cfo_yoy)}. Cash generation from operations is lagging sales growth — "
                        "check receivables and working capital."
                    ),
                    figures=[
                        {"metric": "Total revenue", "period": curr_p, "value_mn": rev_curr, "yoy_pct": rev_yoy},
                        {"metric": "Cash from operations", "period": curr_p, "value_mn": cfo_curr, "yoy_pct": cfo_yoy},
                    ],
                    verify=[
                        "Capital IQ Financials → Income Statement → Total Revenue",
                        "Capital IQ Financials → Cash Flow → Cash from Ops",
                    ],
                    source={"sheet": "Cash Flow / Income Statement", "periods": [prev_p, curr_p]},
                ),
            )

    if ni_curr is not None and ni_prev is not None and cfo_curr is not None and cfo_prev is not None:
        if ni_curr > ni_prev and cfo_curr < cfo_prev:
            _add_flag(
                flags,
                DataFlag(
                    id="ni_up_ocf_down",
                    category="cash_flow",
                    severity="review",
                    headline="Reported profit rose but operating cash flow fell",
                    explanation=(
                        f"Net income increased from {_fmt_mn(ni_prev)} to {_fmt_mn(ni_curr)} while "
                        f"cash from operations fell from {_fmt_mn(cfo_prev)} to {_fmt_mn(cfo_curr)}. "
                        "Earnings are not converting to cash."
                    ),
                    figures=[
                        {"metric": "Net income", "period": curr_p, "value_mn": ni_curr},
                        {"metric": "Net income", "period": prev_p, "value_mn": ni_prev},
                        {"metric": "Cash from operations", "period": curr_p, "value_mn": cfo_curr},
                        {"metric": "Cash from operations", "period": prev_p, "value_mn": cfo_prev},
                    ],
                    verify=[
                        "Capital IQ Financials → Income Statement → Net Income",
                        "Capital IQ Financials → Cash Flow → Cash from Ops",
                    ],
                ),
            )

    if ni_curr is not None and cfo_curr is not None and ni_curr > 0 and cfo_curr < ni_curr * th.ocf_to_ni_min:
        _add_flag(
            flags,
            DataFlag(
                id="ocf_below_ni",
                category="cash_flow",
                severity="review",
                headline="Cash from operations is well below net income",
                explanation=(
                    f"Cash from operations ({_fmt_mn(cfo_curr)}) is materially below net income "
                    f"({_fmt_mn(ni_curr)}) for {curr_p}. A large share of reported profit did not "
                    "come through as operating cash."
                ),
                figures=[
                    {"metric": "Cash from operations", "period": curr_p, "value_mn": cfo_curr},
                    {"metric": "Net income", "period": curr_p, "value_mn": ni_curr},
                ],
                verify=["Capital IQ Financials → Cash Flow vs Income Statement, same fiscal year"],
            ),
        )

    ebitda_curr = curr_m.get("ebitda")
    fcf_curr = curr_r.get("fcf_mn")
    if ebitda_curr is not None and ebitda_curr > 0 and fcf_curr is not None and fcf_curr < 0:
        _add_flag(
            flags,
            DataFlag(
                id="fcf_negative_ebitda_positive",
                category="cash_flow",
                severity="review",
                headline="EBITDA is positive but free cash flow is negative",
                explanation=(
                    f"EBITDA is {_fmt_mn(ebitda_curr)} but free cash flow is {_fmt_mn(fcf_curr)} after capex. "
                    "Growth or working capital is consuming cash despite operating profitability."
                ),
                figures=[
                    {"metric": "EBITDA", "period": curr_p, "value_mn": ebitda_curr},
                    {"metric": "Free cash flow", "period": curr_p, "value_mn": fcf_curr},
                ],
                verify=["Capital IQ Financials → Cash Flow → Capex and Cash from Ops"],
            ),
        )

    ar_curr = curr_m.get("accounts_receivable")
    ar_prev = prev_m.get("accounts_receivable")
    ar_yoy = _yoy_pct(ar_curr, ar_prev)
    if rev_yoy is not None and ar_yoy is not None and ar_yoy > rev_yoy + th.ar_vs_rev_extra_pp:
        _add_flag(
            flags,
            DataFlag(
                id="receivables_faster_than_revenue",
                category="working_capital",
                severity="review",
                headline="Receivables are growing faster than revenue",
                explanation=(
                    f"Trade receivables grew {_fmt_pct(ar_yoy)} vs revenue {_fmt_pct(rev_yoy)} ({prev_p} → {curr_p}). "
                    "Collection may be slowing or revenue may be booked before cash is received."
                ),
                figures=[
                    {"metric": "Receivables", "period": curr_p, "value_mn": ar_curr, "yoy_pct": ar_yoy},
                    {"metric": "Total revenue", "period": curr_p, "value_mn": rev_curr, "yoy_pct": rev_yoy},
                ],
                verify=[
                    "Capital IQ Financials → Balance Sheet → Accounts Receivable",
                    "Capital IQ Financials → Income Statement → Total Revenue",
                ],
            ),
        )

    rd_curr = curr_r.get("receivable_days")
    rd_prev = prev_r.get("receivable_days")
    if rd_curr is not None and rd_prev is not None and rd_curr - rd_prev > th.receivable_days_increase:
        _add_flag(
            flags,
            DataFlag(
                id="receivable_days_up",
                category="working_capital",
                severity="review",
                headline="Receivable days have increased",
                explanation=(
                    f"Receivable days rose from {rd_prev:.0f} to {rd_curr:.0f} days ({prev_p} → {curr_p}). "
                    "The collection period has lengthened."
                ),
                figures=[
                    {"metric": "Receivable days", "period": curr_p, "value": rd_curr, "unit": "days"},
                    {"metric": "Receivable days", "period": prev_p, "value": rd_prev, "unit": "days"},
                ],
                verify=["Capital IQ Financials → Balance Sheet + Income Statement"],
            ),
        )

    inv_curr = curr_m.get("inventory")
    inv_prev = prev_m.get("inventory")
    inv_yoy = _yoy_pct(inv_curr, inv_prev)
    if rev_yoy is not None and inv_yoy is not None and inv_yoy > rev_yoy + th.inventory_vs_rev_extra_pp:
        _add_flag(
            flags,
            DataFlag(
                id="inventory_faster_than_revenue",
                category="working_capital",
                severity="review",
                headline="Inventory is growing faster than revenue",
                explanation=(
                    f"Inventory grew {_fmt_pct(inv_yoy)} vs revenue {_fmt_pct(rev_yoy)}. "
                    "Possible overstocking or slower sales relative to stock build."
                ),
                figures=[
                    {"metric": "Inventory", "period": curr_p, "value_mn": inv_curr, "yoy_pct": inv_yoy},
                    {"metric": "Total revenue", "period": curr_p, "value_mn": rev_curr, "yoy_pct": rev_yoy},
                ],
                verify=[
                    "Capital IQ Financials → Balance Sheet → Inventory",
                    "Capital IQ Financials → Income Statement → Total Revenue",
                ],
            ),
        )

    gm_curr = curr_r.get("gross_margin")
    gm_prev = prev_r.get("gross_margin")
    if rev_yoy is not None and rev_yoy >= th.rev_growth_min_for_margin_check and gm_curr is not None and gm_prev is not None:
        if (gm_curr - gm_prev) < -th.gross_margin_drop:
            _add_flag(
                flags,
                DataFlag(
                    id="gross_margin_down_revenue_up",
                    category="margins",
                    severity="review",
                    headline="Revenue grew but gross margin fell",
                    explanation=(
                        f"Revenue grew {_fmt_pct(rev_yoy)} but gross margin fell from {gm_prev:.1%} to {gm_curr:.1%}. "
                        "Growth may be discount-driven or mix shifted to lower-margin products."
                    ),
                    figures=[
                        {"metric": "Gross margin", "period": curr_p, "value_pct": gm_curr * 100},
                        {"metric": "Gross margin", "period": prev_p, "value_pct": gm_prev * 100},
                        {"metric": "Revenue YoY", "period": curr_p, "yoy_pct": rev_yoy},
                    ],
                    verify=["Capital IQ Financials → Income Statement"],
                ),
            )

    em_curr = curr_r.get("ebitda_margin")
    em_prev = prev_r.get("ebitda_margin")
    if rev_yoy is not None and rev_yoy >= th.rev_growth_min_for_margin_check and em_curr is not None and em_prev is not None:
        if (em_curr - em_prev) < -th.ebitda_margin_drop:
            _add_flag(
                flags,
                DataFlag(
                    id="ebitda_margin_down_revenue_up",
                    category="margins",
                    severity="review",
                    headline="Revenue grew but EBITDA margin compressed",
                    explanation=(
                        f"Revenue grew {_fmt_pct(rev_yoy)} but EBITDA margin fell from {em_prev:.1%} to {em_curr:.1%}."
                    ),
                    figures=[
                        {"metric": "EBITDA margin", "period": curr_p, "value_pct": em_curr * 100},
                        {"metric": "EBITDA margin", "period": prev_p, "value_pct": em_prev * 100},
                    ],
                    verify=["Capital IQ Financials → Income Statement"],
                ),
            )

    debt_curr = curr_m.get("total_debt")
    debt_prev = prev_m.get("total_debt")
    debt_yoy = _yoy_pct(debt_curr, debt_prev)
    if rev_yoy is not None and debt_yoy is not None and debt_yoy > rev_yoy + th.debt_vs_rev_extra_pp:
        _add_flag(
            flags,
            DataFlag(
                id="debt_faster_than_revenue",
                category="leverage",
                severity="review",
                headline="Debt is rising faster than revenue",
                explanation=(
                    f"Total debt grew {_fmt_pct(debt_yoy)} vs revenue {_fmt_pct(rev_yoy)}. "
                    "Leverage is increasing faster than the business grew."
                ),
                figures=[
                    {"metric": "Total debt", "period": curr_p, "value_mn": debt_curr, "yoy_pct": debt_yoy},
                    {"metric": "Total revenue", "period": curr_p, "value_mn": rev_curr, "yoy_pct": rev_yoy},
                ],
                verify=["Capital IQ Financials → Balance Sheet → Total Debt"],
            ),
        )

    ic_curr = curr_r.get("interest_coverage")
    ic_prev = prev_r.get("interest_coverage")
    if ic_curr is not None and ic_prev is not None and ic_prev > 0 and ic_curr < ic_prev * th.interest_coverage_drop_ratio:
        _add_flag(
            flags,
            DataFlag(
                id="interest_coverage_falling",
                category="leverage",
                severity="review",
                headline="Interest coverage has declined",
                explanation=(
                    f"Interest coverage fell from {ic_prev:.1f}x to {ic_curr:.1f}x ({prev_p} → {curr_p}). "
                    "Less headroom to service debt from operating profit."
                ),
                figures=[
                    {"metric": "Interest coverage", "period": curr_p, "value": ic_curr, "unit": "x"},
                    {"metric": "Interest coverage", "period": prev_p, "value": ic_prev, "unit": "x"},
                ],
                verify=["Capital IQ Financials → Income Statement → EBIT and Interest Expense"],
            ),
        )

    return flags


def _guidance_flags(records: list[GuidanceRecord], items: list[FinancialLineItem]) -> list[DataFlag]:
    flags: list[DataFlag] = []
    if not records:
        return flags

    from iis.finance.guidance_track import guidance_track_rows

    pivoted = pivot_guidance(records)
    track = {(r["metric"], r["fiscal_year"]): r for r in guidance_track_rows(records)}
    beats = 0
    misses = 0
    tracked = 0
    spend_over = 0
    spend_tracked = 0
    track_details: list[str] = []

    for (_base, fy), bucket in sorted(pivoted.items(), key=lambda x: x[0][1], reverse=True):
        guidance = bucket.get("guidance")
        actual = bucket.get("actual")
        consensus = bucket.get("consensus")
        metric = bucket["metric"]
        row = track.get((metric, fy)) or {}
        outcome = row.get("outcome")

        if guidance is None or actual is None or not outcome:
            continue

        guided_str = row.get("guidance_fmt") or _fmt_mn(guidance)
        actual_str = row.get("actual_fmt") or _fmt_mn(actual)
        diff_label = row.get("delta_label") or ""
        figures = [
            {"metric": metric, "period": fy, "field": "guidance", "value": guidance},
            {"metric": metric, "period": fy, "field": "actual", "value": actual},
        ]
        verify = [f"Capital IQ Estimates → Guidance sheet → {metric} → column {fy}"]
        track_details.append(f"{fy} {metric}: guided {guided_str}, actual {actual_str} ({outcome})")

        if row.get("spend_metric"):
            spend_tracked += 1
            if outcome == "spent above guidance":
                spend_over += 1
                _add_flag(
                    flags,
                    DataFlag(
                        id=f"guidance_overspend_{fy}_{_slug(metric)}",
                        category="guidance",
                        severity="info",
                        headline=f"{metric} came in above guidance for {fy}",
                        explanation=(
                            f"Management guided {guided_str} for {metric} in {fy}; actual was {actual_str} "
                            f"({diff_label})."
                        ),
                        figures=figures,
                        verify=verify,
                        source=bucket.get("source"),
                    ),
                )
        else:
            tracked += 1
            if outcome == "met/beat":
                beats += 1
            else:
                misses += 1
                _add_flag(
                    flags,
                    DataFlag(
                        id=f"guidance_miss_{fy}_{_slug(metric)}",
                        category="guidance",
                        severity="review",
                        headline=f"Management missed {metric} guidance for {fy}",
                        explanation=(
                            f"Management guided {guided_str} for {metric} in {fy}; actual was {actual_str} "
                            f"({diff_label} vs guidance)."
                        ),
                        figures=figures,
                        verify=verify,
                        source=bucket.get("source"),
                    ),
                )
            if outcome == "met/beat" and (row.get("delta_value") or 0) > 2:
                _add_flag(
                    flags,
                    DataFlag(
                        id=f"guidance_beat_{fy}_{_slug(metric)}",
                        category="guidance",
                        severity="info",
                        headline=f"Management beat {metric} guidance for {fy}",
                        explanation=(
                            f"Management guided {guided_str} for {metric} in {fy}; actual was {actual_str} "
                            f"({diff_label} vs guidance)."
                        ),
                        figures=figures,
                        verify=verify,
                        source=bucket.get("source"),
                    ),
                )

        if consensus is not None and not row.get("spend_metric") and not _is_growth_metric(metric):
            if guidance < consensus * 0.97:
                _add_flag(
                    flags,
                    DataFlag(
                        id=f"guidance_below_consensus_{fy}_{_slug(metric)}",
                        category="guidance",
                        severity="info",
                        headline=f"Management guidance was below consensus for {fy} {metric}",
                        explanation=(
                            f"Management guided {_fmt_mn(guidance)} vs consensus {_fmt_mn(consensus)} — "
                            "internal outlook was more cautious than the Street at guidance time."
                        ),
                        figures=[
                            {"metric": metric, "period": fy, "field": "guidance", "value": guidance},
                            {"metric": metric, "period": fy, "field": "consensus", "value": consensus},
                        ],
                        verify=[f"Capital IQ Estimates → Guidance sheet, {fy}"],
                    ),
                )

        # Reconcile guidance actual vs financials
        fin_actual = _financials_value_for_guidance(items, metric, fy)
        if fin_actual is not None and actual is not None and not _is_growth_metric(metric):
            if abs(actual) > 0:
                gap_pct = abs(fin_actual - actual) / abs(actual) * 100
                if gap_pct > 5:
                    _add_flag(
                        flags,
                        DataFlag(
                            id=f"guidance_vs_financials_{fy}_{_slug(metric)}",
                            category="reconciliation",
                            severity="review",
                            headline=f"Guidance tab actual differs from Financials for {fy} {metric}",
                            explanation=(
                                f"Guidance sheet shows actual {_fmt_mn(actual)} for {metric} in {fy}; "
                                f"Financials workbook shows {_fmt_mn(fin_actual)} — difference of {gap_pct:.1f}%. "
                                "Reconcile before modelling."
                            ),
                            figures=[
                                {"metric": metric, "period": fy, "source": "guidance_sheet", "value_mn": actual},
                                {"metric": metric, "period": fy, "source": "financials", "value_mn": fin_actual},
                            ],
                            verify=[
                                "Capital IQ Estimates → Guidance → Actual/Latest",
                                "Capital IQ Financials → matching line item",
                            ],
                        ),
                    )

    if spend_tracked >= 2 and spend_over:
        _add_flag(
            flags,
            DataFlag(
                id="guidance_spend_track_record",
                category="guidance",
                severity="review" if spend_over * 2 > spend_tracked else "info",
                headline=f"Spend came in above guidance in {spend_over} of {spend_tracked} comparable years",
                explanation=(
                    "Latest: "
                    + next((d for d in track_details if "spent" in d or "within guidance" in d), "")
                    + ". Full year-by-year table in the guidance track section."
                ),
                figures=[{"spend_over": spend_over, "tracked": spend_tracked}],
                verify=["Capital IQ Estimates → Guidance sheet — compare Guidance vs Actual rows by FY"],
            ),
        )

    if tracked >= 2:
        _add_flag(
            flags,
            DataFlag(
                id="guidance_track_record",
                category="guidance",
                severity="review" if misses > beats else "info",
                headline=f"Management guidance met in {beats} of {tracked} comparable years",
                explanation=(
                    f"Track record on Guidance sheet (guidance vs actual): "
                    + "; ".join(track_details[:5])
                    + ("..." if len(track_details) > 5 else "")
                ),
                figures=[{"beats": beats, "misses": misses, "tracked": tracked}],
                verify=["Capital IQ Estimates → Guidance sheet — compare Guidance vs Actual rows by FY"],
            ),
        )

    return flags


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


def _financials_value_for_guidance(items: list[FinancialLineItem], metric: str, fiscal_year: str) -> float | None:
    lower = metric.lower()
    line_item = None
    if "revenue" in lower or "sales" in lower:
        line_item = "total_revenue"
    elif "ebitda" in lower:
        line_item = "ebitda"
    elif "net income" in lower or "profit" in lower or "pat" in lower:
        line_item = "net_income"
    elif "eps" in lower:
        line_item = "eps_normalized"
    if not line_item:
        return None

    fy_year = re.search(r"20\d{2}", fiscal_year)
    if not fy_year:
        return None
    target = fy_year.group(0)

    matches = [
        i
        for i in query_financials(items, line_item=line_item, period_type=PeriodType.ACTUAL)
        if i.value is not None and i.period_end and target in (i.period_end or "")
    ]
    if not matches:
        latest = get_latest_actual(items, line_item)
        return float(latest.value) if latest and latest.value else None
    matches.sort(key=lambda x: x.period_end or "", reverse=True)
    return float(matches[0].value)


def _reconciliation_flags(session: ResearchSession) -> list[DataFlag]:
    flags: list[DataFlag] = []
    art = next((a for a in session.agent_artifacts if a.artifact_type == "annual_report_analysis"), None)
    if not art or not session.financial_line_items:
        return flags

    fin_statements = art.payload.get("financial_statements") or {}
    for filename, fin in fin_statements.items():
        income = fin.get("income_statement") or {}
        ar_rev = income.get("total_revenue")
        if ar_rev is None:
            continue
        latest = get_latest_actual(session.financial_line_items, "total_revenue")
        if latest is None or latest.value is None:
            continue
        ciq_rev = float(latest.value)
        if ciq_rev == 0:
            continue
        gap_pct = abs(ciq_rev - ar_rev) / abs(ciq_rev) * 100
        if gap_pct > 5:
            _add_flag(
                flags,
                DataFlag(
                    id=f"ar_vs_ciq_revenue_{_slug(filename)}",
                    category="reconciliation",
                    severity="review",
                    headline="Capital IQ revenue differs from annual report extraction",
                    explanation=(
                        f"Capital IQ shows revenue {_fmt_mn(ciq_rev)} ({latest.period_label}); "
                        f"annual report extraction shows {_fmt_mn(ar_rev)} — difference of {gap_pct:.1f}%. "
                        "Reconcile reporting basis (consolidated vs standalone, units) before using in models."
                    ),
                    figures=[
                        {"metric": "Revenue", "source": "capital_iq", "value_mn": ciq_rev, "period": latest.period_end},
                        {"metric": "Revenue", "source": "annual_report", "value_mn": ar_rev, "file": filename},
                    ],
                    verify=[
                        "Capital IQ Financials → Income Statement → Total Revenue",
                        f"Annual report {filename} → Income Statement (extracted in analyze pipeline)",
                    ],
                ),
            )

        ar_ni = income.get("net_income")
        latest_ni = get_latest_actual(session.financial_line_items, "net_income")
        if ar_ni is not None and latest_ni is not None and latest_ni.value:
            gap_pct_ni = abs(float(latest_ni.value) - ar_ni) / abs(float(latest_ni.value)) * 100
            if gap_pct_ni > 5:
                _add_flag(
                    flags,
                    DataFlag(
                        id=f"ar_vs_ciq_ni_{_slug(filename)}",
                        category="reconciliation",
                        severity="review",
                        headline="Capital IQ net income differs from annual report extraction",
                        explanation=(
                            f"Capital IQ net income {_fmt_mn(float(latest_ni.value))} vs "
                            f"annual report {_fmt_mn(ar_ni)} — difference of {gap_pct_ni:.1f}%."
                        ),
                        figures=[
                            {"metric": "Net income", "source": "capital_iq", "value_mn": float(latest_ni.value)},
                            {"metric": "Net income", "source": "annual_report", "value_mn": ar_ni, "file": filename},
                        ],
                        verify=["Capital IQ Financials vs annual report financial statements"],
                    ),
                )
    return flags


def _segment_and_industry_flags(items: list[FinancialLineItem]) -> list[DataFlag]:
    flags: list[DataFlag] = []
    segments = ["jewellery", "watches", "eyecare", "apparel", "others"]
    growth_by_seg: dict[str, float] = {}

    for seg in segments:
        seg_items = query_financials(items, statement="segments", line_item=seg, period_type=PeriodType.ACTUAL)
        by_period: dict[str, float] = {}
        for i in seg_items:
            if i.value is not None and i.period_end:
                by_period[i.period_end] = float(i.value)
        series = sorted(by_period.items(), key=lambda x: x[0], reverse=True)
        if len(series) < 2:
            continue
        yoy = _yoy_pct(series[0][1], series[1][1])
        if yoy is not None:
            growth_by_seg[seg] = yoy

    if len(growth_by_seg) >= 2:
        sorted_g = sorted(growth_by_seg.items(), key=lambda x: x[1], reverse=True)
        best_seg, best_g = sorted_g[0]
        worst_seg, worst_g = sorted_g[-1]
        if best_g - worst_g > 15:
            _add_flag(
                flags,
                DataFlag(
                    id="segment_growth_dispersion",
                    category="industry",
                    severity="info",
                    headline="Growth is concentrated in one segment, not broad-based",
                    explanation=(
                        f"{best_seg.title()} revenue grew {_fmt_pct(best_g)} while "
                        f"{worst_seg.title()} grew {_fmt_pct(worst_g)} — "
                        f"spread of {best_g - worst_g:.0f} pp between segments."
                    ),
                    figures=[
                        {"segment": s, "yoy_pct": g} for s, g in sorted_g
                    ],
                    verify=["Capital IQ Financials → Segments sheet"],
                ),
            )

    rev_series = _get_actual_series(items, "total_revenue")
    if len(rev_series) >= 2:
        company_yoy = _yoy_pct(rev_series[0][1], rev_series[1][1])
        if company_yoy is not None:
            _add_flag(
                flags,
                DataFlag(
                    id="company_growth_context",
                    category="industry",
                    severity="info",
                    headline=f"Company revenue grew {_fmt_pct(company_yoy)} YoY (latest vs prior actual)",
                    explanation=(
                        "Compare this to category/industry growth cited in the annual report Industry section "
                        "or peer Capital IQ files. Upload peer Financials to enable automatic peer variance flags."
                    ),
                    figures=[
                        {"metric": "Total revenue", "period": rev_series[0][0], "value_mn": rev_series[0][1], "yoy_pct": company_yoy},
                    ],
                    verify=[
                        "Capital IQ Financials → Income Statement",
                        "Annual report → Industry Overview (manual compare)",
                    ],
                ),
            )

    return flags


def compute_data_flags(
    session: ResearchSession,
    *,
    profile: Any | None = None,
) -> list[DataFlag]:
    """Run all verifiable flag rules on session data; filter by checklist profile."""
    from iis.finance.checklist import filter_flags_by_profile, load_checklist_profile

    prof = profile or load_checklist_profile()
    items = session.financial_line_items
    flags: list[DataFlag] = []

    if items:
        flags.extend(_cash_and_working_capital_flags(items, thresholds=prof.thresholds))
        flags.extend(_segment_and_industry_flags(items))

    if session.guidance_records:
        flags.extend(_guidance_flags(session.guidance_records, items))

    flags.extend(_reconciliation_flags(session))

    try:
        from iis.finance.data_freshness import freshness_flags

        flags.extend(freshness_flags(session))
    except Exception:
        pass

    flags = filter_flags_by_profile(flags, prof)

    # Sort: review before info
    severity_order = {"review": 0, "info": 1}
    flags.sort(key=lambda f: (severity_order.get(f.severity, 9), f.category, f.id))
    return flags


def flags_to_payload(flags: list[DataFlag]) -> dict[str, Any]:
    review = [f for f in flags if f.severity == "review"]
    return {
        "total": len(flags),
        "review_count": len(review),
        "flags": [asdict(f) for f in flags],
    }


def format_flags_for_chat(flags: list[DataFlag], *, max_flags: int = 12) -> str:
    if not flags:
        return (
            "No data flags triggered on uploaded financials. "
            "Upload Capital IQ Financials + Estimates (Guidance sheet) for full checks."
        )
    review = [f for f in flags if f.severity == "review"]
    lines = [f"**{len(review)} item(s) to review** (of {len(flags)} total flags)\n"]
    for i, flag in enumerate(flags[:max_flags], 1):
        prefix = "⚠" if flag.severity == "review" else "·"
        lines.append(f"{prefix} **{flag.headline}**")
        lines.append(f"  {flag.explanation}")
        if flag.verify:
            lines.append(f"  _Verify:_ {'; '.join(flag.verify[:2])}")
        lines.append("")
    if len(flags) > max_flags:
        lines.append(f"_…and {len(flags) - max_flags} more flags in session artifact._")
    return "\n".join(lines)
