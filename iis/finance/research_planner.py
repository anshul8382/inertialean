"""One Hive call: basic company card → what we should look for in the docs.

Runs early in Analyze so the one-shot digest (and report) prioritize the right angles.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from iis.config import settings
from iis.models.artifacts import AgentArtifact
from iis.models.enums import EventType, PdfDocType, SourceTier
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

_SYSTEM = """You are a senior equity research planner for Indian listed companies.
Given ONLY a short company card (identity + a few CIQ facts + what PDFs are on file),
tell the analyst what to look for in the annual report, earnings concalls, and broker notes.

Rules:
- Do NOT invent ₹ figures or guidance numbers.
- Be specific to this company/sector when possible; avoid generic checklist fluff.
- Prefer 8–15 sharp bullets total across sections.
- Flag what would falsify a bull case and what management must prove.
"""


def _notify(cb: ProgressCb | None, msg: str) -> None:
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "—"
    v = float(x)
    if abs(v) <= 1.5:
        v *= 100.0
    return f"{v:.1f}%"


def _fmt_mn(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{float(x):,.0f}"


def build_company_card(session: ResearchSession) -> dict[str, Any]:
    """Compact Python facts for the planner prompt (no LLM)."""
    co = session.company
    name = co.name if co else (session.company_key or "Unknown")
    nse = getattr(co, "nse_ticker", None) if co else None
    bse = getattr(co, "bse_code", None) if co else None

    ratios: dict[str, Any] = {}
    try:
        from iis.finance.ratios import get_latest_ratio_summary

        ratios = get_latest_ratio_summary(session.financial_line_items) or {}
    except Exception:
        ratios = {}

    rev = ebitda_m = pat = None
    try:
        from iis.parsers.capital_iq.financials import get_latest_actual

        for key, dest in (
            ("total_revenue", "rev"),
            ("revenue", "rev"),
            ("ebitda", "ebitda"),
            ("net_income", "pat"),
            ("profit_after_tax", "pat"),
        ):
            item = get_latest_actual(session.financial_line_items, key)
            if item and item.value is not None:
                if dest == "rev" and rev is None:
                    rev = item.value
                elif dest == "ebitda" and ebitda_m is None:
                    ebitda_m = item.value
                elif dest == "pat" and pat is None:
                    pat = item.value
    except Exception:
        pass

    docs = {"annual_report": 0, "transcript": 0, "brokerage_report": 0, "other": 0}
    for d in session.pdf_documents or []:
        dt = str(getattr(d, "doc_type", "") or "")
        if dt == PdfDocType.ANNUAL_REPORT.value or "annual" in dt:
            docs["annual_report"] += 1
        elif dt == PdfDocType.TRANSCRIPT.value or "transcript" in dt or "concall" in dt:
            docs["transcript"] += 1
        elif dt == PdfDocType.BROKERAGE_REPORT.value or "broker" in dt:
            docs["brokerage_report"] += 1
        else:
            docs["other"] += 1

    card = {
        "name": name,
        "nse": nse,
        "bse": bse,
        "company_key": session.company_key,
        "ciq_line_items": len(session.financial_line_items or []),
        "revenue_mn": rev,
        "ebitda_mn": ebitda_m,
        "pat_mn": pat,
        "ebitda_margin": ratios.get("ebitda_margin"),
        "roce": ratios.get("roce"),
        "roe": ratios.get("roe"),
        "net_debt_equity": ratios.get("net_debt_to_equity") or ratios.get("debt_to_equity"),
        "pdfs": docs,
        "chunk_count": len(session.pdf_chunks or []),
    }
    return card


def _fmt_leverage(x: Any) -> str:
    if not isinstance(x, (int, float)):
        return "—"
    v = float(x)
    if abs(v) <= 1.5:
        return f"{v:.2f}x"
    return f"{v:.2f}"


def company_card_text(card: dict[str, Any]) -> str:
    pdfs = card.get("pdfs") or {}
    lines = [
        f"Company: {card.get('name')}",
        f"NSE: {card.get('nse') or '—'} · BSE: {card.get('bse') or '—'}",
        f"CIQ line items on file: {card.get('ciq_line_items') or 0}",
        (
            f"Latest (CIQ, ₹ mn): revenue {_fmt_mn(card.get('revenue_mn'))} · "
            f"EBITDA {_fmt_mn(card.get('ebitda_mn'))} · PAT {_fmt_mn(card.get('pat_mn'))}"
        ),
        (
            f"Ratios: EBITDA margin {_fmt_pct(card.get('ebitda_margin'))} · "
            f"ROCE {_fmt_pct(card.get('roce'))} · ROE {_fmt_pct(card.get('roe'))} · "
            f"leverage {_fmt_leverage(card.get('net_debt_equity'))}"
        ),
        (
            f"PDFs on file: AR={pdfs.get('annual_report', 0)}, "
            f"concalls={pdfs.get('transcript', 0)}, "
            f"broker={pdfs.get('brokerage_report', 0)} "
            f"(~{card.get('chunk_count', 0)} text chunks)"
        ),
    ]
    return "\n".join(lines)


def _parse_focus_bullets(raw: str) -> list[str]:
    bullets: list[str] = []
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith(("-", "*", "•")) or re.match(r"^\d+[.)]\s+", s):
            s = re.sub(r"^[-*•]\s*", "", s)
            s = re.sub(r"^\d+[.)]\s+", "", s)
            if len(s) > 12:
                bullets.append(s[:280])
    return bullets[:20]


def run_research_planner(
    session: ResearchSession,
    *,
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    """One Hive call → store ``research_plan`` artifact."""
    if not bool(getattr(settings, "research_planner_enabled", True)):
        return {"ok": False, "skipped": True, "reason": "research_planner_enabled=false"}

    from iis.context.manager import llm_agent
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    llm = get_llm_client(finance=True)
    if not llm.is_available():
        return {"ok": False, "skipped": True, "reason": "llm_unavailable"}

    card = build_company_card(session)
    card_txt = company_card_text(card)
    _notify(progress_cb, f"Hive research plan — what to look for · {card.get('name')}…")

    prompt = (
        f"{card_txt}\n\n"
        "Based on this company card ONLY, answer with these exact markdown headings (##):\n"
        "## What to look for in the annual report\n"
        "## What to look for in earnings concalls\n"
        "## What to check in broker notes (street vs filings)\n"
        "## Key risks / falsifiers\n"
        "## Priority questions (top 5)\n\n"
        "Tight bullets. Company-specific where possible. No invented numbers.\n"
    )

    timeout = float(getattr(settings, "research_planner_timeout_sec", 90.0) or 90.0)
    timeout = min(120.0, max(45.0, timeout))
    max_tokens = int(getattr(settings, "research_planner_max_tokens", 1800) or 1800)

    raw = ""
    with llm_agent("research_planner"):
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
                    allow_reasoning_fallback=False,
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
            logger.info("research_planner failed: %s", exc)
            return {"ok": False, "error": str(exc)[:300], "card": card}

    md = (raw or "").strip()
    focus = _parse_focus_bullets(md)
    result: dict[str, Any] = {
        "ok": bool(md) and len(md) > 120,
        "markdown": md,
        "focus_bullets": focus,
        "card": card,
        "path": "research_planner_one_shot",
    }

    session.agent_artifacts = [
        a for a in session.agent_artifacts if a.artifact_type != "research_plan"
    ]
    session.agent_artifacts.append(
        AgentArtifact(
            agent_id="research_planner",
            artifact_type="research_plan",
            payload=result,
            confidence=0.7 if result.get("ok") else 0.2,
            provenance=[card.get("name") or "company"],
            source_tier=SourceTier.AGENT,
        )
    )
    try:
        session.add_event(
            EventType.INFERENCE,
            agent="research_planner",
            details={
                "ok": result.get("ok"),
                "focus_n": len(focus),
                "chars_out": len(md),
            },
        )
    except Exception:
        pass

    _notify(
        progress_cb,
        f"Research plan ready · {len(focus)} focus bullets"
        if result.get("ok")
        else "Research plan empty/failed — continuing with defaults",
    )
    return result


def get_research_plan(session: ResearchSession) -> dict[str, Any] | None:
    for a in session.agent_artifacts or []:
        if a.artifact_type == "research_plan" and isinstance(a.payload, dict):
            return a.payload
    return None


def research_plan_markdown(session: ResearchSession) -> list[str]:
    plan = get_research_plan(session)
    if not plan:
        return []
    md = (plan.get("markdown") or "").strip()
    if len(md) < 80:
        return []
    lines = [
        "_Hive planner: basic company card → what to look for in AR / concalls / brokers._",
        "",
    ]
    lines.extend(md.splitlines())
    lines.append("")
    return lines


def research_plan_focus_block(session: ResearchSession, *, max_bullets: int = 12) -> str:
    """Compact focus list for injection into the one-shot digest prompt."""
    plan = get_research_plan(session)
    if not plan or not plan.get("ok"):
        return ""
    bullets = list(plan.get("focus_bullets") or [])[:max_bullets]
    if not bullets and plan.get("markdown"):
        bullets = _parse_focus_bullets(str(plan["markdown"]))[:max_bullets]
    if not bullets:
        return ""
    return "Priority focus (from research planner):\n" + "\n".join(f"- {b}" for b in bullets)
