"""Subject vs confirmed peers — multi-year basic metrics as tables."""
from __future__ import annotations

from typing import Any

from iis.finance.ratios import build_period_metrics, compute_period_ratios
from iis.models.artifacts import FinancialLineItem
from iis.models.session import ResearchSession

_METRICS: list[tuple[str, str, str]] = [
    ("Revenue ₹ mn", "total_revenue", "mn"),
    ("EBITDA ₹ mn", "ebitda", "mn"),
    ("EBITDA margin", "ebitda_margin", "pct"),
    ("PAT ₹ mn", "net_income", "mn"),
    ("ROE", "roe", "pct"),
    ("ROCE", "roce", "pct"),
    ("Net debt / EBITDA", "net_debt_to_ebitda", "x"),
]


def _fy(pe: str) -> str:
    if len(pe) >= 4 and pe[:4].isdigit():
        return f"FY{pe[:4][-2:]}"
    return pe[:8]


def _annual_periods(items: list[FinancialLineItem], *, max_years: int = 6) -> list[str]:
    periods = build_period_metrics(items)
    ordered = sorted(periods.keys())
    annual = [p for p in ordered if len(p) >= 7 and p[5:7] in ("03", "12")]
    use = annual if annual else ordered
    return use[-max_years:]


def _series_for(
    items: list[FinancialLineItem],
    periods: list[str],
) -> dict[str, dict[str, float | None]]:
    pm = build_period_metrics(items)
    out: dict[str, dict[str, float | None]] = {}
    for key_label, key, kind in _METRICS:
        vals: dict[str, float | None] = {}
        for pe in periods:
            if kind == "mn":
                vals[pe] = pm.get(pe, {}).get(key)
            else:
                ratios = compute_period_ratios(pm.get(pe) or {})
                vals[pe] = ratios.get(key)
        out[key_label] = vals
    return out


def _fmt(v: float | None, kind: str) -> str:
    if v is None:
        return "—"
    if kind == "pct":
        x = v * 100 if abs(v) <= 1.5 else v
        return f"{x:.1f}%"
    if kind == "x":
        return f"{v:.2f}x"
    return f"{v:,.0f}"


def peer_timeline_markdown(session: ResearchSession, *, max_years: int = 6) -> list[str]:
    """Multi-year tables: subject + each confirmed peer with CIQ on file."""
    try:
        from iis.finance.competitor_discovery import confirmed_peer_libraries
        from iis.library.store import get_company_library_store
    except Exception:
        return []

    peers = confirmed_peer_libraries(session)
    if not peers:
        return []

    subject_items = list(session.financial_line_items or [])
    if not subject_items:
        return []

    store = get_company_library_store()
    companies: list[tuple[str, list[FinancialLineItem]]] = [
        (
            f"**{(session.company.name if session.company else 'Subject')}**",
            subject_items,
        )
    ]
    for p in peers[:6]:
        try:
            lib = store.load(p["company_key"])
        except Exception:
            continue
        if not lib.financial_line_items:
            continue
        companies.append((p["name"][:40], list(lib.financial_line_items)))

    if len(companies) < 2:
        return []

    # Align on subject annual periods
    periods = _annual_periods(subject_items, max_years=max_years)
    if not periods:
        return []
    labels = [_fy(p) for p in periods]

    lines: list[str] = [
        "## Peer comparison — timeline (confirmed peers, CIQ)",
        "",
        f"_Subject vs {len(companies) - 1} confirmed peer(s) with Capital IQ Financials. "
        f"Periods {labels[0]}–{labels[-1]}._",
        "",
    ]

    # One table per metric: rows = companies, cols = years
    for metric_label, key, kind in _METRICS:
        lines.append(f"### {metric_label}")
        lines.append("")
        lines.append("| Company | " + " | ".join(labels) + " |")
        lines.append("|---|" + "|".join(["---:"] * len(periods)) + "|")
        for name, items in companies:
            series = _series_for(items, periods)
            vals = series.get(metric_label) or {}
            cells = [_fmt(vals.get(pe), kind) for pe in periods]
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
        lines.append("")

    # Latest-year side-by-side snapshot
    pe = periods[-1]
    lines.append(f"### Latest year snapshot ({_fy(pe)})")
    lines.append("")
    lines.append("| Company | " + " | ".join(m[0] for m in _METRICS) + " |")
    lines.append("|---|" + "|".join(["---:"] * len(_METRICS)) + "|")
    for name, items in companies:
        series = _series_for(items, [pe])
        cells = []
        for metric_label, _, kind in _METRICS:
            cells.append(_fmt((series.get(metric_label) or {}).get(pe), kind))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines.append("")
    return lines
