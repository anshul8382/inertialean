"""Classify chat turns into durable notes / analysis instructions / actions.

Heuristic-first (fast, offline). Optional LLM polish when Hive/LLM is configured.
Never auto-persists company rules without an explicit Remember:/Rule: prefix —
instead returns *proposals* the UI can confirm.
"""
from __future__ import annotations

import json
import re
from typing import Any

from iis.models.session import ResearchSession

# Proposal kinds the UI understands
# - company_rule: persist for this company forever (needs confirm unless explicit Remember:)
# - session_directive: apply to this analysis / report
# - analyst_note: append to company analyst notes
# - rerun_analysis: re-run Python + agents
# - regenerate_report: refresh markdown from current session
# - update_report: reingest + analyze + regenerate (post new files)


_EXPLICIT_RULE = re.compile(
    r"^\s*(?:remember|rule|note for (?:analyst|research)|context)\s*[:\-]\s*(.+)$",
    re.I | re.S,
)
_NOTE_CUE = re.compile(
    r"\b(note to self|save (?:this )?note|analyst note|for (?:my|the) notes)\b",
    re.I,
)
_ANALYSIS_CUE = re.compile(
    r"\b(for this analysis|in (?:the|this) report|when (?:you |we )?analy[sz]e|"
    r"focus (?:the )?analysis|emphasize|weight|ignore|exclude|don'?t use|"
    r"use (?:standalone|consolidated)|treat .{1,40} as|assume )\b",
    re.I,
)
_COMPANY_CUE = re.compile(
    r"\b(for this company|for this stock|every (?:time|run|session)|always (?:use|treat|ignore)|"
    r"company rule|save (?:this )?for (?:future|upcoming) sessions?)\b",
    re.I,
)
_RERUN = re.compile(
    r"\b(rerun analysis|re-run analysis|run analysis again|reanaly[sz]e|re-analy[sz]e)\b",
    re.I,
)
_REGEN = re.compile(
    r"\b(regenerate report|refresh (?:the )?report|update (?:the )?report|"
    r"rebuild (?:the )?report)\b",
    re.I,
)
_UPDATE_AFTER_FILES = re.compile(
    r"\b(update (?:the )?report with (?:new |these )?files|new (?:peer )?files? (?:are |is )?on|"
    r"include (?:the )?new (?:uploads?|documents?|files?))\b",
    re.I,
)
_QUESTION = re.compile(r"\?\s*$")


def _prop(
    kind: str,
    text: str,
    *,
    title: str,
    confirm: bool = True,
    auto: bool = False,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"{kind}-{abs(hash(text + kind)) % 10_000_000:07d}",
        "kind": kind,
        "text": text.strip(),
        "title": title,
        "needs_confirm": confirm and not auto,
        "auto": auto,
        "meta": meta or {},
    }


def classify_chat_turn(session: ResearchSession, question: str) -> list[dict[str, Any]]:
    """Return zero or more proposals for this user message."""
    q = (question or "").strip()
    if not q:
        return []
    proposals: list[dict[str, Any]] = []
    lower = q.lower()

    if _RERUN.search(q):
        proposals.append(
            _prop(
                "rerun_analysis",
                q,
                title="Re-run full analysis with data on file",
                confirm=False,
                auto=True,
            )
        )
    if _UPDATE_AFTER_FILES.search(q) or (
        _REGEN.search(q) and any(w in lower for w in ("file", "upload", "peer", "document", "new"))
    ):
        proposals.append(
            _prop(
                "update_report",
                q,
                title="Re-ingest → analyze → regenerate report",
                confirm=False,
                auto=True,
            )
        )
    elif _REGEN.search(q):
        proposals.append(
            _prop(
                "regenerate_report",
                q,
                title="Regenerate research report from current session",
                confirm=False,
                auto=True,
            )
        )

    explicit = _EXPLICIT_RULE.match(q)
    if explicit:
        body = explicit.group(1).strip()
        scope = "company" if _COMPANY_CUE.search(q) else ("session" if _ANALYSIS_CUE.search(q) else "company")
        kind = "company_rule" if scope == "company" else "session_directive"
        proposals.append(
            _prop(
                kind,
                body,
                title=("Save as company rule (future sessions)" if kind == "company_rule"
                       else "Use as session analysis instruction"),
                confirm=False,
                auto=True,
                meta={"scope": scope},
            )
        )
        return _dedupe(proposals)

    # Questions → usually one-shot; only propose notes if they asked to save
    if _QUESTION.search(q) and not _NOTE_CUE.search(q) and not _ANALYSIS_CUE.search(q):
        return _dedupe(proposals)

    if _NOTE_CUE.search(q) or (len(q) > 40 and _COMPANY_CUE.search(q) and "note" in lower):
        proposals.append(
            _prop(
                "analyst_note",
                q,
                title="Append to analyst notes for this company?",
                confirm=True,
            )
        )

    if _COMPANY_CUE.search(q) and not _QUESTION.search(q) and len(q) < 500:
        proposals.append(
            _prop(
                "company_rule",
                q,
                title="Save as company rule for every future analysis?",
                confirm=True,
                meta={"scope": "company"},
            )
        )
    elif _ANALYSIS_CUE.search(q) and not _QUESTION.search(q) and len(q) < 500:
        proposals.append(
            _prop(
                "session_directive",
                q,
                title="Include this in the next analysis / report?",
                confirm=True,
                meta={"scope": "session"},
            )
        )

    # Optional LLM refinement when available and message looks instructional
    if not proposals and len(q) > 24 and not _QUESTION.search(q):
        llm_props = _llm_propose(session, q)
        proposals.extend(llm_props)

    return _dedupe(proposals)


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in items:
        key = f"{p['kind']}|{p['text'][:120].lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _llm_propose(session: ResearchSession, question: str) -> list[dict[str, Any]]:
    """Ask LLM to classify; fail soft to []."""
    try:
        from iis.llm.factory import get_llm_client
    except Exception:
        return []

    company = session.company.name if session.company else "this company"
    prompt = (
        f"You route analyst chat for equity research on {company}.\n"
        "User message:\n"
        f"{question[:800]}\n\n"
        "Return JSON with key proposals (array). Each item: kind, text, title, needs_confirm.\n"
        "kind one of: company_rule, session_directive, analyst_note, "
        "rerun_analysis, regenerate_report, update_report.\n"
        "Only propose lasting instructions or refresh actions. "
        "For pure factual questions return {\"proposals\":[]}."
    )
    try:
        client = get_llm_client()
        if not client.is_available():
            return []
        data = client.extract_json(
            prompt,
            system="You classify research-chat intent. Reply with JSON only.",
        )
        raw = (data or {}).get("proposals") or []
        out: list[dict[str, Any]] = []
        for item in raw[:3]:
            kind = (item.get("kind") or "").strip()
            if kind in ("", "none", "qa"):
                continue
            text = (item.get("text") or question).strip()
            title = (item.get("title") or f"Apply as {kind.replace('_', ' ')}?").strip()
            out.append(
                _prop(
                    kind,
                    text,
                    title=title,
                    confirm=bool(item.get("needs_confirm", True)),
                    meta={"scope": "company" if kind == "company_rule" else "session"},
                )
            )
        return out
    except Exception:
        return []


def apply_proposal(session: ResearchSession, proposal: dict[str, Any]) -> dict[str, Any]:
    """Persist a confirmed proposal (rules / notes). Actions are executed by orchestrator/UI."""
    from iis.context.manager import add_directive, get_analyst_notes, set_analyst_notes

    kind = proposal.get("kind")
    text = (proposal.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "empty proposal"}

    if kind == "company_rule":
        rule = add_directive(session, text, scope="company", source="chat_confirm")
        return {"ok": True, "kind": kind, "rule": rule}
    if kind == "session_directive":
        rule = add_directive(session, text, scope="session", source="chat_confirm")
        return {"ok": True, "kind": kind, "rule": rule}
    if kind == "analyst_note":
        if not session.company_key:
            return {"ok": False, "error": "no company"}
        prev = get_analyst_notes(session.company_key)
        merged = (prev + "\n\n" if prev else "") + text
        set_analyst_notes(session.company_key, merged.strip())
        return {"ok": True, "kind": kind, "notes_len": len(merged)}
    if kind in ("rerun_analysis", "regenerate_report", "update_report"):
        return {"ok": True, "kind": kind, "action": kind}
    return {"ok": False, "error": f"unknown kind {kind}"}
