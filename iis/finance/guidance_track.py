"""Guidance vs actuals + helpers for concall reflection.

Deterministic — no invented numbers. Actuals prefer Capital IQ Guidance sheet,
then backfill from Financials when the sheet Actual row is blank.
"""
from __future__ import annotations

import re
from typing import Any

from iis.finance.flags import (
    _financials_value_for_guidance,
    _normalize_guidance_metric,
    _fmt_mn,
    _fmt_pct,
    _is_growth_metric,
    pivot_guidance,
)
from iis.models.artifacts import FinancialLineItem, GuidanceRecord


def guidance_track_rows(
    records: list[GuidanceRecord],
    items: list[FinancialLineItem] | None = None,
) -> list[dict[str, Any]]:
    """Pivoted guidance track with outcome vs actual (and optional financials backfill)."""
    items = items or []
    rows: list[dict[str, Any]] = []
    pivoted = pivot_guidance(records)
    ranges = {
        (_normalize_guidance_metric(r.metric), r.fiscal_year): (r.guidance_low, r.guidance_high)
        for r in records
        if r.guidance_low is not None and r.guidance_high is not None
    }

    _junk = re.compile(
        r"^(actual|actual/latest|days before actual|guidance date|consensus estimate|"
        r"as of|current fiscal year end)$",
        re.I,
    )

    for (_base, fy), bucket in sorted(pivoted.items(), key=lambda x: x[0][1], reverse=True):
        metric = bucket["metric"]
        if _junk.match((metric or "").strip()) or len((metric or "").strip()) < 3:
            continue
        guidance = bucket.get("guidance")
        consensus = bucket.get("consensus")
        actual = bucket.get("actual")
        actual_source = "guidance_sheet" if actual is not None else None

        if actual is None and items and guidance is not None:
            fin = _financials_value_for_guidance(items, metric, fy)
            if fin is not None:
                actual = fin
                actual_source = "financials"

        if guidance is None and actual is None and consensus is None:
            continue

        lo, hi = ranges.get((_base, fy), (None, None))
        spend = _is_spend_metric(metric, guidance, actual)
        outcome = None
        delta_label = None
        delta_value = None
        if guidance is not None and actual is not None:
            if _is_growth_metric(metric):
                delta_value = actual - guidance
                delta_label = f"{delta_value:+.1f} pp"
                outcome = "met/beat" if delta_value >= -2 else "missed"
            elif spend:
                if guidance == 0:
                    continue
                a, g = abs(actual), abs(guidance)
                band_lo, band_hi = (min(abs(lo), abs(hi)), max(abs(lo), abs(hi))) if lo is not None else (g, g)
                delta_value = (a - band_hi) / band_hi * 100 if a > band_hi else (a - band_lo) / band_lo * 100 if a < band_lo else 0.0
                if a > band_hi * 1.03:
                    outcome = "spent above guidance"
                elif a < band_lo * 0.97:
                    outcome = "spent below guidance"
                else:
                    outcome = "within guidance"
                delta_label = _fmt_pct(delta_value) + (" vs top of range" if lo is not None and a > band_hi else "")
            else:
                if guidance == 0:
                    continue
                floor = min(lo, hi) if lo is not None else guidance
                delta_value = ((actual - guidance) / abs(guidance)) * 100
                delta_label = _fmt_pct(delta_value)
                outcome = "met/beat" if actual >= floor - abs(floor) * 0.03 else "missed"

        def _g_fmt() -> str:
            f = _fmt_pct if _is_growth_metric(metric) else _fmt_mn
            if lo is not None and hi is not None:
                return f"{f(lo)} to {f(hi)}"
            return f(guidance)

        rows.append(
            {
                "metric": metric,
                "fiscal_year": fy,
                "guidance": guidance,
                "consensus": consensus,
                "actual": actual,
                "actual_source": actual_source,
                "outcome": outcome,
                "delta_label": delta_label,
                "delta_value": delta_value,
                "spend_metric": spend,
                "guidance_low": lo,
                "guidance_high": hi,
                "guidance_fmt": _g_fmt() if guidance is not None else "N/A",
                "actual_fmt": (
                    _fmt_pct(actual) if actual is not None and _is_growth_metric(metric) else _fmt_mn(actual)
                ),
                "consensus_fmt": (
                    _fmt_pct(consensus)
                    if consensus is not None and _is_growth_metric(metric)
                    else _fmt_mn(consensus)
                ),
            }
        )
    return rows


def _is_spend_metric(metric: str, guidance: float | None, actual: float | None) -> bool:
    if re.search(r"capex|capital expenditure|expense|cost|interest", metric or "", re.I):
        return True
    return guidance is not None and guidance < 0 and (actual is None or actual < 0)


def guidance_track_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    comparable = [r for r in rows if r.get("outcome")]
    perf = [r for r in comparable if not r.get("spend_metric")]
    spend = [r for r in comparable if r.get("spend_metric")]
    beats = sum(1 for r in perf if r["outcome"] == "met/beat")
    misses = sum(1 for r in perf if r["outcome"] == "missed")
    over = sum(1 for r in spend if r["outcome"] == "spent above guidance")
    parts = []
    if perf:
        parts.append(f"Management met or beat guidance in {beats} of {len(perf)} comparable year(s)")
    if spend:
        names = sorted({r["metric"] for r in spend})
        parts.append(f"{', '.join(names)} came in above guidance in {over} of {len(spend)} year(s)")
    return {
        "tracked": len(comparable),
        "beats": beats,
        "misses": misses,
        "spend_over": over,
        "headline": "; ".join(parts) if parts else "Not enough guidance vs actual pairs yet (need Estimates Guidance sheet).",
    }


def guidance_track_prompt_block(
    records: list[GuidanceRecord],
    items: list[FinancialLineItem] | None = None,
) -> str:
    """Compact text for LLM / report prompts."""
    rows = guidance_track_rows(records, items)
    if not rows:
        return "No Capital IQ guidance vs actual pairs available."
    summary = guidance_track_summary(rows)
    lines = [summary["headline"], ""]
    for r in rows[:12]:
        lines.append(
            f"- {r['fiscal_year']} {r['metric']}: guided {r['guidance_fmt']}, "
            f"actual {r['actual_fmt']}"
            + (f" ({r['outcome']}, {r['delta_label']})" if r.get("outcome") else "")
            + (f" [actual from {r['actual_source']}]" if r.get("actual_source") else "")
        )
    return "\n".join(lines)


def guidance_track_markdown(
    records: list[GuidanceRecord],
    items: list[FinancialLineItem] | None = None,
) -> list[str]:
    rows = guidance_track_rows(records, items)
    if not rows:
        return [
            "_No pivoted guidance vs actual pairs yet. Upload Capital IQ **Estimates** "
            "(Guidance sheet) to enable track-record comparison._",
            "",
        ]
    summary = guidance_track_summary(rows)
    out = [
        f"**Track record:** {summary['headline']}",
        "",
        "| Metric | FY | Guidance | Consensus | Actual | Outcome |",
        "|---|---|---:|---:|---:|---|",
    ]
    for r in rows[:20]:
        out.append(
            f"| {r['metric']} | {r['fiscal_year']} | {r['guidance_fmt']} | "
            f"{r['consensus_fmt']} | {r['actual_fmt']} | "
            f"{(r['outcome'] + (' (' + r['delta_label'] + ')' if r.get('delta_label') else '')) if r.get('outcome') else '—'} |"
        )
    out.append("")
    return out
