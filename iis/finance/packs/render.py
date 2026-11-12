"""Render packs to analyst-friendly markdown (hover IDs in brackets)."""
from __future__ import annotations

from typing import Any


def _claim_line(c: dict[str, Any]) -> str:
    cid = c.get("id") or ""
    param = c.get("parameter") or ""
    ctype = c.get("claim_type") or ""
    rel = c.get("reliability") or ""
    src = c.get("source_file") or ""
    page = c.get("page")
    loc = f"{src}" + (f" p.{page}" if page is not None else "")
    quote = (c.get("quote") or "").replace("\n", " ")
    meta = f"_{ctype}/{rel}_"
    return f"- **{param}** {meta} [{cid}] ({loc})\n  {quote}"


SECTION_TITLES = {
    # AR
    "business_positioning": "A. Business understanding & competitive positioning",
    "statements_and_notes": "B. Statements & notes to accounts",
    "governance": "C. Management quality & governance",
    "capital_allocation": "D. Capital allocation",
    "working_capital": "E. Working capital efficiency",
    "debt_obligations": "F. Debt & financial obligations",
    "shareholding": "G. Promoter & shareholding",
    "risk_factors": "H. Risk factors",
    "subsidiaries_segments": "I. Subsidiaries & segments",
    "accounting_audit": "J. Accounting policies & audit",
    # Concall
    "growth_quality": "A. Growth quality (core vs one-off)",
    "guidance_outlook": "B. Management guidance & outlook",
    "margins": "C. Margin analysis",
    "capex_capacity": "D. Capex & capacity",
    "competitive_positioning": "E. Competitive positioning",
    "management_credibility": "F. Management confidence & credibility",
    "qa_insights": "G. Q&A insights",
    "working_capital_cash": "H. Working capital & cash flow",
    "new_initiatives": "I. New business & growth initiatives",
    "risks_challenges": "J. Risks & challenges",
    "guidance_vs_actual": "K. Guidance vs actual (CIQ)",
    "commentary_changes": "L. Commentary change signals",
    # Broker
    "investment_thesis": "A. Investment thesis",
    "financial_estimates": "B. Financial estimates & projections",
    "valuation": "C. Valuation methodology",
    "earnings_assumptions": "D. Earnings growth assumptions",
    "peer_comparison": "E. Peer comparison",
    "industry_outlook": "F. Industry outlook",
    "catalysts": "G. Key triggers & catalysts",
    "risks_downside": "H. Risks & downside",
    "estimate_revisions": "I. Changes in earnings estimates",
    "mgmt_vs_broker": "J. Management vs brokerage assumptions",
    "analyst_track_record": "K. Analyst track record",
    "valuation_sensitivity": "L. Valuation sensitivity",
}


def render_pack_markdown(pack: dict[str, Any], *, title: str | None = None) -> str:
    company = pack.get("company") or "Company"
    ptype = pack.get("pack_type") or "pack"
    lines = [
        f"# {title or (ptype.replace('_', ' ').title() + ' research pack')} — {company}",
        "",
        f"_Built {pack.get('built_at')} · {pack.get('claim_count', 0)} claims · schema v{pack.get('schema_version', 1)}_",
        "",
        "Cite claims by ID in brackets (e.g. `[AB-12ab34cd]`). Hover/UI can resolve ID → source quote.",
        "",
    ]
    meta = pack.get("meta") or {}
    if meta:
        lines.append("## Meta")
        for k, v in meta.items():
            lines.append(f"- **{k}**: `{v}`" if not isinstance(v, (dict, list)) else f"- **{k}**: {v}")
        lines.append("")

    sections = pack.get("sections") or {}
    for key, claims in sections.items():
        heading = SECTION_TITLES.get(key, key.replace("_", " ").title())
        lines.append(f"## {heading}")
        lines.append("")
        if not claims:
            lines.append("_No excerpts retrieved for this section._")
            lines.append("")
            continue
        for c in claims:
            lines.append(_claim_line(c))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
