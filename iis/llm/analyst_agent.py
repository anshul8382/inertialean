"""Post-collection LLM analyst: request Python tools → refine → answer/report.

Pipeline contract:
  1. Python has already ingested files and built metrics / dossier / artifacts.
  2. This agent may ONLY add interpretation via tools that read that data.
  3. If Ollama is unavailable or times out, callers keep the Python draft.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from iis.config import settings
from iis.llm.finance_client import FinanceOllamaClient
from iis.llm.python_tools import run_tool, tool_catalog_text
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are an equity research analyst writing from AVAILABLE INFORMATION ONLY.\n"
    "Python has collected the data (Capital IQ, annual reports, concalls, ratios, "
    "dossier). You process, organise, and analyse that provided information and "
    "produce the final report or answer.\n\n"
    "HARD RULES — no exceptions:\n"
    "- Every number, claim, peer, rating, date, and outlook must come from the "
    "provided draft or from Python tool results.\n"
    "- You MAY interpret and analyse what is provided (e.g. what a margin or "
    "leverage ratio implies, how guidance compares to actuals).\n"
    "- You MUST NOT make assumptions, forecasts, or fill gaps with guesses.\n"
    "- If information is missing, omit that point or state that it is not in the "
    "uploaded materials — never invent it.\n"
    "- No 'analyst to complete', placeholders, or speculative Buy/Hold/Sell "
    "unless that rating already appears in provided broker materials.\n\n"
    "- Cite fact IDs from the FACT SHEET after numbers, e.g. 'Revenue ₹108,231 mn [F3]'. "
    "Numbers that cannot be traced to provided data are deleted by an automatic verifier.\n"
    "- Follow the RESEARCH RULES and USER DIRECTIVES in the context block.\n"
    "- Peers: discuss only user-confirmed peers whose files are on record (get_peers).\n\n"
    "Tool protocol — reply with ONE JSON object only:\n"
    '  {"tool":"<name>","args":{...}}  to fetch more provided data, or\n'
    '  {"tool":null,"answer":"<markdown>"}  for the final outcome.\n'
    "Concise structured markdown. Omit empty sections."
)


def _enabled() -> bool:
    return bool(getattr(settings, "llm_analyst_enabled", True))


def _max_rounds() -> int:
    return max(1, int(getattr(settings, "llm_analyst_max_tool_rounds", 3) or 3))


def _timeout() -> float:
    return float(getattr(settings, "llm_analyst_timeout_sec", 45.0) or 45.0)


def _chat_enabled() -> bool:
    return bool(getattr(settings, "chat_llm_analyst", True))


def _chat_max_rounds() -> int:
    return max(
        1,
        int(
            getattr(settings, "chat_llm_analyst_max_tool_rounds", None)
            or getattr(settings, "llm_analyst_max_tool_rounds", 4)
            or 4
        ),
    )


def _chat_timeout() -> float:
    return float(
        getattr(settings, "chat_llm_analyst_timeout_sec", None)
        or getattr(settings, "llm_analyst_timeout_sec", 180.0)
        or 180.0
    )


def _parse_action(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    # Prefer fenced JSON
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0), strict=False)
    except json.JSONDecodeError:
        try:
            data = json.loads(m.group(0).replace(",}", "}").replace(",]", "]"), strict=False)
        except Exception:
            a = re.match(r'\s*\{\s*"tool"\s*:\s*null\s*,\s*"answer"\s*:\s*"([\s\S]*)"\s*\}\s*$', m.group(0))
            if not a:
                return None
            body = a.group(1).replace('\\"', '"').replace("\\n", "\n")
            return {"tool": None, "answer": body}
    if not isinstance(data, dict):
        return None
    return data


def _chat(
    llm: FinanceOllamaClient,
    messages: list[dict[str, str]],
    *,
    timeout: float | None = None,
) -> str:
    return llm.chat(messages, temperature=0.15, timeout=timeout if timeout is not None else _timeout())


def run_tool_loop(
    session: ResearchSession,
    *,
    task: str,
    seed_context: str = "",
    max_rounds: int | None = None,
    agent: str = "analyst",
    extra_evidence: list[str] | None = None,
    require_analyze_flag: bool = True,
    timeout_sec: float | None = None,
) -> str | None:
    """Run LLM ↔ Python tool loop; return verified final markdown or None."""
    if require_analyze_flag and not _enabled():
        return None
    if not require_analyze_flag and not _chat_enabled():
        return None
    try:
        llm = FinanceOllamaClient()
        if not llm.is_available():
            return None
    except Exception:
        return None

    from iis.context.manager import build_context_block, llm_agent
    from iis.facts.store import build_fact_store, fact_sheet_text

    company = session.company.name if session.company else "the company"
    rounds = max_rounds if max_rounds is not None else _max_rounds()
    catalog = tool_catalog_text()
    seed = (seed_context or "")[:14000]
    chat_timeout = timeout_sec
    try:
        facts = build_fact_store(session)
    except Exception:
        facts = []
    fact_sheet = fact_sheet_text(facts) if facts else ""
    context_block = build_context_block(session)
    evidence: list[str] = [seed, fact_sheet, context_block, *(extra_evidence or [])]

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM + ("\n\n" + context_block if context_block else "")},
        {
            "role": "user",
            "content": (
                f"Company: {company}\n\n"
                f"Task:\n{task}\n\n"
                f"{catalog}\n\n"
                f"{fact_sheet}\n\n"
                f"Seed context from Python (may be incomplete — request tools if needed):\n"
                f"{seed or '(none — use tools)'}\n\n"
                "Start by calling a tool if you need data, or return {\"tool\":null,\"answer\":...} "
                "if the seed is enough."
            ),
        },
    ]
    with llm_agent(agent):
        answer = _loop(
            session,
            llm,
            messages,
            rounds,
            evidence,
            timeout=chat_timeout,
        )
    if not answer:
        return None
    return _verify(answer, evidence, facts, session=session, agent=agent)


def _verify(answer: str, evidence: list[str], facts: list[dict[str, Any]], *, session: ResearchSession, agent: str) -> str | None:
    from iis.facts.verifier import verify_llm_output

    return verify_llm_output(session, answer, evidence, agent=agent, facts=facts)


def _loop(
    session: ResearchSession,
    llm: FinanceOllamaClient,
    messages: list[dict[str, str]],
    rounds: int,
    evidence: list[str],
    *,
    timeout: float | None = None,
) -> str | None:
    from iis.llm.gate import LLMAbortError, get_llm_gate

    tool_trace: list[str] = []
    gate = get_llm_gate()
    for i in range(rounds):
        gate.check_abort()
        try:
            raw = _chat(llm, messages, timeout=timeout)
        except LLMAbortError:
            raise
        except Exception as exc:
            logger.info("LLM analyst chat failed: %s", exc)
            break
        action = _parse_action(raw)
        if not action:
            # Model returned prose — accept if long enough
            if raw and len(raw.strip()) > 80 and "{" not in raw[:40]:
                return raw.strip()
            messages.append({"role": "assistant", "content": raw or ""})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        'Respond with JSON only: either {"tool":"...","args":{}} '
                        'or {"tool":null,"answer":"...markdown..."}'
                    ),
                }
            )
            continue

        tool_name = action.get("tool")
        if not tool_name:
            answer = action.get("answer") or action.get("markdown") or ""
            if isinstance(answer, str) and len(answer.strip()) > 40:
                return answer.strip()
            break

        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        result = run_tool(session, str(tool_name), args)
        evidence.append(result)
        tool_trace.append(str(tool_name))
        messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)[:2000]})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"Tool `{tool_name}` result:\n{result[:9000]}\n\n"
                    "Call another tool or return {\"tool\":null,\"answer\":...}."
                ),
            }
        )
        logger.info("LLM analyst round %s/%s used tool %s", i + 1, rounds, tool_name)

    gate.check_abort()
    # Final forced answer attempt
    messages.append(
        {
            "role": "user",
            "content": (
                "No more tools. Return JSON {\"tool\":null,\"answer\":\"<final markdown>\"} "
                "using only evidence already retrieved."
            ),
        }
    )
    try:
        raw = _chat(llm, messages)
        action = _parse_action(raw)
        if action and not action.get("tool"):
            answer = action.get("answer") or ""
            if isinstance(answer, str) and len(answer.strip()) > 40:
                return answer.strip()
        if raw and len(raw.strip()) > 120:
            return raw.strip()
    except LLMAbortError:
        raise
    except Exception:
        pass
    logger.info("LLM analyst produced no answer (tools used: %s)", tool_trace)
    return None


def data_status_block(session: ResearchSession) -> str:
    """What has been computed / is on file — authoritative, so the LLM never guesses 'not run'."""
    lines = ["=== DATA STATUS (authoritative — what exists on file) ==="]
    q = session.market_quote
    lines.append(f"- Live price: {'₹%s (%s)' % (q.price, q.source) if q and q.price else 'price unavailable'}")
    arts = {a.artifact_type: a.payload for a in session.agent_artifacts}
    recon = arts.get("reconciliation") or {}
    checks = recon.get("checks") or []
    if checks:
        lines.append("- Reconciliation checks RUN: " + "; ".join(
            f"{c.get('check')}: {c.get('status')}" for c in checks[:10]))
    else:
        lines.append("- Reconciliation checks: not computed")
    try:
        from iis.finance.guidance_track import guidance_track_rows, guidance_track_summary

        rows = guidance_track_rows(session.guidance_records, session.financial_line_items)
        summ = guidance_track_summary(rows)
        lines.append(
            f"- CIQ Guidance sheet: {len(session.guidance_records)} record(s); "
            f"{summ['tracked']} guidance-vs-actual pair(s) — {summ['headline']}"
        )
    except Exception:
        lines.append(f"- CIQ Guidance sheet: {len(session.guidance_records)} record(s)")
    lines.append(f"- CIQ consensus rows: {len(session.consensus_metrics)}")
    bv = arts.get("brokerage_view") or {}
    if bv:
        rows = bv.get("matrix") or []
        stale = sum(1 for r in rows if r.get("stale"))
        lines.append(f"- Broker reports parsed: {len(rows)} ({len(rows) - stale} current, {stale} stale)")
    try:
        from iis.finance.competitor_discovery import confirmed_peer_libraries

        lines.append(f"- Confirmed peers with CIQ on file: {len(confirmed_peer_libraries(session))}")
    except Exception:
        pass
    return "\n".join(lines)


def write_final_report(session: ResearchSession, python_draft: str) -> str | None:
    """Equity analyst: report from provided info only; analyse, do not assume."""
    task = (
        "Create the FINAL equity research report in markdown from available "
        "information only.\n"
        "You may call Python tools (especially get_research_pack) for more pack slices.\n"
        "Packs on file: AR (filings), concall (mgmt guidance/Q&A), broker (STREET only).\n"
        "Rules:\n"
        "- Filings/CIQ/concall explicit claims = primary evidence; cite claim IDs like [AB-…].\n"
        "- Broker estimates/TP/ratings = STREET; label as brokerage views; test vs AR+concall.\n"
        "- Prefer explicit concall guidance over qualitative/aspirational.\n"
        "- Use mgmt_vs_broker gaps when present.\n"
        "- Do NOT invent numbers. If a section has no evidence, omit it.\n"
        "Write like an *initiating coverage* note. Order (omit if empty):\n"
        "1) Business overview; 2) Investment case; 3) Rejection/bear; 4) Open questions;\n"
        "5) Financials testing the story; 6) Guidance track + concall outlook;\n"
        "7) Street thesis stress-test; 8) Valuation; 9) Risks & catalysts; 10) Data gaps.\n"
        "Do not start with financial tables. Confirmed peers only.\n"
        "DATA STATUS is authoritative. Do not refer to earlier chat turns."
    )
    pack_seed = ""
    try:
        from iis.finance.packs import packs_seed_markdown

        pack_seed = packs_seed_markdown(session, max_chars=8000)
    except Exception:
        pack_seed = ""
    seed = (
        data_status_block(session)
        + ("\n\n" + pack_seed if pack_seed else "")
        + "\n\n=== PYTHON DRAFT REPORT (facts already collected) ===\n"
        + (python_draft or "")[:7000]
    )
    return run_tool_loop(
        session,
        task=task,
        seed_context=seed,
        max_rounds=_max_rounds() + 1,
        agent="analyst_report",
        extra_evidence=[python_draft or "", pack_seed],
    )

def answer_user_question(
    session: ResearchSession,
    question: str,
    *,
    python_draft: str | None = None,
) -> str | None:
    """Chat path: LLM accepts question, may call Python tools, refines, responds.

    Independent of Analyze's ``llm_analyst_enabled`` (that flag only gates report polish).
    """
    if not _chat_enabled():
        return None
    task = (
        f"As an equity research analyst, answer from PROVIDED information only.\n"
        f"Question: {question}\n"
        f"First decide which Python tools you need (metrics, packs, PDF search, peers, "
        f"guidance, ratios). Call them, then write a clear markdown answer.\n"
        f"You may analyse and interpret what those materials show.\n"
        f"Do not assume or invent anything not returned by tools or the draft.\n"
        f"If the data is not available, say so clearly and list what is missing.\n"
        f"Cite file/page or claim IDs when quoting."
    )
    seed = ""
    if python_draft:
        seed = data_status_block(session) + "\n\n=== PYTHON DRAFT ANSWER ===\n" + python_draft[:8000]
    else:
        try:
            from iis.llm.python_tools import run_tool as _rt

            bits = [
                "=== PREFETCH (call more tools if needed) ===",
                f"metrics: {_rt(session, 'get_key_metrics', {})[:2000]}",
                f"flags: {_rt(session, 'get_flags', {})[:1000]}",
            ]
            try:
                bits.append(f"research_pack: {_rt(session, 'get_research_pack', {'pack': 'ar'})[:2500]}")
            except Exception:
                pass
            try:
                bits.append(f"concall_pack: {_rt(session, 'get_research_pack', {'pack': 'concall'})[:2000]}")
            except Exception:
                pass
            seed = data_status_block(session) + "\n\n" + "\n".join(bits)
        except Exception:
            seed = data_status_block(session)
    return run_tool_loop(
        session,
        task=task,
        seed_context=seed,
        max_rounds=_chat_max_rounds(),
        agent="analyst_chat",
        extra_evidence=[python_draft or ""],
        require_analyze_flag=False,
        timeout_sec=_chat_timeout(),
    )
