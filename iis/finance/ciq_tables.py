"""Capital IQ financials + estimates as markdown tables for the research report.

Renders statements largely as uploaded (line_item_raw × fiscal periods),
plus consensus estimates and per-broker sections when brokerage PDFs exist.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from iis.models.artifacts import ConsensusMetric, FinancialLineItem
from iis.models.enums import PeriodType
from iis.models.session import ResearchSession

_STATEMENT_TITLES = {
    "income_statement": "Income Statement (Capital IQ)",
    "balance_sheet": "Balance Sheet (Capital IQ)",
    "cash_flow": "Cash Flow Statement (Capital IQ)",
    "ratios": "Ratios (Capital IQ)",
    "key_stats": "Key Stats (Capital IQ)",
    "segments": "Segments (Capital IQ)",
    "multiples": "Trading Multiples (Capital IQ)",
    "historical_capitalization": "Historical Capitalization (Capital IQ)",
    "capital_structure": "Capital Structure (Capital IQ)",
    "supplemental": "Supplemental (Capital IQ)",
    "pension_opeb": "Pension / OPEB (Capital IQ)",
}

_SKIP_LINE_ITEMS = {
    "calculation_type",
    "currency",
    "filing_date",
    "for_the_fiscal_period_ending",
    "restatement_type",
    "template",
    "order",
}

# Prefer these metrics first in estimates tables (then remaining alphabetically)
_EST_PRIORITY = (
    "Revenue",
    "EBITDA",
    "EBIT",
    "EBT (Excl. Excep)",
    "Net Income (Excl. Excep)",
    "Net Income (GAAP)",
    "EPS Normalized",
    "EPS (GAAP)",
    "Gross Margin %",
    "Capital Expenditure",
    "Cash From Operations",
    "Free Cash Flow",
    "Net Debt",
    "ROE %",
    "ROA %",
    "DPS",
    "Book Value / Share",
    "TEV",
)


_MONTH_ABBR = {
    "01": "Jan",
    "02": "Feb",
    "03": "Mar",
    "04": "Apr",
    "05": "May",
    "06": "Jun",
    "07": "Jul",
    "08": "Aug",
    "09": "Sep",
    "10": "Oct",
    "11": "Nov",
    "12": "Dec",
}

# Compact P&L / cash lines for 8-quarter trend (normalized line_item keys)
_QUARTER_TREND_KEYS = (
    "total_revenue",
    "revenue",
    "total_revenues",
    "ebitda",
    "ebit",
    "operating_income",
    "net_income",
    "net_income_to_company",
    "diluted_eps",
    "basic_eps",
    "gross_profit",
    "cash_from_operations",
    "capital_expenditure",
    "free_cash_flow",
)


def _fy_label(period_end: str | None, period_label: str = "") -> str:
    pe = (period_end or "")[:10]
    pl = (period_label or "").lower()
    if "ltm" in pl:
        return f"LTM {pe[:4]}" if pe[:4].isdigit() else "LTM"
    if len(pe) >= 4 and pe[:4].isdigit():
        return f"FY{str(pe[:4])[-2:]}"
    pl_raw = (period_label or "").strip()
    return pl_raw[:12] if pl_raw else (pe or "?")


def _q_label(period_end: str | None, period_label: str = "") -> str:
    import re

    pe = (period_end or "")[:10]
    pl = period_label or ""
    fq = re.search(r"\bFQ([1-4])\b", pl, re.I)
    if len(pe) >= 7:
        mon = _MONTH_ABBR.get(pe[5:7], pe[5:7])
        yy = pe[2:4]
        if fq:
            return f"FQ{fq.group(1)} {mon}-{yy}"
        return f"{mon}-{yy}"
    if fq:
        return f"FQ{fq.group(1)}"
    return (pl.strip()[:14] if pl.strip() else (pe or "?"))


def _period_kind(period_end: str | None, period_type: Any, period_label: str = "") -> str:
    """Classify a CIQ column: annual | quarterly | ltm | skip.

    Annual workbook labels: ``12 months Mar-31-2026A``, ``Reclassified 12 months …``
    Quarterly workbook labels: ``3 months FQ1/CQ2 Jun-30-2025``,
    ``Press Release FQ2/CQ3 Sep-30-2025`` (balance sheet), ``FQ4/CQ1 Mar-31-2026``.
    """
    import re

    pe = (period_end or "")[:10]
    pl = (period_label or "").lower()
    pt = period_type.value if hasattr(period_type, "value") else str(period_type or "")
    pt = pt.lower()

    if "ltm" in pl:
        return "ltm"
    # Quarterly first — FQ/CQ markers beat "press release" alone
    if (
        "3 months" in pl
        or re.search(r"\bfq\d\b", pl)
        or "/cq" in pl
        or re.search(r"\bfq\d", pl)
        or "quarterly" in pl
    ):
        return "quarterly"
    if "12 months" in pl or pl.startswith("fy") or " fiscal year" in pl:
        return "annual"
    if pt == PeriodType.PRESS_RELEASE.value or "press release" in pl:
        # Lone PR stubs without quarter markers — skip
        return "skip"
    if len(pe) < 7:
        return "skip"
    mm = pe[5:7]
    # Label missing: Mar/Dec YE annuals; Jun/Sep treated as quarters (India Mar YE)
    if mm in ("03", "12"):
        return "annual"
    if mm in ("06", "09"):
        return "quarterly"
    return "skip"


def _is_annual_period(
    period_end: str | None,
    period_type: Any,
    period_label: str = "",
) -> bool:
    return _period_kind(period_end, period_type, period_label) == "annual"


def _is_quarterly_period(
    period_end: str | None,
    period_type: Any,
    period_label: str = "",
) -> bool:
    return _period_kind(period_end, period_type, period_label) == "quarterly"


def _fmt_cell(v: float | None, *, line_item: str = "") -> str:
    if v is None:
        return "—"
    # Ratios / EPS / margins: keep decimals
    li = (line_item or "").lower()
    if any(k in li for k in ("margin", "roe", "roa", "roce", "ratio", "turnover", "days", "eps", "%", "per_share", "pershare")):
        if abs(v) < 10 and ("margin" in li or "roe" in li or "roa" in li or "roce" in li or li.endswith("_pct") or "%" in li):
            # CIQ ratios sometimes already in percent units
            if abs(v) <= 1.5 and ("margin" in li or "roe" in li or "roa" in li or "roce" in li):
                return f"{v * 100:.1f}%"
            return f"{v:.1f}"
        if "eps" in li or "per_share" in li or "pershare" in li or "dps" in li or "book_value" in li:
            return f"{v:,.2f}"
        return f"{v:,.2f}"
    if abs(v) >= 100 or float(v).is_integer():
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def _period_columns(
    items: list[FinancialLineItem],
    *,
    max_periods: int = 8,
    kind: str = "annual",
) -> list[tuple[str, str]]:
    """Return [(period_end, short_label), ...] oldest → newest, capped.

    kind: annual | quarterly | any
    """
    candidates: dict[str, str] = {}
    for it in items:
        pe = (it.period_end or "")[:10]
        if not pe:
            continue
        pk = _period_kind(pe, it.period_type, it.period_label or "")
        if kind == "annual" and pk != "annual":
            continue
        if kind == "quarterly" and pk != "quarterly":
            continue
        if kind == "any" and pk in ("skip", "ltm"):
            continue
        if kind == "quarterly":
            candidates[pe] = _q_label(pe, it.period_label)
        else:
            candidates[pe] = _fy_label(pe, it.period_label)
    ordered = sorted(candidates.keys())
    if len(ordered) > max_periods:
        ordered = ordered[-max_periods:]
    return [(pe, candidates[pe]) for pe in ordered]


def _render_statement_grid(
    rows: list[FinancialLineItem],
    cols: list[tuple[str, str]],
    *,
    title: str,
    footnote: str,
    keep_keys: set[str] | None = None,
    kind: str | None = None,
) -> list[str]:
    if not cols:
        return []
    col_set = {c[0] for c in cols}
    by_line: dict[str, dict[str, float | None]] = defaultdict(dict)
    labels: dict[str, str] = {}
    order: list[str] = []
    for it in rows:
        key = it.line_item or it.line_item_raw or ""
        if not key or key.startswith("_") or key in _SKIP_LINE_ITEMS:
            continue
        if keep_keys is not None and key not in keep_keys:
            continue
        pe = (it.period_end or "")[:10]
        if pe not in col_set:
            continue
        # Annual vs quarterly can share period_end (e.g. Mar YE Q4) — keep kinds apart
        if kind in ("annual", "quarterly"):
            if _period_kind(pe, it.period_type, it.period_label or "") != kind:
                continue
        if key not in labels:
            labels[key] = (it.line_item_raw or key.replace("_", " ")).strip() or key
            order.append(key)
        if pe not in by_line[key] or by_line[key][pe] is None:
            by_line[key][pe] = it.value

    kept: list[str] = []
    for key in order:
        vals = [by_line[key].get(pe) for pe, _ in cols]
        if all(v is None for v in vals):
            continue
        raw = labels[key].lower()
        if raw in ("calculation type", "currency", "filing date"):
            continue
        kept.append(key)
    if not kept:
        return []

    # Prefer trend-key order when filtering to keep_keys
    if keep_keys is not None:
        preferred = [k for k in _QUARTER_TREND_KEYS if k in kept]
        rest = [k for k in kept if k not in preferred]
        kept = preferred + rest

    period_labs = [lab for _, lab in cols]
    lines = [
        f"### {title}",
        "",
        footnote,
        "",
        "| Line item | " + " | ".join(period_labs) + " |",
        "|---|" + "|".join(["---:"] * len(cols)) + "|",
    ]
    for key in kept:
        cells = [_fmt_cell(by_line[key].get(pe), line_item=key) for pe, _ in cols]
        lab = labels[key].replace("|", "/")
        lines.append(f"| {lab} | " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def statement_table_markdown(
    items: list[FinancialLineItem],
    statement: str,
    *,
    max_periods: int = 8,
    title: str | None = None,
    include_quarters: bool = True,
    max_quarters: int = 8,
) -> list[str]:
    """One CIQ statement as GFM tables: annual years, then last-8 quarters when present."""
    rows = [i for i in items if (i.statement or "") == statement]
    if not rows:
        return []
    cols = _period_columns(rows, max_periods=max_periods, kind="annual")
    if not cols:
        cols = _period_columns(rows, max_periods=max_periods, kind="any")
    if not cols:
        return []

    head = title or _STATEMENT_TITLES.get(statement, statement.replace("_", " ").title())
    period_labs = [lab for _, lab in cols]
    annual_cols = _period_columns(rows, max_periods=max_periods, kind="annual")
    col_kind = "annual" if cols == annual_cols else None
    lines = _render_statement_grid(
        rows,
        cols,
        title=head,
        footnote=(
            f"_₹ millions unless noted · Capital IQ annual · "
            f"{period_labs[0]}–{period_labs[-1]}_"
            if col_kind == "annual"
            else f"_₹ millions unless noted · Capital IQ as uploaded · "
            f"{period_labs[0]}–{period_labs[-1]}_"
        ),
        kind=col_kind,
    )
    if not lines:
        return []

    if include_quarters:
        q_cols = _period_columns(rows, max_periods=max_quarters, kind="quarterly")
        if len(q_cols) >= 2:
            q_labs = [lab for _, lab in q_cols]
            # Full statement for IS/BS/CF; key-line trend for ratios/key_stats/segments
            if statement in ("income_statement", "balance_sheet", "cash_flow"):
                q_block = _render_statement_grid(
                    rows,
                    q_cols,
                    title=f"{head} — last {len(q_cols)} quarters",
                    footnote=(
                        f"_₹ millions · quarterly columns as uploaded · "
                        f"{q_labs[0]}–{q_labs[-1]}_"
                    ),
                    kind="quarterly",
                )
            else:
                present = {
                    (it.line_item or "")
                    for it in rows
                    if (it.line_item or "") in _QUARTER_TREND_KEYS
                }
                q_block = _render_statement_grid(
                    rows,
                    q_cols,
                    title=f"{head} — last {len(q_cols)} quarters (key lines)",
                    footnote=(
                        f"_₹ millions · quarterly · {q_labs[0]}–{q_labs[-1]}_"
                    ),
                    keep_keys=present or None,
                    kind="quarterly",
                )
            if q_block:
                lines.extend(q_block)
        elif len(q_cols) == 1:
            # Single stub — still show, labelled clearly
            q_labs = [lab for _, lab in q_cols]
            q_block = _render_statement_grid(
                rows,
                q_cols,
                title=f"{head} — latest interim / quarter",
                footnote=f"_Only one quarterly/interim column on file ({q_labs[0]})._",
                kind="quarterly",
            )
            if q_block:
                lines.extend(q_block)

    return lines


def quarterly_trend_markdown(
    session: ResearchSession,
    *,
    max_quarters: int = 8,
) -> list[str]:
    """Compact key P&L / cash trend across last N quarters when CIQ has them."""
    items = list(session.financial_line_items or [])
    if not items:
        return []
    # Prefer income statement; fall back to any items with quarterly cols
    is_rows = [i for i in items if (i.statement or "") == "income_statement"]
    cf_rows = [i for i in items if (i.statement or "") == "cash_flow"]
    base = is_rows or items
    q_cols = _period_columns(base, max_periods=max_quarters, kind="quarterly")
    if len(q_cols) < 2:
        return []
    present = {(it.line_item or "") for it in (is_rows + cf_rows) if it.line_item}
    keep = {k for k in _QUARTER_TREND_KEYS if k in present}
    if not keep:
        return []
    q_labs = [lab for _, lab in q_cols]
    nq = len(q_cols)
    return _render_statement_grid(
        is_rows + cf_rows,
        q_cols,
        title=f"Key financials — last {nq} quarters (Capital IQ)",
        footnote=(
            f"_₹ millions · quarterly trend · {q_labs[0]}–{q_labs[-1]}. "
            "Shown when the CIQ Financials upload includes quarterly columns._"
        ),
        keep_keys=keep,
        kind="quarterly",
    )


def estimates_tables_markdown(session: ResearchSession) -> list[str]:
    """CIQ Estimates consensus as tables (mean; forward high/low/n)."""
    metrics = list(session.consensus_metrics or [])
    if not metrics:
        return []

    by_metric: dict[str, list[ConsensusMetric]] = defaultdict(list)
    for cm in metrics:
        by_metric[cm.metric].append(cm)

    # Metric order
    names = [m for m in _EST_PRIORITY if m in by_metric]
    names += sorted(m for m in by_metric if m not in names)

    periods = sorted({cm.period for cm in metrics if cm.period})
    if not periods:
        return []

    lines: list[str] = [
        "### Consensus estimates (Capital IQ)",
        "",
        "_Mean estimates, ₹ mn / stated units. Forward years have no actual yet._",
        "",
    ]

    # Mean table (all periods)
    lines.append("#### Consensus mean by period")
    lines.append("")
    lines.append("| Metric | " + " | ".join(periods) + " |")
    lines.append("|---|" + "|".join(["---:"] * len(periods)) + "|")
    for metric in names:
        by_p = {cm.period: cm for cm in by_metric[metric]}
        cells = []
        for p in periods:
            cm = by_p.get(p)
            if not cm or cm.mean is None:
                cells.append("—")
            else:
                cells.append(_fmt_cell(cm.mean, line_item=metric.lower()))
        lines.append(f"| {metric} | " + " | ".join(cells) + " |")
    lines.append("")

    # Forward detail: mean / high / low / n
    fwd_periods = sorted(
        {
            cm.period
            for cm in metrics
            if cm.actual is None and cm.period
        }
    )
    if fwd_periods:
        lines.append("#### Forward estimates detail (mean · high · low · n)")
        lines.append("")
        lines.append("| Metric | " + " | ".join(fwd_periods) + " |")
        lines.append("|---|" + "|".join(["---:"] * len(fwd_periods)) + "|")
        for metric in names:
            rows = [cm for cm in by_metric[metric] if cm.actual is None]
            if not rows:
                continue
            by_p = {cm.period: cm for cm in rows}
            cells = []
            for p in fwd_periods:
                cm = by_p.get(p)
                if not cm or cm.mean is None:
                    cells.append("—")
                    continue
                bits = [_fmt_cell(cm.mean, line_item=metric.lower())]
                if cm.high is not None or cm.low is not None:
                    bits.append(
                        f"{_fmt_cell(cm.low, line_item=metric.lower())}–"
                        f"{_fmt_cell(cm.high, line_item=metric.lower())}"
                    )
                if cm.num_estimates:
                    bits.append(f"n={cm.num_estimates}")
                cells.append(" · ".join(bits))
            lines.append(f"| {metric} | " + " | ".join(cells) + " |")
        lines.append("")

    # Actual vs final consensus for completed years
    past = [cm for cm in metrics if cm.actual is not None]
    past_periods = sorted({cm.period for cm in past if cm.period})
    if past_periods:
        lines.append("#### Reported vs final consensus (actual / mean · surprise)")
        lines.append("")
        lines.append("| Metric | " + " | ".join(past_periods) + " |")
        lines.append("|---|" + "|".join(["---:"] * len(past_periods)) + "|")
        for metric in names:
            rows = [cm for cm in by_metric[metric] if cm.actual is not None]
            if not rows:
                continue
            by_p = {cm.period: cm for cm in rows}
            cells = []
            for p in past_periods:
                cm = by_p.get(p)
                if not cm or cm.actual is None:
                    cells.append("—")
                    continue
                surprise = (cm.actual / cm.mean - 1) if cm.mean else None
                sur = f" ({surprise:+.1%})" if surprise is not None else ""
                cells.append(
                    f"{_fmt_cell(cm.actual, line_item=metric.lower())} / "
                    f"{_fmt_cell(cm.mean, line_item=metric.lower())}{sur}"
                )
            lines.append(f"| {metric} | " + " | ".join(cells) + " |")
        lines.append("")

    # Target / recommendation from estimates summary
    try:
        from iis.finance.multiples import get_estimates_summary, get_multiples_table

        est = get_estimates_summary(session)
        if est.get("target_price_mean") or est.get("consensus_recommendation"):
            lines.append("#### Street target & recommendation (CIQ)")
            lines.append("")
            lines.append("| Field | Value |")
            lines.append("|---|---|")
            if est.get("target_price_mean") is not None:
                lines.append(f"| Target price (mean) | ₹{float(est['target_price_mean']):,.0f} |")
            if est.get("target_price_median") is not None:
                lines.append(f"| Target price (median) | ₹{float(est['target_price_median']):,.0f} |")
            if est.get("target_price_num_estimates") is not None:
                lines.append(f"| # target estimates | {est['target_price_num_estimates']} |")
            if est.get("consensus_recommendation"):
                lines.append(f"| Consensus recommendation | {est['consensus_recommendation']} |")
            lines.append("")
        mult = get_multiples_table(session)
        if isinstance(mult, dict) and mult:
            periods_m = sorted(mult.keys())
            metric_keys = sorted({k for row in mult.values() if isinstance(row, dict) for k in row})
            if periods_m and metric_keys:
                lines.append("#### Trading multiples (CIQ Estimates)")
                lines.append("")
                lines.append("| Multiple | " + " | ".join(str(p) for p in periods_m) + " |")
                lines.append("|---|" + "|".join(["---:"] * len(periods_m)) + "|")
                for mk in metric_keys:
                    cells = []
                    for p in periods_m:
                        row = mult.get(p) or {}
                        v = row.get(mk) if isinstance(row, dict) else None
                        if v is None:
                            cells.append("—")
                        else:
                            try:
                                cells.append(f"{float(v):.1f}x")
                            except (TypeError, ValueError):
                                cells.append(str(v))
                    lines.append(f"| {mk} | " + " | ".join(cells) + " |")
                lines.append("")
    except Exception:
        pass

    return lines


def _segment_axis_tables(
    items: list[FinancialLineItem],
    *,
    max_periods: int = 8,
) -> list[str]:
    """Render Business vs Geographic segment blocks as separate tables."""
    segs = [i for i in items if (i.statement or "") == "segments"]
    if not segs:
        return []

    def _axis_of(it: FinancialLineItem) -> str:
        raw = (it.line_item_raw or "").lower()
        if "geographic" in raw:
            return "Geographic"
        if "business" in raw or "product" in raw or "segment ·" in raw:
            return "Business"
        return "Other"

    lines: list[str] = []
    for axis, title in (
        ("Business", "Business segments (Capital IQ)"),
        ("Geographic", "Geographic segments (Capital IQ)"),
    ):
        axis_items = [i for i in segs if _axis_of(i) == axis]
        if not axis_items:
            continue
        # Prefer Revenues first — clone items under a synthetic filter via statement_table
        # by temporarily using line_item_raw ordering in grid. Build per-metric subsections.
        metrics_order = (
            "Revenues",
            "EBITDA",
            "EBIT",
            "Operating Income",
            "Net Profit Before Tax",
            "Net Profit After Tax",
            "Assets",
            "Capital Expenditure",
            "Depreciation & Amortization",
            "Interest Expense",
            "Tax Expense",
        )
        axis_lines: list[str] = [f"### {title}", ""]
        any_block = False
        for metric in metrics_order:
            # Exact "· Metric)" suffix — avoid EBIT matching EBITDA
            suffix = f"· {metric})".lower()
            subset = [
                i
                for i in axis_items
                if (i.line_item_raw or "").lower().rstrip().endswith(suffix)
            ]
            if not subset:
                continue
            # Render via grid with friendly labels (strip axis·metric suffix for row name)
            cols = _period_columns(subset, max_periods=max_periods, kind="annual")
            if not cols:
                cols = _period_columns(subset, max_periods=max_periods, kind="any")
            if not cols:
                continue

            # Remap display labels to bare segment/region names
            remapped: list[FinancialLineItem] = []
            for it in subset:
                raw = it.line_item_raw or ""
                name = raw
                if "(" in raw:
                    name = raw.split("(", 1)[0].strip()
                # skip Totals-only noise? keep totals
                remapped.append(
                    it.model_copy(update={"line_item_raw": name, "line_item": name.lower().replace(" ", "_")})
                    if hasattr(it, "model_copy")
                    else it
                )
            # Fall back without model_copy
            if remapped and remapped[0] is subset[0] and not hasattr(subset[0], "model_copy"):
                remapped = subset

            block = _render_statement_grid(
                remapped if remapped else subset,
                cols,
                title=f"{axis} · {metric}",
                footnote=(
                    f"_₹ millions · {axis.lower()} {metric.lower()} · "
                    f"{cols[0][1]}–{cols[-1][1]}_"
                ),
                kind="annual" if cols == _period_columns(subset, max_periods=max_periods, kind="annual") else None,
            )
            if block:
                # Downgrade ### to #### under axis heading
                if block and block[0].startswith("### "):
                    block[0] = "#" + block[0]
                axis_lines.extend(block)
                any_block = True
        if any_block:
            lines.extend(axis_lines)
            lines.append("")
    return lines


def financials_as_uploaded_markdown(
    session: ResearchSession,
    *,
    statements: Iterable[str] | None = None,
    max_periods: int = 8,
    compact: bool = False,
) -> list[str]:
    """Full CIQ statement dump for report Layer 1 / appendix."""
    items = list(session.financial_line_items or [])
    if not items:
        return []
    order = list(
        statements
        or (
            ("income_statement", "balance_sheet", "cash_flow")
            if compact
            else (
                "income_statement",
                "balance_sheet",
                "cash_flow",
                "ratios",
                "key_stats",
                "segments",
                "historical_capitalization",
                "capital_structure",
            )
        )
    )
    lines: list[str] = [
        "## Capital IQ financials (as uploaded)",
        "",
        "_Tables below mirror Capital IQ Financials workbook line items (₹ millions). "
        "Annual years first; last-8-quarter tables appear when quarterly columns are in the upload. "
        "Business + Geographic segment mixes come from the Segments tab._",
        "",
    ]
    n_before = len(lines)
    # Compact N-quarter key-line trend up front when available
    if not compact:
        q_trend = quarterly_trend_markdown(session, max_quarters=max_periods)
        if q_trend:
            lines.extend(q_trend)
    for st in order:
        if st == "segments" and not compact:
            seg_blocks = _segment_axis_tables(items, max_periods=max_periods)
            if seg_blocks:
                lines.extend(seg_blocks)
                continue
        block = statement_table_markdown(
            items,
            st,
            max_periods=max_periods if not compact else min(6, max_periods),
            include_quarters=not compact,
            max_quarters=max_periods,
        )
        if block:
            lines.extend(block)
    if len(lines) == n_before:
        return []
    return lines


def brokerage_report_sections_markdown(session: ResearchSession) -> list[str]:
    """One subsection per uploaded brokerage report (tables + STREET matrix)."""
    lines: list[str] = []
    broker_files: list[str] = []
    try:
        from iis.finance.report_enrichment import _brokerage_filenames

        broker_files = sorted(_brokerage_filenames(session))
    except Exception:
        broker_files = []
    if not broker_files:
        # Fall back to pdf_documents tagged brokerage
        for doc in session.pdf_documents or []:
            dt = (doc.doc_type or "").lower()
            if "broker" in dt:
                broker_files.append(doc.file)
        broker_files = sorted(set(broker_files))
    if not broker_files:
        return []

    lines.extend(
        [
            "## Brokerage research (uploaded reports)",
            "",
            f"_{len(broker_files)} brokerage PDF(s) on file. Figures below are STREET views — "
            "CIQ remains authoritative for ₹._",
            "",
        ]
    )

    # Street estimate matrix (all brokers)
    try:
        from iis.finance.brokerage_view import brokerage_view_markdown

        matrix = brokerage_view_markdown(session)
        if matrix:
            lines.append("### Broker estimates vs CIQ consensus")
            lines.append("")
            lines.extend(matrix)
    except Exception:
        pass

    # Hive structured broker narrative if present
    try:
        from iis.finance.broker_pipeline import structured_broker_markdown

        art = next(
            (a for a in session.agent_artifacts if a.artifact_type == "brokerage_view"),
            None,
        )
        struct = (art.payload or {}).get("structured_broker") if art else None
        hive_md = structured_broker_markdown(struct if isinstance(struct, dict) else None)
        if hive_md:
            lines.append("### Structured STREET read")
            lines.append("")
            lines.extend(hive_md)
            lines.append("")
    except Exception:
        pass

    # Per-file sections
    try:
        from iis.finance.report_enrichment import brokerage_research_markdown

        body = brokerage_research_markdown(session, max_reports=12)
        if body and not any("No brokerage PDFs matched" in (ln or "") for ln in body[:3]):
            lines.append("### Broker report excerpts")
            lines.append("")
            lines.extend(body)
    except Exception:
        pass

    # Explicit file index table
    lines.append("### Brokerage files on session")
    lines.append("")
    lines.append("| # | File |")
    lines.append("|---:|---|")
    for i, f in enumerate(broker_files, 1):
        from pathlib import Path

        lines.append(f"| {i} | `{Path(f).name}` |")
    lines.append("")
    return lines


def ciq_appendix_markdown(session: ResearchSession) -> str:
    """Fallback appendix when report_finalize is unavailable."""
    parts: list[str] = [
        "",
        "---",
        "",
        "# Appendix — Capital IQ & brokerage tables",
        "",
        "_Python-rendered from uploads. Do not treat narrative as overriding these tables._",
        "",
    ]
    fin = financials_as_uploaded_markdown(session, compact=False, max_periods=8)
    if fin:
        parts.extend(fin)
    est = estimates_tables_markdown(session)
    if est:
        parts.append("## Capital IQ estimates")
        parts.append("")
        parts.extend(est)
    try:
        from iis.finance.metric_series import build_capex_analysis, format_capex_analysis

        capex = build_capex_analysis(session.financial_line_items or [], recent_years=8)
        if capex:
            parts.append("## Capex (Capital IQ)")
            parts.append("")
            parts.append(format_capex_analysis(capex))
            parts.append("")
    except Exception:
        pass
    try:
        from iis.finance.peer_timeline import peer_timeline_markdown

        peer = peer_timeline_markdown(session)
        if peer:
            parts.extend(peer)
    except Exception:
        pass
    br = brokerage_report_sections_markdown(session)
    if br:
        parts.extend(br)
    body = "\n".join(parts).rstrip() + "\n"
    if body.count("\n") < 12:
        return ""
    return body
