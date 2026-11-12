"""Offline analysis pack: deterministic Python → analysis_pack.md → chat ground truth.

No internet. Regenerated from Capital IQ / session artifacts after ingest or analyze.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iis.config import settings
from iis.finance.checklist import (
    FLAG_CATEGORY_TO_BUCKET,
    checklist_summary_markdown,
    load_checklist_profile,
)
from iis.finance.flags import compute_data_flags, format_flags_for_chat
from iis.finance.formulas import formulas_markdown
from iis.finance.growth import build_growth_summary
from iis.finance.metric_series import build_capex_analysis, format_capex_analysis, get_metric_series
from iis.finance.metrics import get_key_metrics_summary
from iis.finance.multiples import get_estimates_summary, get_multiples_table
from iis.finance.quality_scores import compute_optional_quality_pack, format_quality_scores
from iis.finance.ratios import compute_all_ratios, format_ratio_value
from iis.models.enums import GateId
from iis.models.session import ResearchSession
from iis.parsers.capital_iq.financials import get_latest_actual

PACK_FILENAME = "analysis_pack.md"


def analysis_pack_path(session: ResearchSession) -> Path | None:
    key = session.company_key
    if not key:
        return None
    return settings.data_dir / "companies" / key / "reports" / PACK_FILENAME


def load_analysis_pack(session: ResearchSession) -> str | None:
    path = analysis_pack_path(session)
    if not path or not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def write_analysis_pack(session: ResearchSession) -> Path | None:
    """Build and persist analysis_pack.md under the company library."""
    path = analysis_pack_path(session)
    if not path:
        # Fall back to session-scoped staging when company_key missing
        path = settings.data_dir / "drive_staging" / (session.id[:12]) / PACK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    md = build_analysis_pack_markdown(session)
    path.write_text(md, encoding="utf-8")
    return path


def build_analysis_pack_markdown(session: ResearchSession) -> str:
    """Pure-Python research pack from uploaded data only."""
    profile = load_checklist_profile()
    company = session.company.name if session.company else "Unknown"
    bse = session.company.bse_code if session.company else None
    nse = session.company.nse_ticker if session.company else None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        f"# Analysis pack — {company}",
        "",
        f"_Generated {now} from uploaded Capital IQ / PDFs only (no live web)._",
        f"_Session `{session.id[:8]}…` · key `{session.company_key or '—'}` · BSE {bse or '—'} · NSE {nse or '—'}_",
        f"_Checklist profile: **{profile.name}**_",
        "",
        "## How to use",
        "Chat should answer from this pack first. If a fact is missing here, say so, then use local PDFs or (only if enabled) external sources.",
        "Tune buckets/thresholds in `~/.iis/checklist_profile.json`.",
        "",
    ]

    # Precompute flags once for checklist counts + flags section
    flag_objs = compute_data_flags(session, profile=profile) if session.financial_line_items or session.guidance_records else []
    flag_counts: dict[str, int] = {}
    for f in flag_objs:
        if f.severity != "review":
            continue
        bucket = FLAG_CATEGORY_TO_BUCKET.get(f.category, f.category)
        flag_counts[bucket] = flag_counts.get(bucket, 0) + 1

    builders: list[tuple[str, Any]] = [
        ("sources", lambda: _section_sources(session)),
        ("checklist", lambda: checklist_summary_markdown(profile, flag_counts=flag_counts)),
        ("headline", lambda: _section_headline(session)),
        ("pnl", lambda: _section_pnl(session)),
        ("ratios", lambda: _section_ratios(session, profile)),
        ("capex", lambda: _section_capex(session)),
        ("growth", lambda: _section_growth(session)),
        ("estimates", lambda: _section_estimates(session)),
        ("flags", lambda: _section_flags(session, flag_objs=flag_objs)),
        ("quality_scores", lambda: _section_quality_scores(session, profile)),
        ("valuation", lambda: _section_valuation(session)),
        ("capacity", lambda: _section_capacity_excerpts(session)),
        ("formulas", lambda: formulas_markdown(profile.highlight_ratios)),
    ]
    for key, builder in builders:
        if profile.section_enabled(key):
            lines.extend(builder())

    lines.append("")
    lines.append("---")
    lines.append("_End of analysis pack._")
    return "\n".join(lines)


def _section_ratios(session: ResearchSession, profile: Any) -> list[str]:
    lines = ["## Key ratios (CIQ)", ""]
    if not session.financial_line_items:
        lines.append("_No financials._")
        lines.append("")
        return lines
    payload = compute_all_ratios(session.financial_line_items)
    latest = payload.get("latest_ratios") or {}
    period = payload.get("latest_period") or "—"
    lines.append(f"_Period: **{period}**_")
    lines.append("")
    lines.append("| Ratio | Value | Formula |")
    lines.append("|---|---:|---|")
    from iis.finance.ratios import RATIO_CATALOG

    for key in profile.highlight_ratios:
        meta = RATIO_CATALOG.get(key) or {}
        val = latest.get(key)
        if val is None:
            continue
        label = meta.get("label") or key
        formula = meta.get("formula") or ""
        lines.append(f"| {label} | {format_ratio_value(key, val)} | `{formula}` |")
    if len(lines) <= 5:
        lines.append("_No highlight ratios available for latest period._")
    lines.append("")
    return lines


def _section_quality_scores(session: ResearchSession, profile: Any) -> list[str]:
    lines = ["## Quality scores (optional)", ""]
    if not profile.enable_quality_scores:
        lines.append("_Disabled in checklist profile._")
        lines.append("")
        return lines
    if not session.financial_line_items:
        lines.append("_No financials._")
        lines.append("")
        return lines
    payload = compute_optional_quality_pack(session.financial_line_items)
    if not payload:
        lines.append("_Need at least two fiscal years of CIQ actuals._")
        lines.append("")
        return lines
    lines.append(format_quality_scores(payload))
    lines.append("")
    return lines


def _section_sources(session: ResearchSession) -> list[str]:
    lines = ["## Sources on file", ""]
    files = session.uploaded_files or []
    if not files and session.pdf_documents:
        files = [d.file for d in session.pdf_documents]
    if not files:
        lines.append("_No files listed on session yet._")
    else:
        for f in files[:40]:
            name = Path(f).name
            lines.append(f"- {name}")
    lines.append("")
    lines.append(
        f"- Financial line items: **{len(session.financial_line_items)}** · "
        f"PDF chunks: **{len(session.pdf_chunks)}** · "
        f"Consensus rows: **{len(session.consensus_metrics)}**"
    )
    lines.append("")
    return lines


def _section_headline(session: ResearchSession) -> list[str]:
    m = get_key_metrics_summary(session.financial_line_items) if session.financial_line_items else {}
    lines = ["## Headline metrics", ""]
    if not m:
        lines.append("_No financials parsed yet — upload Capital IQ Financials._")
        lines.append("")
        return lines
    if m.get("revenue_mn") is not None:
        lines.append(f"- Latest revenue: **₹{m['revenue_mn']:,.0f} mn**")
    if m.get("ebitda_margin") is not None:
        lines.append(f"- EBITDA margin: **{m['ebitda_margin']:.1%}**")
    if m.get("revenue_cagr_3y") is not None:
        lines.append(f"- 3Y revenue CAGR: **{m['revenue_cagr_3y']:.1%}**")
    est = get_estimates_summary(session)
    if est.get("target_price_mean"):
        lines.append(f"- Consensus target: **₹{est['target_price_mean']:,.0f}**")
    lines.append("")
    return lines


def _latest_row(session: ResearchSession, canonical: str) -> str:
    item = get_latest_actual(session.financial_line_items, canonical)
    if not item or item.value is None:
        return "—"
    return f"{item.value:,.1f} ({item.period_label})"


def _section_pnl(session: ResearchSession) -> list[str]:
    lines = ["## P&L / cash (latest actuals, ₹ mn)", ""]
    if not session.financial_line_items:
        lines.append("_No line items._")
        lines.append("")
        return lines
    specs = [
        ("total_revenue", "Revenue"),
        ("gross_profit", "Gross profit"),
        ("ebitda", "EBITDA"),
        ("ebit", "EBIT"),
        ("net_income", "Net income"),
        ("free_cash_flow", "Free cash flow"),
        ("capital_expenditure", "Capex"),
        ("total_debt", "Total debt"),
        ("cash_and_equivalents", "Cash"),
    ]
    lines.append("| Metric | Latest actual |")
    lines.append("|---|---|")
    for key, label in specs:
        lines.append(f"| {label} | {_latest_row(session, key)} |")
    lines.append("")

    # Short multi-year revenue / ebitda
    for metric, title in (("total_revenue", "Revenue history"), ("ebitda", "EBITDA history")):
        series = get_metric_series(session.financial_line_items, metric, max_points=6)
        if not series:
            continue
        lines.append(f"### {title}")
        lines.append("")
        for p in series:
            lines.append(f"- {p.period_label}: **{p.value_mn:,.0f}**")
        lines.append("")
    return lines


def _section_capex(session: ResearchSession) -> list[str]:
    lines = ["## Capex analysis", ""]
    if not session.financial_line_items:
        lines.append("_No financials._")
        lines.append("")
        return lines
    payload = build_capex_analysis(session.financial_line_items, recent_years=3)
    if not payload:
        lines.append("_Capex line not found in Capital IQ export (check cash-flow sheet)._")
        lines.append("")
        return lines
    lines.append(format_capex_analysis(payload))
    lines.append("")
    return lines


def _section_growth(session: ResearchSession) -> list[str]:
    lines = ["## Revenue growth", ""]
    if not session.financial_line_items:
        lines.append("_No financials._")
        lines.append("")
        return lines
    summary = build_growth_summary(session.financial_line_items)
    actuals = summary.get("historical") or []
    if not actuals:
        lines.append("_No revenue series._")
        lines.append("")
        return lines
    for p in actuals[-6:]:
        yoy = f", YoY {p.yoy_growth:.1%}" if p.yoy_growth is not None else ""
        lines.append(f"- {p.period_label}: **{p.value_mn:,.0f}**{yoy}")
    if summary.get("cagr_3y") is not None:
        lines.append(f"- 3Y CAGR: **{summary['cagr_3y']:.1%}**")
    if summary.get("cagr_5y") is not None:
        lines.append(f"- 5Y CAGR: **{summary['cagr_5y']:.1%}**")
    lines.append("")
    return lines


def _section_estimates(session: ResearchSession) -> list[str]:
    lines = ["## Estimates / multiples", ""]
    est = get_estimates_summary(session)
    if est.get("target_price_mean"):
        lines.append(f"- Target price (mean): **₹{est['target_price_mean']:,.0f}**")
    if est.get("target_price_median"):
        lines.append(f"- Target price (median): **₹{est['target_price_median']:,.0f}**")
    table = get_multiples_table(session)
    if table:
        lines.append("")
        lines.append("| Period | P/E | EV/EBITDA |")
        lines.append("|---|---:|---:|")
        for period, data in list(table.items())[:8]:
            pe = f"{data['pe']:.1f}x" if data.get("pe") else "—"
            ev = f"{data['ev_ebitda']:.1f}x" if data.get("ev_ebitda") else "—"
            lines.append(f"| {period} | {pe} | {ev} |")
    if len(lines) == 2:
        lines.append("_No Estimates workbook loaded._")
    lines.append("")
    return lines


def _section_flags(session: ResearchSession, *, flag_objs: list[Any] | None = None) -> list[str]:
    lines = ["## Data flags", ""]
    if flag_objs is not None:
        lines.append(format_flags_for_chat(flag_objs))
        lines.append("")
        return lines
    art = next((a for a in session.agent_artifacts if a.artifact_type == "data_flags"), None)
    if art:
        flags = art.payload.get("flags") or []
        from iis.finance.flags import DataFlag

        objs = []
        for f in flags:
            try:
                objs.append(
                    DataFlag(
                        id=f.get("id") or "flag",
                        category=f.get("category") or "other",
                        severity=f.get("severity") or "review",
                        headline=f.get("headline") or "Flag",
                        explanation=f.get("explanation") or "",
                        figures=f.get("figures") or [],
                        verify=f.get("verify") or [],
                    )
                )
            except Exception:
                continue
        lines.append(format_flags_for_chat(objs))
    else:
        lines.append(format_flags_for_chat(compute_data_flags(session)))
    lines.append("")
    return lines


def _section_valuation(session: ResearchSession) -> list[str]:
    lines = ["## Valuation (session)", ""]
    v = session.valuation
    if not v or v.intrinsic_value_per_share is None:
        lines.append("_DCF not run yet — approve Financials, then Run Analysis._")
        lines.append("")
        return lines
    gate4 = session.is_gate_approved(GateId.VALUATION_ASSUMPTIONS)
    lines.append(f"- Intrinsic value: **₹{v.intrinsic_value_per_share:,.0f}/sh**")
    if v.market_price:
        lines.append(f"- Market price: **₹{v.market_price:,.2f}/sh** ({v.market_price_source})")
    if v.margin_of_safety_pct is not None:
        lines.append(f"- MoS: **{v.margin_of_safety_pct:.1%}**")
    lines.append(f"- Gate 4 valuation: **{'approved' if gate4 else 'pending'}**")
    if session.assumptions:
        lines.append("")
        lines.append("### DCF assumptions")
        for a in session.assumptions:
            val = a.user_value if a.user_value is not None else a.value
            if a.unit == "decimal" and isinstance(val, (int, float)):
                shown = f"{float(val):.1%}"
            else:
                shown = str(val)
            lines.append(f"- {a.label}: **{shown}**")
    lines.append("")
    return lines


def _section_capacity_excerpts(session: ResearchSession) -> list[str]:
    """Keyword harvest from PDFs — no LLM. Helps utilization / plant questions."""
    lines = ["## Capacity / utilization excerpts (PDF keyword hits)", ""]
    if not session.pdf_chunks:
        lines.append("_No PDF chunks indexed._")
        lines.append("")
        return lines
    keys = (
        "capacity utilization",
        "utilisation",
        "utilization",
        "installed capacity",
        "plant capacity",
        "production capacity",
        "capacity expansion",
    )
    hits: list[str] = []
    for chunk in session.pdf_chunks:
        text = (chunk.text or "").lower()
        if not any(k in text for k in keys):
            continue
        snippet = (chunk.text or "").strip().replace("\n", " ")
        if len(snippet) > 420:
            idx = min((text.find(k) for k in keys if k in text), default=0)
            start = max(0, idx - 80)
            snippet = snippet[start : start + 420] + "…"
        hits.append(f"- [{Path(chunk.file).name} p.{chunk.page}] {snippet}")
        if len(hits) >= 8:
            break
    if not hits:
        lines.append("_No capacity/utilization phrases found in indexed PDFs._")
    else:
        lines.extend(hits)
    lines.append("")
    return lines


SECTION_HINTS: dict[str, tuple[str, ...]] = {
    "Analysis checklist": ("checklist", "bucket", "earnings quality", "profile"),
    "Capex analysis": ("capex", "capital expenditure", "capital spend", "capex/sales"),
    "Headline metrics": ("revenue", "ebitda margin", "cagr", "headline"),
    "P&L / cash (latest actuals, ₹ mn)": (
        "gross profit",
        "net income",
        "free cash",
        "fcf",
        "debt",
        "ebitda",
        "ebit",
        "p&l",
        "pnl",
    ),
    "Key ratios (CIQ)": ("ratio", "roe", "roa", "margin", "coverage", "turnover"),
    "Revenue growth": ("growth", "cagr", "yoy"),
    "Estimates / multiples": ("target", "p/e", "pe ", "multiple", "consensus", "ntm"),
    "Data flags": ("flag", "red flag", "anomal", "forensic"),
    "Quality scores (optional)": ("piotroski", "f-score", "quality score", "composite"),
    "Formula definitions (transparent)": ("formula", "definition", "how computed"),
    "Valuation (session)": ("intrinsic", "dcf", "margin of safety", "mos", "fair value", "wacc"),
    "Capacity / utilization excerpts (PDF keyword hits)": (
        "capacity",
        "utilization",
        "utilisation",
        "plant",
        "installed",
    ),
}


def extract_pack_context(pack_md: str, question: str, *, max_chars: int = 6000) -> str:
    """Pull the most relevant markdown sections for a question (no LLM)."""
    if not pack_md or not question:
        return pack_md[:max_chars] if pack_md else ""
    lower = question.lower()
    # Split on ## headings
    parts = re.split(r"\n(?=## )", pack_md)
    scored: list[tuple[int, str]] = []
    for part in parts:
        head = part.split("\n", 1)[0].lstrip("# ").strip()
        score = 0
        for section, hints in SECTION_HINTS.items():
            if section.lower() in head.lower() or head.lower() in section.lower():
                if any(h in lower for h in hints):
                    score += 5
        for h in re.findall(r"[a-z0-9%]{3,}", lower):
            if h in part.lower():
                score += 1
        if score:
            scored.append((score, part))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        # Return head + flags + capex as default context
        return pack_md[:max_chars]
    out = "\n\n".join(p for _, p in scored[:4])
    return out[:max_chars]


def answer_from_pack_deterministic(session: ResearchSession, question: str) -> dict[str, Any] | None:
    """
    If the question maps cleanly to a coded module, return answer text without LLM.
    """
    lower = question.lower()
    # Capex history / intensity — not DCF assumption edits
    if any(k in lower for k in ("capex", "capital expenditure", "capital spend")) and not _is_assumption_edit(lower):
        payload = build_capex_analysis(session.financial_line_items, recent_years=3)
        if payload:
            return {
                "answer": format_capex_analysis(payload),
                "query_type": "capex_analysis",
                "source": "analysis_pack_module",
            }
    if any(k in lower for k in ("data flag", "red flag", "anomal", "forensic")):
        return {
            "answer": format_flags_for_chat(compute_data_flags(session)),
            "query_type": "data_flags",
            "source": "analysis_pack_module",
        }
    if any(k in lower for k in ("capacity utilization", "capacity utilisation", "utilization", "utilisation", "installed capacity")):
        pack = load_analysis_pack(session) or build_analysis_pack_markdown(session)
        ctx = extract_pack_context(pack, question)
        if "No capacity/utilization phrases found" in ctx and "Capacity / utilization" in ctx:
            return {
                "answer": (
                    "No capacity/utilization figures found in the analysis pack or PDF keyword scan. "
                    "Upload AR / investor presentations that mention plant capacity, or ask me to search "
                    "transcripts more broadly."
                ),
                "query_type": "capacity_utilization",
                "source": "analysis_pack",
            }
        # Return excerpts directly — fast, no LLM
        return {
            "answer": "**Capacity / utilization (from analysis pack excerpts):**\n\n" + ctx,
            "query_type": "capacity_utilization",
            "source": "analysis_pack",
        }
    return None


def _is_assumption_edit(lower: str) -> bool:
    return bool(
        re.search(r"\d+\s*%", lower)
        or any(k in lower for k in ("set ", "use ", "change assumption", "update assumption", "gate 4"))
    )
