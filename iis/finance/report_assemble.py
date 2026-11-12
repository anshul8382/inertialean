"""Assemble a research narrative from the evidence dossier (+ CIQ calcs).

Python builds the factual dossier. Optional local LLM only:
  - organises into the suggested report structure
  - polishes wording
  - adds interpretation grounded in the dossier (no new facts)

If Ollama is unavailable, returns the structured dossier markdown as-is.
"""
from __future__ import annotations

from typing import Any

from iis.finance.evidence_dossier import (
    build_evidence_dossier,
    write_evidence_dossier,
)
from iis.models.session import ResearchSession

SUGGESTED_STRUCTURE = """
1. Business overview
2. Annual report / management narrative (SWOT, risks, outlook)
3. Competitive position
4. Financial performance & ratio interpretation
5. Guidance / concall themes
6. Brokerage / street views (facts only)
7. Valuation (model outputs only)
8. Risks on file
""".strip()


def assemble_report_from_dossier(
    session: ResearchSession,
    *,
    use_llm: bool = True,
    persist: bool = True,
) -> str:
    """Return markdown: dossier-first, optionally LLM-polished assembly."""
    if persist:
        try:
            write_evidence_dossier(session)
        except Exception:
            pass
    dossier = build_evidence_dossier(session)
    dossier_md = dossier.markdown()

    # Always prepend CIQ / model facts already computed into session report helpers
    calcs = _calculation_appendix(session)

    if not use_llm:
        return _join(dossier_md, calcs)

    polished = _llm_assemble(session, dossier_md, calcs)
    if polished:
        return polished
    return _join(dossier_md, calcs)


def _join(*parts: str) -> str:
    out: list[str] = []
    for p in parts:
        if p and p.strip():
            out.append(p.strip())
    return "\n\n".join(out).rstrip() + "\n"


def _calculation_appendix(session: ResearchSession) -> str:
    lines: list[str] = ["## Calculated from Capital IQ / model", ""]
    try:
        from iis.finance.metrics import get_key_metrics_summary
        from iis.finance.growth import compute_revenue_cagr
        from iis.finance.ratios import format_ratio_value, get_latest_ratio_summary

        m = get_key_metrics_summary(session.financial_line_items or [])
        r = get_latest_ratio_summary(session.financial_line_items or [])
        cagr = compute_revenue_cagr(session.financial_line_items or [], years=3)
        rows = []
        if m.get("revenue_mn") is not None:
            rows.append(f"| Revenue (latest) | ₹{m['revenue_mn']:,.0f} mn |")
        if m.get("ebitda_margin") is not None:
            rows.append(f"| EBITDA margin | {m['ebitda_margin']:.1%} |")
        if cagr is not None:
            rows.append(f"| Revenue 3Y CAGR | {cagr:.1%} |")
        for key in ("roe", "roce", "net_debt_to_ebitda", "cfo_to_ni", "fcf_margin"):
            if r.get(key) is not None:
                rows.append(f"| {key} | {format_ratio_value(key, r[key])} |")
        if session.valuation and session.valuation.intrinsic_value_per_share is not None:
            rows.append(
                f"| DCF fair value | ₹{session.valuation.intrinsic_value_per_share:,.0f}/sh |"
            )
            if session.valuation.margin_of_safety_pct is not None:
                rows.append(f"| MoS | {session.valuation.margin_of_safety_pct:.1%} |")
        if rows:
            lines.extend(["| Metric | Value |", "|---|---|", *rows, ""])
        else:
            return ""
    except Exception:
        return ""
    return "\n".join(lines)


def _llm_assemble(session: ResearchSession, dossier_md: str, calcs: str) -> str | None:
    """Ask local finance LLM to assemble — cite only provided evidence."""
    try:
        from iis.llm.finance_client import FinanceOllamaClient

        llm = FinanceOllamaClient()
        if not llm.is_available():
            return None
    except Exception:
        return None

    company = session.company.name if session.company else "the company"
    # Keep prompt bounded for MacBook Air
    dossier_trim = dossier_md[:14000]
    calcs_trim = (calcs or "")[:3000]
    prompt = (
        f"You are an equity research analyst writing a note on {company} "
        f"from AVAILABLE INFORMATION ONLY.\n\n"
        f"Use ONLY the evidence dossier and calculated metrics below. "
        f"You may process and analyse what is provided; you must NOT make "
        f"assumptions or invent numbers, peers, forecasts, or events. "
        f"If a topic has no evidence, omit that section entirely — "
        f"never write 'analyst to complete' or similar fillers.\n\n"
        f"Suggested structure:\n{SUGGESTED_STRUCTURE}\n\n"
        f"For each included section: organise facts, polish wording, and add "
        f"brief analysis that follows directly from the evidence "
        f"(e.g. what a margin or leverage ratio implies). "
        f"Keep street ratings clearly labeled as broker views.\n\n"
        f"=== EVIDENCE DOSSIER ===\n{dossier_trim}\n\n"
        f"=== CALCULATED METRICS ===\n{calcs_trim}\n\n"
        f"Return full markdown research note only."
    )
    try:
        text = llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Equity research analyst: report only from provided evidence. "
                        "Analyse and organise; never assume or invent. "
                        "Omit empty sections. No placeholders."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.15,
        )
        text = (text or "").strip()
        if len(text) < 200:
            return None
        return text if text.endswith("\n") else text + "\n"
    except Exception:
        return None


def dossier_stats(session: ResearchSession) -> dict[str, Any]:
    d = build_evidence_dossier(session)
    return {
        "hit_count": len(d.hits),
        "topic_counts": d.topic_counts,
        "company": d.company,
    }
