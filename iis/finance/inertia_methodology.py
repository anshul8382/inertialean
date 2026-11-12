"""Inertia initiating-coverage methodology — rubric for Hive AR relevance & critic.

Numbers (revenue, margins, Capex totals, geo mix ₹) come from Capital IQ + Python.
Hive judges narrative relevance and summarises; it must not invent financials.
"""
from __future__ import annotations

from typing import Any

from iis.models.session import ResearchSession

# Narrative quality dimensions (not a valuation model)
DIMENSION_WEIGHTS: dict[str, int] = {
    "business_quality": 25,
    "financial_quality": 25,
    "management_governance": 20,
    "growth_prospects": 15,
    "balance_sheet_strength": 15,
}

RELEVANCE_RUBRIC = """
You are an equity research analyst screening an Indian annual-report SECTION for
initiating-coverage relevance.

KEEP (decision=keep) if the section helps answer any of:
- How the company makes money; products; segments; customers
- Revenue/volume mix, geography of SALES (not non-current assets by location)
- Capacity, utilisation, plants, Capex purpose (amounts flagged for CIQ check)
- Competitive position, moat, pricing/RM sensitivity
- Sector/industry STRUCTURE that hits THIS company's P&L (e.g. agrochem structural shifts)
- Policy / trade / regulatory factors SPECIFIC to the company's industry
- Strategy journey that explains the equity story (e.g. AgChem → CSM → life sciences)
- Guidance / outlook / management priorities
- Governance that can impair equity (auditor issues, RPT, pledge, contingents)

SKIP (decision=skip) if the section is mostly:
- Generic India GDP / world GDP / macro boilerplate common to every AR
- CSR / BRSR brochure, green cover, energy mix as "ESG highlights"
- AGM / IEPF / secretarial process / blank committee name lists
- Pure accounting-policy / Ind-AS boilerplate with no company-specific red flags
- Repeated page headers / cover letters with no substance

SKIM (decision=skim) if mixed: some useful facts buried in boilerplate — worth a
light pass if checklist gaps remain.

HARD RULES:
- Do NOT treat non-current assets by geography as "market" or revenue mix.
- Do NOT invent numbers. Flag ₹ figures as narrative only (CIQ is authoritative).
- Prefer sector-specific and company-specific over economy-wide prose.
""".strip()

CRITIC_RUBRIC = """
You are an Independent Reviewer for an Inertia initiating-coverage draft based on
annual-report extracts.

Your job is to CHALLENGE, not confirm:
1. Separate FACTS (with source) from ASSUMPTIONS / aspirational language.
2. List contradictions between bullets or vs Capital IQ notes if provided.
3. List unanswered MUST checklist items still missing.
4. Call out any bullet that looks like GDP/ESG/process boilerplate that should be dropped.
5. Call out any risk that numbers were invented or assets mislabeled as revenue.

OUTPUT JSON only (no markdown fences):
{
  "headlines": ["short concern or confirmation strings"],
  "facts_ok": ["..."],
  "assumptions": ["..."],
  "contradictions": ["..."],
  "drop_bullets": ["exact or close text to drop"],
  "missing_checklist_ids": ["id1"],
  "gap_fill_sections": ["section titles from the AR index to re-read"],
  "confidence": 0.0
}
""".strip()

DEEP_READ_MODES = (
    "leadership_letters",
    "future_outlook",
    "governance",
    "financial_capital",
    "mda",
    "other_keep",
)


def methodology_prompt_block() -> str:
    weights = ", ".join(f"{k}={v}%" for k, v in DIMENSION_WEIGHTS.items())
    return (
        f"{RELEVANCE_RUBRIC}\n\n"
        f"Quality dimensions (narrative scoring only, not valuation): {weights}.\n"
        "Keep valuation separate from business/financial/management quality."
    )


def open_checklist_gaps(session: ResearchSession) -> list[dict[str, Any]]:
    """Must/should checklist items still missing or partial."""
    try:
        from iis.finance.business_checklist import build_business_checklist

        payload = build_business_checklist(session)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for it in payload.get("items") or []:
        if it.get("priority") not in ("must", "should"):
            continue
        if it.get("status") in ("missing", "partial"):
            out.append(
                {
                    "id": it.get("id"),
                    "item": it.get("item"),
                    "ask": it.get("ask"),
                    "priority": it.get("priority"),
                    "status": it.get("status"),
                }
            )
    return out


def gaps_prompt_block(session: ResearchSession) -> str:
    gaps = open_checklist_gaps(session)
    if not gaps:
        return "Open checklist gaps: none flagged (still prefer keep of high-signal narrative)."
    lines = ["Open initiating-coverage checklist gaps:"]
    for g in gaps[:16]:
        lines.append(
            f"- [{g.get('priority')}] {g.get('id')}: {g.get('item')} — {g.get('ask')}"
        )
    return "\n".join(lines)
