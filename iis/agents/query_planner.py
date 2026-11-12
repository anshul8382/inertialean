"""Ollama-based query planner: natural language → structured data query."""
from __future__ import annotations

import re
from typing import Any

from iis.config import settings
from iis.finance.multiples import get_estimates_summary, get_multiples_table
from iis.llm.ollama_client import OllamaClient
from iis.models.session import ResearchSession


def _looks_like_pct_edit(lower: str) -> bool:
    return bool(re.search(r"\d+\s*%", lower))

QUERY_TYPES = """
Query types — pick ONE:

FINANCIALS (Capital IQ Financials .xls):
- revenue_growth — revenue history, CAGR, forward consensus revenue projections
  params: historical_years, forward_years
- financial_metric — single line item (revenue, ebitda, net_income, eps, debt, cash)
  params: metric, period_type ("actual"|"estimate")
- capex_analysis — multi-year capex, vs sales, recent vs long-term (use for capex history questions)
- capacity_utilization — plant/capacity excerpts from analysis pack / PDFs
- ratio — computed ratios from financials (roe, roa, margins, debt/equity, current_ratio)
  params: ratio
- all_ratios — full ratio catalog
- segment_revenue — segment revenue (jewellery, watches, eyecare)
  params: segment

ESTIMATES (Capital IQ Estimates .xls) — DO NOT confuse these:
- valuation_multiples — forward P/E, EV/EBITDA, P/BV, PEG from Multiples sheet (NOT target price)
  params: period ("NTM"|"FY 2027"|"FY 2028"), multiple ("pe"|"ev_ebitda"|"pbv")
- consensus — analyst TARGET PRICE, consensus EPS, consensus revenue only
  params: metric ("target_price"|"eps"|"revenue") — NEVER use for P/E questions

AGENT ARTIFACTS (after analyze):
- guidance, porters, annual_report

TRANSCRIPTS:
- transcript_qa — search earnings call Q&A pairs directly (management answers)
  params: topic (optional keyword filter)

DATA FLAGS (verifiable anomalies — no scores):
- data_flags — cash vs revenue, working capital, guidance vs actual, reconciliations
- red_flags — alias for data_flags
- guidance_track_record — management guidance vs reported actuals over past years

MULTI-STEP (combine structured + qualitative):
- multi — when question needs BOTH numbers AND narrative (e.g. "revenue growth and what management said")
  params: steps — array of {query_type, params} sub-queries

SESSION:
- valuation (DCF intrinsic value), market_price (live price), gates, files, generate_note

FALLBACK: pdf_rag, external
"""

PLANNER_SYSTEM = """You route analyst questions to structured queries against IIS data stores.
You do NOT answer — only pick query_type + params.

CRITICAL DISTINCTIONS:
- "forward PE", "P/E", "price to earnings", "NTM multiple", "trading multiple" → valuation_multiples
- "target price", "analyst target", "consensus price" → consensus with metric=target_price
- P/E is NOT target price. Target price is INR/share. P/E is a ratio (e.g. 66x).
- "forward EBITDA multiple", "EV/EBITDA" → valuation_multiples with multiple=ev_ebitda
- revenue growth / CAGR / projections → revenue_growth

Use the DATA CATALOG below — only route to fields that exist in the session.

Return JSON (single step):
{"query_type":"...", "params":{...}, "confidence":"high|medium|low", "reason":"..."}

For multi-step questions return:
{"query_type":"multi", "steps":[{"query_type":"revenue_growth","params":{}},{"query_type":"transcript_qa","params":{"topic":"outlook"}}], "confidence":"high", "reason":"..."}
"""


def build_data_catalog(session: ResearchSession) -> str:
    """Describe available fields so the planner can route accurately."""
    lines = ["DATA CATALOG (fields available in this session):"]

    if session.financial_line_items:
        lines.append("- financials.line_items: total_revenue, ebitda, net_income, gross_profit, total_debt, cash, segments")
        lines.append("- financials.data_flags: cash vs revenue, receivables, guidance vs actual, reconciliations")
        lines.append("- financials.computed_ratios: roe, roa, margins, debt/equity, current_ratio, inventory_days")
        lines.append("- financials.revenue_growth: historical YoY, 3Y/5Y CAGR, forward revenue estimates")

    summary = get_estimates_summary(session)
    if summary:
        if summary.get("target_price_mean"):
            lines.append(f"- estimates.target_price_mean: ₹{summary['target_price_mean']:,.0f}/share (consensus analyst target)")
        if summary.get("target_price_median"):
            lines.append(f"- estimates.target_price_median: ₹{summary['target_price_median']:,.0f}/share")
        multiples = get_multiples_table(session)
        if multiples:
            lines.append("- estimates.multiples (Multiples sheet — NOT target price):")
            for period, data in list(multiples.items())[:6]:
                parts = []
                if data.get("pe"):
                    parts.append(f"P/E {data['pe']:.1f}x")
                if data.get("ev_ebitda"):
                    parts.append(f"EV/EBITDA {data['ev_ebitda']:.1f}x")
                if parts:
                    lines.append(f"    · {period}: {', '.join(parts)}")
        if session.consensus_metrics:
            metrics = sorted({m.metric for m in session.consensus_metrics})
            lines.append(f"- estimates.consensus_metrics: {', '.join(metrics[:6])}")

    if session.pdf_documents:
        qa_count = sum(len(d.qa_pairs) for d in session.pdf_documents)
        if qa_count:
            lines.append(f"- transcripts.qa_pairs: {qa_count} earnings call Q&A pairs (use transcript_qa)")

    if session.pdf_chunks:
        lines.append("- pdfs: annual_report, transcripts, presentations (qualitative only)")

    artifacts = [a.artifact_type for a in session.agent_artifacts]
    if "guidance_summary" in artifacts:
        lines.append("- agent.guidance_summary: management themes and commentary")
    if "porters_five_forces" in artifacts:
        lines.append("- agent.porters_five_forces: competitive analysis")
    if "annual_report_analysis" in artifacts:
        lines.append("- agent.annual_report_analysis: AR sections and extracted financials")

    if len(lines) == 1:
        lines.append("- (no data uploaded yet)")
    return "\n".join(lines)


def build_session_context(session: ResearchSession) -> str:
    company = session.company.name if session.company else "Unknown"
    artifacts = [a.artifact_type for a in session.agent_artifacts]

    lines = [
        f"Company: {company}",
        f"Financial line items: {len(session.financial_line_items)}",
        f"PDF chunks: {len(session.pdf_chunks)}",
        f"Consensus metrics: {len(session.consensus_metrics)}",
        f"Agent artifacts: {', '.join(artifacts) if artifacts else 'none'}",
        "",
        build_data_catalog(session),
    ]
    from iis.models.enums import GateId

    session.init_gates()
    gate_status = ", ".join(f"{g.value}={session.gate(g).status.value}" for g in GateId)
    lines.append(f"\nGates: {gate_status}")
    return "\n".join(lines)


def route_deterministic(session: ResearchSession, question: str) -> dict[str, Any] | None:
    """Pure-Python router — no Ollama. Prefer this on Mac Mini / offline packs."""
    lower = question.lower()

    # Capex history / intensity (NOT DCF assumption edits).
    # Skip when the ask is multi-part research (peers / customers / segments) — chat LLM handles those.
    if any(k in lower for k in ("capex", "capital expenditure", "capital spend")):
        multi_research = any(
            k in lower
            for k in (
                "peer",
                "competitor",
                "customer",
                "who are",
                "what are",
                "pharma",
                "electronics",
                "segment",
            )
        ) and (question.count("?") >= 1 or re.search(r"(?m)^\s*\d+[.)]", question) or " vs " in lower)
        if multi_research:
            return None
        if not (
            _looks_like_pct_edit(lower)
            or any(k in lower for k in ("set ", "use ", "update assumption", "gate 4", "dcf assumption"))
        ):
            return {
                "query_type": "capex_analysis",
                "params": {"recent_years": 3},
                "confidence": "high",
                "reason": "historical capex from Capital IQ (Python module)",
            }

    if any(k in lower for k in ("capacity utilization", "capacity utilisation", "installed capacity", "plant capacity")):
        return {
            "query_type": "capacity_utilization",
            "params": {},
            "confidence": "high",
            "reason": "capacity/utilization from analysis pack PDF excerpts",
        }

    # Fast path: obvious multiples vs target price (avoid LLM confusion)
    if any(kw in lower for kw in ("forward pe", "forward p/e", "p/e", "price to earnings", "price earnings multiple", "ntm pe", "trailing pe")):
        if get_multiples_table(session):
            period = "NTM" if "ntm" in lower else None
            for fy in ("2027", "2028", "2029", "2030"):
                if fy in lower:
                    period = f"FY {fy}"
            mult = "ev_ebitda" if "ev/ebitda" in lower or "ebitda multiple" in lower else "pe"
            return {
                "query_type": "valuation_multiples",
                "params": {"period": period, "multiple": mult},
                "confidence": "high",
                "reason": "P/E or valuation multiple question — Multiples sheet",
            }

    if any(kw in lower for kw in ("target price", "analyst target", "price target")) and "p/e" not in lower and "pe ratio" not in lower:
        return {
            "query_type": "consensus",
            "params": {"metric": "target_price"},
            "confidence": "high",
            "reason": "analyst target price from Consensus sheet",
        }

    # Multi-step fast path: growth + management commentary
    if ("growth" in lower or "cagr" in lower) and any(
        kw in lower for kw in ("management", "said", "outlook", "guidance", "transcript", "call")
    ):
        return {
            "query_type": "multi",
            "steps": [
                {"query_type": "revenue_growth", "params": {}},
                {"query_type": "transcript_qa", "params": {"topic": "outlook"}},
            ],
            "confidence": "high",
            "reason": "combines revenue growth with management commentary",
        }

    if any(kw in lower for kw in ("earnings call", "transcript", "management said", "q&a", "concall")):
        return {
            "query_type": "transcript_qa",
            "params": {"topic": question},
            "confidence": "high",
            "reason": "earnings call Q&A search",
        }

    if any(
        kw in lower
        for kw in (
            "red flag",
            "red flags",
            "data flag",
            "anomal",
            "forensic",
            "guidance vs",
            "guidance track",
            "beat guidance",
            "miss guidance",
            "missed guidance",
        )
    ):
        if any(k in lower for k in ("guidance", "track record", "beat", "miss")):
            return {
                "query_type": "guidance_track_record",
                "params": {},
                "confidence": "high",
                "reason": "management guidance vs actual track record",
            }
        return {
            "query_type": "data_flags",
            "params": {},
            "confidence": "high",
            "reason": "verifiable data anomaly flags",
        }

    if any(kw in lower for kw in ("revenue growth", "sales growth", "cagr", "top-line growth")):
        return {
            "query_type": "revenue_growth",
            "params": {},
            "confidence": "high",
            "reason": "revenue growth / CAGR from Capital IQ",
        }

    return None


def plan_query(session: ResearchSession, question: str, llm: OllamaClient | None = None) -> dict[str, Any] | None:
    """Route question → query_type. Deterministic first; Ollama only for unknowns."""
    fast = route_deterministic(session, question)
    if fast:
        return fast

    if settings.llm_planner_for_unknown_only is False:
        return None

    if llm is None or not llm.is_available():
        return None

    context = build_session_context(session)
    try:
        result = llm.extract_json(
            f"Session context:\n{context}\n\n{QUERY_TYPES}\n\nAnalyst question: {question}",
            system=PLANNER_SYSTEM,
        )
        if not isinstance(result, dict) or not result.get("query_type"):
            return None
        return result
    except Exception:
        return None
