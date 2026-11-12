"""Python investor checklist + ratio-trend concerns for Hive.

Runs deterministic rules (critique checklists + multi-year ratio moves).
Hive sees elevated concerns with actual values and decides what to discuss;
CSR/remuneration noise stays out unless flagged here.
"""
from __future__ import annotations

from typing import Any

from iis.finance.ratios import (
    RATIO_CATALOG,
    build_period_metrics,
    compute_period_ratios,
    format_ratio_value,
)
from iis.models.artifacts import AgentArtifact
from iis.models.enums import SourceTier
from iis.models.session import ResearchSession

# Ratios tracked for trend / disproportion flags
_TREND_KEYS: list[tuple[str, str, float, bool]] = [
    # key, label hint, material YoY move threshold (abs), higher_is_worse for "bad" direction note
    ("gross_margin", "Gross margin", 0.03, False),       # 3pp
    ("ebitda_margin", "EBITDA margin", 0.025, False),
    ("ebit_margin", "EBIT margin", 0.025, False),
    ("net_margin", "Net margin", 0.02, False),
    ("roe", "ROE", 0.03, False),
    ("roce", "ROCE", 0.03, False),
    ("roa", "ROA", 0.02, False),
    ("current_ratio", "Current ratio", 0.25, False),
    ("debt_to_equity", "Debt / Equity", 0.2, True),
    ("net_debt_to_ebitda", "Net debt / EBITDA", 0.5, True),
    ("interest_coverage", "Interest coverage", 1.5, False),
    ("receivable_days", "Receivable days", 15.0, True),
    ("inventory_days", "Inventory days", 20.0, True),
    ("payable_days", "Payable days", 20.0, True),
    ("cfo_to_ni", "CFO / PAT", 0.25, False),
    ("fcf_margin", "FCF margin", 0.04, False),
    ("capex_to_sales", "Capex / Sales", 0.04, True),
]


def _fy_label(pe: str) -> str:
    if len(pe) >= 4 and pe[:4].isdigit():
        return f"FY{pe[:4][-2:]}"
    return pe[:10]


def _annual_periods(items, *, max_years: int = 8) -> list[str]:
    periods = build_period_metrics(items)
    ordered = sorted(periods.keys())
    annual = [p for p in ordered if len(p) >= 7 and p[5:7] in ("03", "12")]
    use = annual if annual else ordered
    return use[-max_years:]


def ratio_trend_table(session: ResearchSession, *, max_years: int = 6) -> dict[str, Any]:
    """Multi-year ratio grid + YoY / vs-prior-avg flags."""
    items = list(session.financial_line_items or [])
    if not items:
        return {"periods": [], "rows": [], "flags": []}

    periods = _annual_periods(items, max_years=max_years)
    if len(periods) < 2:
        return {"periods": periods, "rows": [], "flags": []}

    pm = build_period_metrics(items)
    by_pe = {pe: compute_period_ratios(pm[pe]) for pe in periods if pe in pm}
    labels = [_fy_label(p) for p in periods]

    rows: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []

    for key, title, thresh, higher_worse in _TREND_KEYS:
        values: list[float | None] = [by_pe.get(pe, {}).get(key) for pe in periods]
        if all(v is None for v in values):
            continue
        meta = RATIO_CATALOG.get(key) or {}
        label = meta.get("label") or title
        rows.append(
            {
                "key": key,
                "label": label,
                "values": values,
                "formatted": [format_ratio_value(key, v) if v is not None else "—" for v in values],
            }
        )

        # YoY on latest two non-null
        hist = [(i, v) for i, v in enumerate(values) if v is not None]
        if len(hist) < 2:
            continue
        i1, v1 = hist[-1]
        i0, v0 = hist[-2]
        delta = v1 - v0
        # For margins/returns treat as absolute; days/x same
        material = abs(delta) >= thresh
        if not material:
            # Also vs prior 3Y average when available
            prior = [v for _, v in hist[:-1]][-3:]
            if len(prior) >= 2:
                avg = sum(prior) / len(prior)
                delta_avg = v1 - avg
                if abs(delta_avg) >= thresh * 1.25:
                    material = True
                    delta = delta_avg
                    i0 = None  # mark as vs avg
        if not material:
            continue

        severity = "watch"
        if abs(delta) >= thresh * 2:
            severity = "fail" if (
                (higher_worse and delta > 0) or (not higher_worse and delta < 0)
            ) else "watch"
        elif (higher_worse and delta > 0) or (not higher_worse and delta < 0):
            severity = "watch"
        else:
            severity = "info"  # moved in a favourable direction but large

        from_lab = labels[i0] if i0 is not None else "prior avg"
        to_lab = labels[i1]
        flags.append(
            {
                "id": f"ratio_{key}",
                "kind": "ratio_trend",
                "severity": severity,
                "title": f"{label}: {from_lab} → {to_lab}",
                "value": (
                    f"{format_ratio_value(key, v0) if i0 is not None else 'avg'} → "
                    f"{format_ratio_value(key, v1)} (Δ {delta:+.3g})"
                ),
                "rule": f"Flag if |Δ| ≥ {thresh:g} ({'higher worse' if higher_worse else 'lower worse'})",
                "why": (
                    "Deterioration vs prior period/avg — check if sales mix, WC, leverage, "
                    "or one-offs explain it."
                    if severity in ("watch", "fail")
                    else "Large favourable move — confirm sustainability."
                ),
                "period_labels": labels,
                "key": key,
            }
        )

    # Disproportion: revenue growth vs WC days / debt
    try:
        revs = [by_pe.get(pe, {}).get("asset_turnover") for pe in periods]  # placeholder
        _ = revs
    except Exception:
        pass

    # Revenue vs receivable days disproportion
    rev_series = []
    for pe in periods:
        m = pm.get(pe)
        if not m:
            continue
        rev_series.append((pe, m.get("total_revenue", "revenue")))
    if len(rev_series) >= 2 and rev_series[-1][1] and rev_series[-2][1]:
        pe1, r1 = rev_series[-1]
        pe0, r0 = rev_series[-2]
        if r0 and r0 > 0:
            rev_g = r1 / r0 - 1
            dso1 = by_pe.get(pe1, {}).get("receivable_days")
            dso0 = by_pe.get(pe0, {}).get("receivable_days")
            if dso1 is not None and dso0 is not None and rev_g is not None:
                # Receivables growing much faster than sales
                if rev_g < 0.15 and (dso1 - dso0) > 20:
                    flags.append(
                        {
                            "id": "disproportion_ar_vs_sales",
                            "kind": "disproportion",
                            "severity": "fail" if (dso1 - dso0) > 35 else "watch",
                            "title": "Receivable days up while sales soft",
                            "value": (
                                f"Sales {_fy_label(pe0)}→{_fy_label(pe1)} {rev_g:+.1%}; "
                                f"DSO {dso0:.0f}→{dso1:.0f} days"
                            ),
                            "rule": "Watch if DSO +>20 days with sales growth <15%",
                            "why": "Working capital / channel stuffing risk — not proportional to growth.",
                        }
                    )
                if rev_g > 0.2 and (dso1 - dso0) > 25:
                    flags.append(
                        {
                            "id": "disproportion_ar_growth",
                            "kind": "disproportion",
                            "severity": "watch",
                            "title": "Receivables days rising with fast growth",
                            "value": (
                                f"Sales {rev_g:+.1%}; DSO {dso0:.0f}→{dso1:.0f}"
                            ),
                            "rule": "Watch if high growth accompanies +>25 DSO",
                            "why": "Growth quality — collections lagging sales.",
                        }
                    )

    return {
        "periods": periods,
        "period_labels": labels,
        "rows": rows,
        "flags": flags,
    }


def _light_critique(session: ResearchSession) -> dict[str, Any]:
    """CIQ-only checklists — skip PDF governance scan / business overview (can hang)."""
    from iis.finance.critique import (
        accounting_quality,
        balance_sheet,
        management_credibility,
        moat_quality,
    )

    lists = {
        "accounting_quality": ("Accounting quality", accounting_quality(session)),
        "balance_sheet": ("Balance sheet strength", balance_sheet(session)),
        "moat": ("Moat / business quality", moat_quality(session)),
        "management": ("Management credibility", management_credibility(session)),
    }
    summary = {
        k: {v: sum(1 for i in items if i["verdict"] == v) for v in ("pass", "watch", "fail")}
        for k, (_lbl, items) in lists.items()
    }
    return {
        "checklists": {k: {"label": lbl, "items": items} for k, (lbl, items) in lists.items()},
        "summary": summary,
        "research_questions": [],
    }


def build_investor_concerns(session: ResearchSession) -> dict[str, Any]:
    """Full pack: checklist fails/watches + ratio trends + disproportion + scorecard flags."""
    try:
        critique = _light_critique(session)
        # Persist light critique for appendix tables
        from iis.models.artifacts import AgentArtifact
        from iis.models.enums import SourceTier

        session.agent_artifacts = [
            a for a in session.agent_artifacts if a.artifact_type != "critique"
        ]
        session.agent_artifacts.append(
            AgentArtifact(
                agent_id="critique",
                artifact_type="critique",
                payload=critique,
                source_tier=SourceTier.EXTRACTED,
            )
        )
    except Exception as exc:
        critique = {"checklists": {}, "error": str(exc)}

    checklist_concerns: list[dict[str, Any]] = []
    for key, block in (critique.get("checklists") or {}).items():
        label = block.get("label") or key
        for item in block.get("items") or []:
            v = item.get("verdict")
            if v not in ("fail", "watch", "review"):
                continue
            checklist_concerns.append(
                {
                    "id": f"checklist_{key}_{item.get('item', '')[:40]}",
                    "kind": "checklist",
                    "bucket": key,
                    "bucket_label": label,
                    "severity": v,
                    "title": item.get("item"),
                    "value": item.get("value"),
                    "rule": item.get("rule"),
                    "why": item.get("evidence") or label,
                }
            )

    trends = ratio_trend_table(session)
    ratio_flags = list(trends.get("flags") or [])

    # Scorecard early warnings
    scorecard_flags: list[dict[str, Any]] = []
    try:
        from iis.finance.inertia_scorecard import build_inertia_scorecard

        sc = build_inertia_scorecard(session)
        for c in sc.get("concerns") or []:
            scorecard_flags.append(
                {
                    "id": f"scorecard_{c.get('title', '')[:40]}",
                    "kind": "scorecard",
                    "severity": c.get("severity"),
                    "title": c.get("title"),
                    "value": c.get("value"),
                    "rule": "Inertia scorecard rule",
                    "why": c.get("why"),
                }
            )
    except Exception:
        pass

    all_flags = checklist_concerns + ratio_flags + scorecard_flags
    rank = {"fail": 0, "review": 1, "watch": 2, "info": 3}
    all_flags.sort(key=lambda f: (rank.get(f.get("severity") or "", 9), f.get("title") or ""))

    summary = {
        "fail": sum(1 for f in all_flags if f.get("severity") == "fail"),
        "watch": sum(1 for f in all_flags if f.get("severity") == "watch"),
        "review": sum(1 for f in all_flags if f.get("severity") == "review"),
        "info": sum(1 for f in all_flags if f.get("severity") == "info"),
    }

    return {
        "summary": summary,
        "concerns": all_flags[:40],
        "ratio_trends": {
            "period_labels": trends.get("period_labels") or [],
            "rows": trends.get("rows") or [],
        },
        "checklist_summary": critique.get("summary") or {},
        "research_questions": (critique.get("research_questions") or [])[:10],
    }


def apply_investor_concerns(session: ResearchSession) -> ResearchSession:
    payload = build_investor_concerns(session)
    session.agent_artifacts = [
        a for a in session.agent_artifacts if a.artifact_type != "investor_concerns"
    ]
    session.agent_artifacts.append(
        AgentArtifact(
            agent_id="investor_concerns",
            artifact_type="investor_concerns",
            payload=payload,
            confidence=0.9,
            provenance=["python", "critique", "ratios"],
            source_tier=SourceTier.EXTRACTED,
        )
    )
    return session


def concerns_markdown_for_hive(payload: dict[str, Any] | None) -> str:
    """Compact block for initiating-coverage context."""
    if not payload:
        return ""
    s = payload.get("summary") or {}
    lines = [
        "=== PYTHON INVESTOR CHECKLIST & RATIO CONCERNS ===",
        "",
        f"Summary: fail={s.get('fail', 0)} · watch={s.get('watch', 0)} · "
        f"review={s.get('review', 0)} · info={s.get('info', 0)}",
        "",
        "These are rule-based flags with actual values. Discuss a concern in the narrative "
        "ONLY if it is investment-relevant; skip noise. Prefer fail/watch over info.",
        "",
    ]
    concerns = payload.get("concerns") or []
    if not concerns:
        lines.append("No elevated checklist/ratio concerns from Python rules.")
        return "\n".join(lines)

    lines.append("| Sev | Concern | Value | Why / rule |")
    lines.append("|---|---|---|---|")
    for c in concerns[:25]:
        sev = (c.get("severity") or "").upper()
        title = str(c.get("title") or "")[:80].replace("|", "/")
        val = str(c.get("value") or "")[:60].replace("|", "/")
        why = str(c.get("why") or c.get("rule") or "")[:100].replace("|", "/")
        lines.append(f"| {sev} | {title} | {val} | {why} |")
    lines.append("")

    # Compact ratio history for flagged keys
    rows = (payload.get("ratio_trends") or {}).get("rows") or []
    labels = (payload.get("ratio_trends") or {}).get("period_labels") or []
    flagged_keys = {c.get("key") for c in concerns if c.get("key")}
    show = [r for r in rows if r.get("key") in flagged_keys] or rows[:8]
    if show and labels:
        lines.append("Ratio history (flagged / sample):")
        lines.append("")
        lines.append("| Ratio | " + " | ".join(labels) + " |")
        lines.append("|---|" + "|".join(["---:"] * len(labels)) + "|")
        for r in show[:12]:
            lines.append(
                f"| {r.get('label')} | " + " | ".join(r.get("formatted") or []) + " |"
            )
        lines.append("")

    qs = payload.get("research_questions") or []
    if qs:
        lines.append("Open research asks from checklist:")
        for q in qs[:6]:
            lines.append(f"- {q}")
        lines.append("")

    lines.append(
        "If you want Python to append the full ratio-trend / checklist tables, include "
        "`ratio_deep` and/or `investor_checklist` in report_include."
    )
    return "\n".join(lines)


def investor_checklist_appendix_markdown(session: ResearchSession) -> list[str]:
    """Checklist + ratio-trend tables for report appendix (no heavy PDF rebuild)."""
    art = next(
        (a for a in session.agent_artifacts if a.artifact_type == "investor_concerns"),
        None,
    )
    payload = art.payload if art else build_investor_concerns(session)
    lines: list[str] = ["## Investor checklist & ratio concerns (Python)", ""]

    s = payload.get("summary") or {}
    lines.append(
        f"_Elevated: **{s.get('fail', 0)} fail** · **{s.get('watch', 0)} watch** · "
        f"{s.get('review', 0)} review._"
    )
    lines.append("")

    concerns = payload.get("concerns") or []
    if concerns:
        lines.append("### Concerns")
        lines.append("")
        lines.append("| Severity | Item | Value | Rule / why |")
        lines.append("|---|---|---|---|")
        for c in concerns:
            lines.append(
                f"| {(c.get('severity') or '').upper()} | "
                f"{str(c.get('title') or '').replace('|', '/')} | "
                f"{str(c.get('value') or '').replace('|', '/')} | "
                f"{str(c.get('why') or c.get('rule') or '').replace('|', '/')[:160]} |"
            )
        lines.append("")

    rows = (payload.get("ratio_trends") or {}).get("rows") or []
    labels = (payload.get("ratio_trends") or {}).get("period_labels") or []
    if rows and labels:
        lines.append("### Ratio trends (multi-year)")
        lines.append("")
        lines.append("| Ratio | " + " | ".join(labels) + " |")
        lines.append("|---|" + "|".join(["---:"] * len(labels)) + "|")
        for r in rows:
            lines.append(f"| {r.get('label')} | " + " | ".join(r.get("formatted") or []) + " |")
        lines.append("")

    # Light critique tables only (already on session artifact — no build_business_checklist)
    _V = {"pass": "✓ pass", "watch": "⚠ watch", "fail": "✗ fail", "n/a": "– n/a", "info": "· info", "review": "◎ review"}
    crit = next((a.payload for a in session.agent_artifacts if a.artifact_type == "critique"), None)
    if crit and crit.get("checklists"):
        lines.append("### Checklist detail")
        lines.append("")
        for block in (crit.get("checklists") or {}).values():
            if not block.get("items"):
                continue
            lines += [
                f"#### {block.get('label')}",
                "",
                "| Item | Verdict | Value | Rule | Evidence |",
                "|---|---|---|---|---|",
            ]
            for i in block["items"]:
                ev = (i.get("evidence") or "").replace("|", "/")[:220]
                lines.append(
                    f"| {i.get('item')} | {_V.get(i.get('verdict'), i.get('verdict'))} | "
                    f"{i.get('value')} | {i.get('rule')} | {ev} |"
                )
            lines.append("")
        lines.append("_Verdicts are fixed rules on CIQ data._")
        lines.append("")

    return lines
