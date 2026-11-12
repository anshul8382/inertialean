"""Inertia standardised analyst scorecard (7 layers + critical-15).

Python computes numbers, YoY direction, and concern flags from CIQ actuals.
Always included in the report appendix when data exists; also shared with Hive.
"""
from __future__ import annotations

from typing import Any

from iis.finance.ratios import (
    build_period_metrics,
    compute_period_ratios,
    format_ratio_value,
)
from iis.models.session import ResearchSession

# Critical-15 (mandatory automation)
CRITICAL_15: list[tuple[str, str, str]] = [
    ("rev_yoy", "Revenue growth YoY", "growth"),
    ("ebitda_yoy", "EBITDA growth YoY", "growth"),
    ("ebitda_margin", "EBITDA margin", "profitability"),
    ("pat_yoy", "PAT growth YoY", "growth"),
    ("eps_yoy", "EPS growth YoY", "growth"),
    ("gross_margin", "Gross margin", "profitability"),
    ("ebit_margin", "EBIT margin", "profitability"),
    ("cfo_to_ni", "CFO / PAT", "cash"),
    ("fcf_mn", "Free cash flow", "cash"),
    ("roce", "ROCE", "returns"),
    ("incremental_roce", "Incremental ROCE", "returns"),
    ("receivable_days", "Debtor days", "wc"),
    ("inventory_days", "Inventory days", "wc"),
    ("net_debt_to_ebitda", "Net Debt / EBITDA", "leverage"),
    ("valuation", "EV/EBITDA & P/E (if available)", "valuation"),
]

LAYERS = (
    ("growth", "1. Growth"),
    ("profitability", "2. Profitability"),
    ("financial_health", "3. Financial health"),
    ("cash", "4. Cash flow quality"),
    ("returns", "5. Capital efficiency"),
    ("outlook", "6. Business outlook"),
    ("valuation", "7. Valuation"),
)


def _fy(pe: str) -> str:
    if len(pe) >= 4 and pe[:4].isdigit():
        return f"FY{pe[:4][-2:]}"
    return (pe or "?")[:10]


def _annual_periods(items, *, max_years: int = 10) -> list[str]:
    periods = build_period_metrics(items)
    ordered = sorted(periods.keys())
    annual = [p for p in ordered if len(p) >= 7 and p[5:7] in ("03", "12")]
    use = annual if annual else ordered
    return use[-max_years:]


def _yoy(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return curr / prev - 1


def _fmt_pct_chg(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.1f}%"


def _fmt_mn(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}"


def _metric_from_pm(pm, key: str) -> float | None:
    aliases = {
        "revenue": ("total_revenue", "revenue", "as_reported_total_revenue"),
        "ebitda": ("ebitda",),
        "ebit": ("ebit",),
        "pat": ("net_income",),
        "pbt": ("ebt_excl_unusual_items", "ebt_incl_unusual_items", "earnings_from_cont_ops"),
        "interest": ("interest_expense",),
        "dep": ("depreciation_amort", "da"),
        "cfo": ("cash_from_ops",),
        "capex": ("capital_expenditure",),
        "debt": ("total_debt",),
        "cash": ("total_cash_st_investments", "cash_and_equivalents"),
        "other_income": ("interest_and_invest_income", "other_non_operating_income"),
        "eps": ("diluted_eps_excl_extra_items", "diluted_eps", "basic_eps"),
        "equity": ("total_common_equity", "total_equity", "book_value_of_common_equity"),
        "gross_profit": ("gross_profit",),
    }
    names = aliases.get(key, (key,))
    return pm.get(*names) if pm else None


def build_inertia_scorecard(session: ResearchSession, *, max_years: int = 8) -> dict[str, Any]:
    items = list(session.financial_line_items or [])
    if not items:
        return {"ok": False, "reason": "no_financials"}

    periods = _annual_periods(items, max_years=max_years)
    if not periods:
        return {"ok": False, "reason": "no_periods"}

    pm_map = build_period_metrics(items)
    ratios_by = {pe: compute_period_ratios(pm_map[pe]) for pe in periods if pe in pm_map}
    labels = [_fy(p) for p in periods]

    # Basic P&L / BS / CF panel (latest vs prior YoY)
    pe_c, pe_p = periods[-1], periods[-2] if len(periods) >= 2 else (periods[-1], None)
    basic_rows: list[dict[str, Any]] = []
    for key, title in (
        ("revenue", "Revenue"),
        ("ebitda", "EBITDA"),
        ("ebit", "EBIT"),
        ("dep", "Depreciation"),
        ("interest", "Interest cost"),
        ("pbt", "PBT"),
        ("pat", "PAT"),
        ("eps", "EPS"),
        ("cfo", "CFO"),
        ("capex", "Capex"),
        ("debt", "Gross debt"),
        ("cash", "Cash & ST investments"),
        ("other_income", "Other income"),
    ):
        cur = _metric_from_pm(pm_map.get(pe_c), key)
        prev = _metric_from_pm(pm_map.get(pe_p), key) if pe_p else None
        # Capex often negative in CF
        if key == "capex" and cur is not None:
            cur = abs(cur)
        if key == "capex" and prev is not None:
            prev = abs(prev)
        yoy = _yoy(cur, prev)
        basic_rows.append(
            {
                "metric": title,
                "current": cur,
                "prior": prev,
                "yoy": yoy,
                "current_fy": labels[-1],
                "prior_fy": labels[-2] if len(labels) > 1 else "—",
            }
        )

    # Net debt
    debt_c = _metric_from_pm(pm_map.get(pe_c), "debt")
    cash_c = _metric_from_pm(pm_map.get(pe_c), "cash")
    debt_p = _metric_from_pm(pm_map.get(pe_p), "debt") if pe_p else None
    cash_p = _metric_from_pm(pm_map.get(pe_p), "cash") if pe_p else None
    nd_c = (debt_c - cash_c) if debt_c is not None and cash_c is not None else None
    nd_p = (debt_p - cash_p) if debt_p is not None and cash_p is not None else None
    basic_rows.append(
        {
            "metric": "Net debt",
            "current": nd_c,
            "prior": nd_p,
            "yoy": _yoy(nd_c, nd_p) if nd_c is not None and nd_p is not None and nd_p != 0 else None,
            "current_fy": labels[-1],
            "prior_fy": labels[-2] if len(labels) > 1 else "—",
        }
    )

    # Margin / return / WC history
    ratio_keys = [
        "gross_margin",
        "ebitda_margin",
        "ebit_margin",
        "net_margin",
        "roe",
        "roce",
        "roa",
        "interest_coverage",
        "debt_to_equity",
        "net_debt_to_ebitda",
        "receivable_days",
        "inventory_days",
        "payable_days",
        "current_ratio",
        "cfo_to_ni",
        "fcf_margin",
        "capex_to_sales",
        "asset_turnover",
    ]
    ratio_rows: list[dict[str, Any]] = []
    for key in ratio_keys:
        vals = [ratios_by.get(pe, {}).get(key) for pe in periods]
        if all(v is None for v in vals):
            continue
        ratio_rows.append(
            {
                "key": key,
                "label": key.replace("_", " ").title(),
                "values": vals,
                "formatted": [format_ratio_value(key, v) if v is not None else "—" for v in vals],
            }
        )

    # Critical-15 snapshot (latest + YoY where applicable)
    r_c = ratios_by.get(pe_c) or {}
    r_p = ratios_by.get(pe_p) or {} if pe_p else {}
    rev_c = _metric_from_pm(pm_map.get(pe_c), "revenue")
    rev_p = _metric_from_pm(pm_map.get(pe_p), "revenue") if pe_p else None
    ebitda_c = _metric_from_pm(pm_map.get(pe_c), "ebitda")
    ebitda_p = _metric_from_pm(pm_map.get(pe_p), "ebitda") if pe_p else None
    pat_c = _metric_from_pm(pm_map.get(pe_c), "pat")
    pat_p = _metric_from_pm(pm_map.get(pe_p), "pat") if pe_p else None
    eps_c = _metric_from_pm(pm_map.get(pe_c), "eps")
    eps_p = _metric_from_pm(pm_map.get(pe_p), "eps") if pe_p else None
    fcf_c = r_c.get("fcf_mn")
    fcf_p = r_p.get("fcf_mn")

    # Incremental ROCE ≈ ΔEBIT / Δ capital employed (equity+debt proxy)
    ebit_c = _metric_from_pm(pm_map.get(pe_c), "ebit")
    ebit_p = _metric_from_pm(pm_map.get(pe_p), "ebit") if pe_p else None
    eq_c = _metric_from_pm(pm_map.get(pe_c), "equity")
    eq_p = _metric_from_pm(pm_map.get(pe_p), "equity") if pe_p else None
    cap_c = (eq_c or 0) + (debt_c or 0) if eq_c is not None or debt_c is not None else None
    cap_p = (eq_p or 0) + (debt_p or 0) if eq_p is not None or debt_p is not None else None
    incr_roce = None
    if ebit_c is not None and ebit_p is not None and cap_c is not None and cap_p is not None:
        d_cap = cap_c - cap_p
        if abs(d_cap) > 1:
            incr_roce = (ebit_c - ebit_p) / d_cap

    critical: list[dict[str, Any]] = [
        {"id": "rev_yoy", "label": "Revenue growth YoY", "value": _yoy(rev_c, rev_p), "fmt": _fmt_pct_chg(_yoy(rev_c, rev_p)), "layer": "growth"},
        {"id": "ebitda_yoy", "label": "EBITDA growth YoY", "value": _yoy(ebitda_c, ebitda_p), "fmt": _fmt_pct_chg(_yoy(ebitda_c, ebitda_p)), "layer": "growth"},
        {"id": "ebitda_margin", "label": "EBITDA margin", "value": r_c.get("ebitda_margin"), "fmt": format_ratio_value("ebitda_margin", r_c.get("ebitda_margin")), "layer": "profitability"},
        {"id": "pat_yoy", "label": "PAT growth YoY", "value": _yoy(pat_c, pat_p), "fmt": _fmt_pct_chg(_yoy(pat_c, pat_p)), "layer": "growth"},
        {"id": "eps_yoy", "label": "EPS growth YoY", "value": _yoy(eps_c, eps_p), "fmt": _fmt_pct_chg(_yoy(eps_c, eps_p)), "layer": "growth"},
        {"id": "gross_margin", "label": "Gross margin", "value": r_c.get("gross_margin"), "fmt": format_ratio_value("gross_margin", r_c.get("gross_margin")), "layer": "profitability"},
        {"id": "ebit_margin", "label": "EBIT margin", "value": r_c.get("ebit_margin"), "fmt": format_ratio_value("ebit_margin", r_c.get("ebit_margin")), "layer": "profitability"},
        {"id": "cfo_to_ni", "label": "CFO / PAT", "value": r_c.get("cfo_to_ni"), "fmt": format_ratio_value("cfo_to_ni", r_c.get("cfo_to_ni")), "layer": "cash"},
        {"id": "fcf_mn", "label": "Free cash flow ₹ mn", "value": fcf_c, "fmt": _fmt_mn(fcf_c), "layer": "cash"},
        {"id": "roce", "label": "ROCE", "value": r_c.get("roce"), "fmt": format_ratio_value("roce", r_c.get("roce")), "layer": "returns"},
        {"id": "incremental_roce", "label": "Incremental ROCE", "value": incr_roce, "fmt": f"{incr_roce:.1%}" if incr_roce is not None else "—", "layer": "returns"},
        {"id": "receivable_days", "label": "Debtor days", "value": r_c.get("receivable_days"), "fmt": format_ratio_value("receivable_days", r_c.get("receivable_days")), "layer": "financial_health"},
        {"id": "inventory_days", "label": "Inventory days", "value": r_c.get("inventory_days"), "fmt": format_ratio_value("inventory_days", r_c.get("inventory_days")), "layer": "financial_health"},
        {"id": "net_debt_to_ebitda", "label": "Net Debt / EBITDA", "value": r_c.get("net_debt_to_ebitda"), "fmt": format_ratio_value("net_debt_to_ebitda", r_c.get("net_debt_to_ebitda")), "layer": "financial_health"},
    ]

    # Valuation from estimates summary if present
    pe_mult = ev_ebitda = None
    try:
        from iis.finance.multiples import get_multiples_table

        mult = get_multiples_table(session) or {}
        # Prefer NTM / FY1-ish keys
        for k, row in mult.items():
            if not isinstance(row, dict):
                continue
            if pe_mult is None and row.get("pe") is not None:
                pe_mult = row.get("pe")
            if ev_ebitda is None and row.get("ev_ebitda") is not None:
                ev_ebitda = row.get("ev_ebitda")
            if "NTM" in str(k).upper() or "FY1" in str(k).upper():
                pe_mult = row.get("pe", pe_mult)
                ev_ebitda = row.get("ev_ebitda", ev_ebitda)
                break
    except Exception:
        pass
    critical.append(
        {
            "id": "valuation",
            "label": "EV/EBITDA & P/E",
            "value": {"ev_ebitda": ev_ebitda, "pe": pe_mult},
            "fmt": (
                f"EV/EBITDA {ev_ebitda:.1f}x" if ev_ebitda is not None else "EV/EBITDA —"
            )
            + " · "
            + (f"P/E {pe_mult:.1f}x" if pe_mult is not None else "P/E —"),
            "layer": "valuation",
        }
    )

    # Concern flags (early warnings)
    concerns: list[dict[str, Any]] = []
    rev_g = _yoy(rev_c, rev_p)
    ebitda_g = _yoy(ebitda_c, ebitda_p)
    if rev_g is not None and ebitda_g is not None and rev_g > 0.1 and ebitda_g < rev_g * 0.4:
        concerns.append(
            {
                "severity": "watch",
                "title": "EBITDA lagging revenue growth",
                "value": f"Rev {_fmt_pct_chg(rev_g)} vs EBITDA {_fmt_pct_chg(ebitda_g)}",
                "why": "Operating leverage / cost pressure — early warning even if sales grow.",
            }
        )
    em_c, em_p = r_c.get("ebitda_margin"), r_p.get("ebitda_margin")
    if em_c is not None and em_p is not None and (em_c - em_p) <= -0.02:
        concerns.append(
            {
                "severity": "watch",
                "title": "EBITDA margin contraction",
                "value": f"{format_ratio_value('ebitda_margin', em_p)} → {format_ratio_value('ebitda_margin', em_c)}",
                "why": "Margin compression YoY.",
            }
        )
    cfo_pat = r_c.get("cfo_to_ni")
    if cfo_pat is not None and cfo_pat < 0.7:
        concerns.append(
            {
                "severity": "fail" if cfo_pat < 0.4 else "watch",
                "title": "Weak cash conversion (CFO/PAT)",
                "value": format_ratio_value("cfo_to_ni", cfo_pat),
                "why": "Earnings quality — examine WC and trailing trends (not one year alone).",
            }
        )
    dso_c, dso_p = r_c.get("receivable_days"), r_p.get("receivable_days")
    if dso_c is not None and dso_p is not None and (dso_c - dso_p) >= 15:
        concerns.append(
            {
                "severity": "fail" if (dso_c - dso_p) >= 25 else "watch",
                "title": "Debtor days rising",
                "value": f"{dso_p:.0f} → {dso_c:.0f} days",
                "why": "Collections / channel quality risk.",
            }
        )
    dio_c, dio_p = r_c.get("inventory_days"), r_p.get("inventory_days")
    if dio_c is not None and dio_p is not None and (dio_c - dio_p) >= 20:
        concerns.append(
            {
                "severity": "watch",
                "title": "Inventory days rising",
                "value": f"{dio_p:.0f} → {dio_c:.0f} days",
                "why": "Possible demand softness or stock build.",
            }
        )
    nd_eb = r_c.get("net_debt_to_ebitda")
    if nd_eb is not None and nd_eb > 2.5:
        concerns.append(
            {
                "severity": "fail" if nd_eb > 3.5 else "watch",
                "title": "Elevated net debt / EBITDA",
                "value": format_ratio_value("net_debt_to_ebitda", nd_eb),
                "why": "Leverage — check refinancing and interest coverage.",
            }
        )
    if incr_roce is not None and incr_roce < 0.08 and cap_c is not None and cap_p is not None and (cap_c - cap_p) > 0:
        concerns.append(
            {
                "severity": "watch",
                "title": "Low incremental ROCE on new capital",
                "value": f"{incr_roce:.1%}",
                "why": "Expansion returns look soft vs capital added.",
            }
        )

    return {
        "ok": True,
        "period_labels": labels,
        "periods": periods,
        "latest_fy": labels[-1] if labels else None,
        "prior_fy": labels[-2] if len(labels) > 1 else None,
        "basic_rows": basic_rows,
        "ratio_rows": ratio_rows,
        "critical_15": critical,
        "concerns": concerns,
        "layers": [ {"id": i, "title": t} for i, t in LAYERS ],
    }


def scorecard_markdown(session: ResearchSession, *, max_years: int = 8) -> list[str]:
    sc = build_inertia_scorecard(session, max_years=max_years)
    if not sc.get("ok"):
        return []

    lines: list[str] = [
        "## Inertia analyst scorecard (Python)",
        "",
        f"_Mandatory critical metrics + multi-year ratios from Capital IQ. "
        f"Latest **{sc.get('latest_fy')}** vs **{sc.get('prior_fy') or '—'}**. "
        f"Direction of movement matters as much as the level._",
        "",
        "### Critical-15 snapshot",
        "",
        "| # | Metric | Value | Layer |",
        "|---:|---|---|---|",
    ]
    for i, row in enumerate(sc.get("critical_15") or [], 1):
        lines.append(
            f"| {i} | {row['label']} | {row.get('fmt') or '—'} | {row.get('layer') or ''} |"
        )
    lines.append("")

    concerns = sc.get("concerns") or []
    if concerns:
        lines.append("### Early-warning flags")
        lines.append("")
        lines.append("| Severity | Flag | Value | Why |")
        lines.append("|---|---|---|---|")
        for c in concerns:
            lines.append(
                f"| {(c.get('severity') or '').upper()} | {c.get('title')} | "
                f"{str(c.get('value') or '').replace('|', '/')} | "
                f"{str(c.get('why') or '').replace('|', '/')} |"
            )
        lines.append("")

    # Basic numbers YoY table
    lines.append("### Basic numbers (annual CIQ) — YoY")
    lines.append("")
    lines.append(
        f"| Parameter | {sc.get('latest_fy')} ₹ mn | {sc.get('prior_fy') or 'Prior'} ₹ mn | YoY |"
    )
    lines.append("|---|---:|---:|---:|")
    for r in sc.get("basic_rows") or []:
        cur, prev = r.get("current"), r.get("prior")
        # EPS not in mn
        if r["metric"] == "EPS":
            c_s = f"{cur:,.2f}" if cur is not None else "—"
            p_s = f"{prev:,.2f}" if prev is not None else "—"
        else:
            c_s, p_s = _fmt_mn(cur), _fmt_mn(prev)
        lines.append(f"| {r['metric']} | {c_s} | {p_s} | {_fmt_pct_chg(r.get('yoy'))} |")
    lines.append("")

    # Ratio trends
    labels = sc.get("period_labels") or []
    rows = sc.get("ratio_rows") or []
    if labels and rows:
        lines.append("### Profitability, returns, WC, cash — multi-year")
        lines.append("")
        lines.append("| Ratio | " + " | ".join(labels) + " |")
        lines.append("|---|" + "|".join(["---:"] * len(labels)) + "|")
        for r in rows:
            lines.append(f"| {r['label']} | " + " | ".join(r.get("formatted") or []) + " |")
        lines.append("")

    lines.append(
        "_Volume / realisation / order book / market share need AR/concall extracts when on file — "
        "not inventable from CIQ alone._"
    )
    lines.append("")
    return lines
