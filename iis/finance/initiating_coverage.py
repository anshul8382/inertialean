"""Final Hive call: fundamental research analyst → initiating coverage report.

Python has already collected CIQ, packs, research plan, and the one-shot digest.
This is the last LLM step in Analyze — the Report tab shows THIS, not the Python dump.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from iis.config import settings
from iis.models.artifacts import AgentArtifact
from iis.models.enums import SourceTier
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

_SYSTEM = """You are a fundamental equity research analyst writing an INITIATING COVERAGE report
for a buy-side / long-term investor covering an Indian listed company.

Voice: professional, skeptical, evidence-led. Clear prose — not a data dump.

═══════════════════════════════════════════════════════════════════
CORE INTENT (read this before writing)
═══════════════════════════════════════════════════════════════════
An initiating coverage report must give a COMPLETE understanding of the company
BEFORE detailed financial analysis and valuation.

Two broad parts — always in this order:

  PART A — GENERAL / BUSINESS UNDERSTANDING
    What the company does, how it makes money, industry, competitive position,
    management & governance. Answer one question:
    “If an investor has never heard of this company, what must they understand
     about its business before deciding whether to invest?”
    Educate on WHAT the business IS. Do NOT jump to “what’s happening lately.”

  PART B — INVESTMENT RESEARCH
    Financial performance, growth prospects, risks, forecasts, valuation,
    investment thesis. Only AFTER the reader understands the business.

FORBIDDEN in Part A (belongs in Part B):
  historical revenue growth narratives, ROCE/incremental ROCE deep-dives,
  financial projections, earnings impact of risks, capex returns analysis,
  “recent quarter / recent year momentum” as the opening frame.

Part A may mention capacity, product mix, or industry growth as BUSINESS context
(who they are / where they sit) — not as a financial results write-up.

═══════════════════════════════════════════════════════════════════
PART A — INTENT CHECKLIST (cover substance; merge into headings below)
═══════════════════════════════════════════════════════════════════
1. Company overview — incorporation/HQ, promoters, segments, products, geography,
   plants, subsidiaries, employees (when in materials).
2. Business model — how money is made, who customers/payers are, revenue streams &
   mix, pricing, distribution, direct vs indirect, recurring vs one-off.
3. Product / service portfolio — key offerings, contribution, differentiation,
   premium vs mass, lifecycle / new launches (when available).
4. Industry overview — size/structure, growth, demand drivers, cyclicality,
   regulation, structural trends, domestic vs global.
5. Industry value chain — suppliers → company → distributors → end customers;
   where the company sits; who captures margin.
6. Competitive landscape — peers, leaders, share/scale, differentiation, cost,
   distribution, barriers, substitutes.
7. Competitive advantages / moat — brand, switching costs, network, IP/tech,
   cost leadership, licences, scale, relationships (only if evidenced).
8. Management & promoters — background, track record, capital allocation hints,
   shareholding, pledging, succession (as available).
9. Corporate governance — board/independents, auditor, RPTs, litigation/regulatory,
   remuneration concerns when material.
10. Manufacturing / operating capabilities — capacity, utilisation, locations,
    technology, supply chain, expansion plans.
11. Growth strategy — capacity, products, geographies, M&A, exports, digital,
    management’s medium-term plan (label management CLAIMS vs verified FACTS).
12. Key business risks (business-model risks — not yet full earnings sensitivity).

When materials lack an item: say so briefly under Open questions — do not invent.

═══════════════════════════════════════════════════════════════════
FACT vs CLAIM vs ASSESSMENT (required discipline)
═══════════════════════════════════════════════════════════════════
- FACT: from AR / filings / CIQ / verified tables — state plainly.
- MANAGEMENT CLAIM: from presentations / concalls / MD&A — label “management says…”.
- ANALYST ASSESSMENT: your judgment — label clearly; never present as fact.
Example: management may claim “market leader”; only call it leadership if share
or peer evidence supports it; otherwise treat as unverified claim.

═══════════════════════════════════════════════════════════════════
EXHIBITS (prefer compact markdown tables in-body)
═══════════════════════════════════════════════════════════════════
Company snapshot · segment mix · geographic mix · product portfolio ·
value-chain position · industry growth (if in materials) · competitive
benchmarking table (own subsection) · promoter holding · manufacturing footprint ·
management profile. Use {{TABLE:…}} markers for CIQ/peer grids Python appends.

Competitive benchmarking is PARTICULARLY important — own subsection with a
Company vs Peer table (Revenue, 3Y CAGR, EBITDA margin, ROCE, ROE, Net debt/EBITDA,
export mix, P/E, EV/EBITDA when peers/CIQ exist). Goal: why premium or discount.

HARD RULES:
- Use ONLY facts in the provided materials. Do NOT invent ₹ figures, margins, guidance, or ratings.
- Capital IQ / Python numbers are authoritative for financials.
- Broker notes are STREET opinion — label them as such.
- If evidence is thin, say so. Do not fill gaps with assumptions.
- Do NOT issue Buy/Hold/Sell unless that rating already appears in provided broker materials;
  frame Investment case vs Bear case instead.
- Do NOT use or invent a DCF / intrinsic value / WACC / margin-of-safety model.
  Valuation = CIQ trading multiples + street targets/ratings only (labelled STREET).
- Do NOT paste raw PDF excerpts, repeated boilerplate, OCR junk, checklists, or file paths in the body.
- Do NOT paste full Capital IQ statement grids — Python appends always-on tables after you.
  You may leave a marker like {{TABLE:ciq_financials}} where a table should sit in-body.
- Citations: use ONLY this hover marker (UI shows a small "ref" chip): [[src:FILE|short excerpt ≤180 chars]]
  Example: margins held near 24% [[src:PI AR 2026.pdf p.66|EBITDA Margin (%) … 25.4%]]
  Never write _(filename)_ or "Source: …" prose refs in the narrative.
- Deduplicate: each fact once. Short paragraphs + tight bullets.
- Prefer AR / presentations / transcripts / CIQ / credit reports / peer ARs over
  generic web colour. Hierarchy: filings > presentations/transcripts > street > industry notes.

OPTIONAL DATA DISCIPLINE:
- Skip CSR / BRSR brochure fluff unless controversy or material cash drag.
- Skip remuneration tables unless pay is exorbitant or jumped sharply without performance link.
- Do discuss auditor fees / auditor change when fees spike YoY or opinion looks off.
- End with a ```report_include``` JSON block listing optional ids to append (see user prompt).
"""


def _notify(cb: ProgressCb | None, msg: str) -> None:
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def _clip(text: str, n: int) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: max(0, n - 1)].rstrip() + "\n\n_[truncated]_"


def _artifact_payload(session: ResearchSession, artifact_type: str) -> dict[str, Any]:
    for a in session.agent_artifacts or []:
        if a.artifact_type == artifact_type and isinstance(a.payload, dict):
            return a.payload
    return {}


def _build_context(session: ResearchSession, python_draft: str) -> str:
    """Lean context: plan + digest + Layer-1 facts only (no Layer-2 junk dump)."""
    company = session.company.name if session.company else (session.company_key or "Company")
    nse = getattr(session.company, "nse_ticker", None) if session.company else None
    bits: list[str] = [f"Company: {company}" + (f" (NSE: {nse})" if nse else "")]

    try:
        from iis.llm.analyst_agent import data_status_block

        bits.append(data_status_block(session))
    except Exception:
        pass

    plan = _artifact_payload(session, "research_plan")
    if plan.get("markdown"):
        bits.append("=== RESEARCH PLAN ===\n" + _clip(str(plan["markdown"]), 8000))

    digest = _artifact_payload(session, "unified_doc_synthesis")
    if digest.get("markdown"):
        bits.append(
            "=== DOCUMENT DIGEST (synthesised — prefer this over raw excerpts) ===\n"
            + _clip(str(digest["markdown"]), 40000)
        )

    # Structured business facts FIRST — ground Part A (what the company IS)
    try:
        from iis.finance.business_checklist import (
            build_business_checklist,
            checklist_markdown,
            fill_business_slots,
            slots_markdown,
        )

        fills = fill_business_slots(session, per_slot=4)
        slot_md = slots_markdown(fills) if fills else []
        chk = build_business_checklist(session)
        chk_md = checklist_markdown(chk) if chk else []
        biz_parts: list[str] = []
        if slot_md:
            biz_parts.extend(slot_md)
        if chk_md:
            biz_parts.extend(chk_md)
        if biz_parts:
            bits.append(
                "=== BUSINESS KNOWLEDGE MODULES (Part A ground truth — educate first) ===\n"
                "Cover these before financial narrative. Treat snippets as FACT candidates; "
                "verify against AR/CIQ. Missing slots → Open questions, do not invent.\n\n"
                + _clip("\n".join(biz_parts), 18000)
            )
    except Exception:
        pass

    try:
        from iis.finance.packs import packs_seed_markdown

        pack = packs_seed_markdown(session, max_chars=8000)
        if pack:
            bits.append("=== PACK CLAIM IDS (cite [AB-…] when useful) ===\n" + pack)
    except Exception:
        pass

    # Layer 1 only — tables + brief; skip Layer 2 excerpt soup (junk OCR / repeats)
    draft = python_draft or ""
    layer1 = draft.split("# Layer 2", 1)[0] if "# Layer 2" in draft else draft
    bits.append(
        "=== PYTHON LAYER 1 FACT PACK (numbers authoritative; do not copy raw excerpt blobs) ===\n"
        + _clip(layer1, 24000)
    )

    try:
        from iis.finance.report_data_catalog import (
            build_report_data_catalog,
            catalog_markdown_for_hive,
        )

        catalog = build_report_data_catalog(session)
        bits.append(catalog_markdown_for_hive(catalog))
        # Persist for UI / debug
        session.agent_artifacts = [
            a for a in (session.agent_artifacts or []) if a.artifact_type != "report_data_catalog"
        ]
        session.agent_artifacts.append(
            AgentArtifact(
                agent_id="report_data_catalog",
                artifact_type="report_data_catalog",
                payload=catalog,
                confidence=0.9,
                provenance=["python"],
                source_tier=SourceTier.EXTRACTED,
            )
        )
    except Exception:
        pass

    # Investor scorecard concerns — Hive decides which to discuss in narrative
    try:
        from iis.finance.investor_concerns import (
            apply_investor_concerns,
            concerns_markdown_for_hive,
        )
        from iis.finance.inertia_scorecard import build_inertia_scorecard, scorecard_markdown

        apply_investor_concerns(session)
        art = next(
            (a for a in (session.agent_artifacts or []) if a.artifact_type == "investor_concerns"),
            None,
        )
        if art and isinstance(art.payload, dict):
            bits.append(concerns_markdown_for_hive(art.payload))
        sc = build_inertia_scorecard(session)
        if sc.get("ok") and sc.get("concerns"):
            bits.append(
                "=== INERTIA SCORECARD EARLY WARNINGS ===\n"
                + "\n".join(
                    f"- [{c.get('severity')}] {c.get('title')}: {c.get('value')} — {c.get('why')}"
                    for c in sc["concerns"]
                )
                + "\n\nPython will append the full scorecard tables to the report appendix."
            )
        # Keep a short critical-15 for narrative grounding
        sc_md = scorecard_markdown(session)
        if sc_md:
            # Only Critical-15 + flags section (~first 40 lines)
            bits.append(
                "=== CRITICAL-15 + FLAGS (excerpt; full tables in appendix) ===\n"
                + _clip("\n".join(sc_md[:45]), 6000)
            )
    except Exception:
        pass

    return "\n\n".join(bits)


def _scrub_prose_refs(md: str) -> str:
    """Convert leftover _(file)_ / Source: prose into hover markers when possible."""
    text = md or ""

    def _repl(m: re.Match[str]) -> str:
        src = (m.group(1) or "").strip()
        if not src or len(src) > 120:
            return ""
        return f" [[src:{src}|See source]]"

    # _(PI AR 2026.pdf p.35)_ or _(Call Q4 2026.pdf)_
    text = re.sub(r"\s*_\(([^)]+\.(?:pdf|xls|xlsx|md)[^)]*)\)_", _repl, text, flags=re.I)
    text = re.sub(r"\s*\(Source:\s*([^)]+)\)", _repl, text, flags=re.I)
    # Collapse duplicate blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def write_initiating_coverage(
    session: ResearchSession,
    python_draft: str,
    *,
    progress_cb: ProgressCb | None = None,
) -> str | None:
    """One Hive call → full initiating coverage markdown, or None on failure."""
    if not bool(getattr(settings, "initiating_coverage_llm_enabled", True)):
        return None

    from iis.context.manager import llm_agent
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    llm = get_llm_client(finance=True)
    if not llm.is_available():
        return None

    company = session.company.name if session.company else (session.company_key or "Company")
    _notify(progress_cb, f"Hive initiating coverage — fundamental analyst write-up · {company}…")

    context = _build_context(session, python_draft)
    prompt = (
        f"Write a full INITIATING COVERAGE research report on {company} in markdown.\n\n"
        "CRITICAL ORDER: Part A must EDUCATE on what the business IS (model, products, "
        "industry, value chain, competition, moat, management, ops, strategy, business risks) "
        "BEFORE Part B discusses recent results, forecasts, or valuation. "
        "A reader who never heard of the company must understand the business from Part A alone. "
        "Do NOT open with ‘recent performance / what’s going on’ — that comes in Part B.\n\n"
        "Use these section headings (##). Omit only if truly no evidence:\n\n"
        "# Part A — Business understanding\n"
        "## 1. Investment summary\n"
        "   (short thesis tease + what the company is in 5–8 lines — not a results wrap)\n"
        "## 2. Company overview\n"
        "## 3. Business model\n"
        "## 4. Product and service portfolio\n"
        "## 5. Industry overview\n"
        "## 6. Industry value chain\n"
        "## 7. Competitive positioning\n"
        "   (include ### Competitive benchmarking table when peer data exists)\n"
        "## 8. Competitive advantages / moat\n"
        "## 9. Management & governance\n"
        "## 10. Manufacturing / operating capabilities\n"
        "## 11. Growth strategy and future plans\n"
        "   (label management claims vs verified facts)\n"
        "## 12. Key business risks\n"
        "   (business-model / structural risks — not earnings scenarios yet)\n\n"
        "# Part B — Investment research\n"
        "## 13. Historical financial performance\n"
        "   (place {{TABLE:ciq_financials}} here if useful)\n"
        "## 14. Quarterly / operating trends\n"
        "   (only if quarterly evidence exists; else note gap)\n"
        "## 15. Financial quality\n"
        "   (cash flow, debt, WC, ROCE — reference scorecard/CIQ; "
        "{{TABLE:capex}} if useful)\n"
        "## 16. Forecasts & street expectations\n"
        "   ({{TABLE:ciq_estimates}}; label STREET)\n"
        "## 17. Valuation\n"
        "   (CIQ multiples + street targets only — no DCF; peer multiples if available)\n"
        "## 18. Investment thesis\n"
        "   (bull / base / bear — assessments, not invented ratings)\n"
        "## 19. Risks to the thesis\n"
        "   (how risks hit earnings, balance sheet, multiple)\n"
        "## 20. Catalysts & what would change the view\n"
        "## 21. Open questions & data gaps\n"
        "## 22. Conclusion\n\n"
        "Write like a research note an analyst would send a PM — synthesised judgment, not paste.\n"
        "Use compact markdown tables for snapshot, segment/geo mix, peers, capacity, management.\n"
        "Prioritize BUSINESS KNOWLEDGE MODULES + research plan when materials support them.\n"
        "Cite with [[src:file|excerpt]] hover markers only — never inline file paths as prose.\n"
        "No invented numbers. Separate FACT / MANAGEMENT CLAIM / ASSESSMENT.\n"
        "Python will append always-on CIQ financials, estimates, capex, scorecard, peer timelines.\n"
        "For optional packs, follow DATA AVAILABLE rules (skip CSR by default; rem only if "
        "exorbitant; auditor fees if spike/out-of-line).\n"
        "You may place {{TABLE:ciq_financials}}, {{TABLE:ciq_estimates}}, {{TABLE:capex}}, "
        "{{TABLE:peer_timeline}} markers where tables should appear in-body.\n"
        "END with a fenced report_include JSON listing optional ids to append, e.g.\n"
        "```report_include\n"
        '{"include": ["ratio_deep", "auditor_fees", "segments"], '
        '"omit": ["csr_esg", "remuneration"], '
        '"notes": "auditor fee spike; segments matter to mix"}\n'
        "```\n\n"
        f"{context}\n"
    )

    timeout = float(getattr(settings, "initiating_coverage_timeout_sec", 900.0) or 900.0)
    timeout = min(900.0, max(120.0, timeout))
    # 1L completion tokens — quality first; empty-content retries may bump further
    max_tokens = int(getattr(settings, "initiating_coverage_max_tokens", 100000) or 100000)
    max_tokens = max(8000, min(100000, max_tokens))

    raw = ""
    with llm_agent("initiating_coverage"):
        try:
            try:
                raw = llm.chat(
                    [
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    timeout=timeout,
                    max_tokens=max_tokens,
                    allow_reasoning_fallback=True,
                    reasoning_effort="none",
                )
            except TypeError:
                raw = llm.chat(
                    [
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    timeout=timeout,
                )
        except LLMAbortError:
            raise
        except Exception as exc:
            logger.info("initiating_coverage failed: %s", exc)
            _notify(progress_cb, f"Initiating coverage call failed — {exc}")
            return None

    md = _scrub_prose_refs((raw or "").strip())
    if len(md) < 500:
        _notify(progress_cb, "Initiating coverage too short — not replacing Python draft")
        return None

    if bool(getattr(settings, "llm_verify_numbers", True)):
        try:
            from iis.facts.store import build_fact_store
            from iis.facts.verifier import verify_llm_output

            facts = build_fact_store(session)
            evidence = [python_draft or "", context]
            md2 = verify_llm_output(
                session,
                md,
                evidence,
                agent="initiating_coverage",
                facts=facts,
            )
            if md2 and len(md2) > 400:
                md = _scrub_prose_refs(md2)
        except Exception:
            pass

    # Persist as dedicated artifact (must not fail the write-up if this throws)
    try:
        session.agent_artifacts = [
            a for a in session.agent_artifacts if a.artifact_type != "initiating_coverage"
        ]
        session.agent_artifacts.append(
            AgentArtifact(
                agent_id="initiating_coverage",
                artifact_type="initiating_coverage",
                payload={"ok": True, "markdown": md, "chars": len(md)},
                confidence=0.8,
                provenance=[company],
                source_tier=SourceTier.AGENT,
            )
        )
    except Exception as exc:
        logger.info("initiating_coverage artifact save failed (markdown kept): %s", exc)

    _notify(progress_cb, f"Initiating coverage ready · {len(md):,} chars")
    return md
