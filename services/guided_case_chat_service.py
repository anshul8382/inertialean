"""
Guided case chat — general shell with optional module picker + Auto router.

Suggest-only turns. Never mutates cashflows/trades.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ALLOWED_ACTION_TYPES = frozenset(
    {"open_url", "rerun_diagnose", "mark_step_complete", "show_fact", "clarify_module"}
)


def list_modules() -> List[Dict[str, str]]:
    from services.guided_chat_modules import registry

    return [
        {"module_id": m.module_id, "display_name": m.display_name}
        for m in registry.all_modules()
    ]


def route_module(
    *,
    module_id: Optional[str],
    message: str,
    page_hint: Optional[str] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Resolve module. Returns (module_id, clarify_turn_or_None).
    module_id 'auto' or empty → score route_hints.
    """
    from services.guided_chat_modules import registry

    mid = (module_id or "").strip().lower()
    if mid and mid not in ("auto", ""):
        if registry.get(mid):
            return mid, None
        return None, _clarify_turn("Unknown module. Pick one from the list.")

    text = f"{page_hint or ''} {message or ''}".lower()
    scores: List[Tuple[int, str]] = []
    for mod in registry.all_modules():
        score = 0
        for hint in mod.route_hints or []:
            if hint.lower() in text:
                score += 2 if len(hint) > 4 else 1
        if page_hint and mod.module_id in (page_hint or ""):
            score += 5
        scores.append((score, mod.module_id))
    scores.sort(reverse=True)
    if not scores:
        return None, _clarify_turn("No guided modules are registered yet.")
    best, second = scores[0], scores[1] if len(scores) > 1 else (0, "")
    if best[0] <= 0 or (second and best[0] == second[0]):
        return None, _clarify_turn(
            "Which topic are you asking about?",
            modules=list_modules(),
        )
    return best[1], None


def _clarify_turn(message: str, modules: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    return {
        "step_id": "clarify_module",
        "message": message,
        "why": "Need a module before loading client facts.",
        "cited_fact_ids": [],
        "primary_action": {
            "type": "clarify_module",
            "label": "Pick a module",
            "modules": modules or list_modules(),
        },
        "secondary_actions": [],
        "asks_user": "Select a module or rephrase with keywords like cashflow, trades, opening book.",
        "done_when": None,
        "module_id": None,
        "suggest_only": True,
    }


def _validate_turn(raw: Dict[str, Any], allowlisted_facts: set[str]) -> Dict[str, Any]:
    action = raw.get("primary_action") or {}
    atype = action.get("type") or "show_fact"
    if atype not in ALLOWED_ACTION_TYPES:
        atype = "show_fact"
    cited = [c for c in (raw.get("cited_fact_ids") or []) if c in allowlisted_facts][:12]
    return {
        "step_id": str(raw.get("step_id") or "guide")[:80],
        "message": str(raw.get("message") or "")[:2000],
        "why": str(raw.get("why") or "")[:1000],
        "cited_fact_ids": cited,
        "primary_action": {
            "type": atype,
            "label": str(action.get("label") or "Continue")[:120],
            "url": str(action.get("url") or "")[:300] if atype == "open_url" else None,
            "modules": action.get("modules") if atype == "clarify_module" else None,
        },
        "secondary_actions": [],
        "asks_user": (str(raw.get("asks_user"))[:500] if raw.get("asks_user") else None),
        "done_when": (str(raw.get("done_when"))[:500] if raw.get("done_when") else None),
        "suggest_only": True,
    }


def _deterministic_fallback(case: Dict[str, Any], step_id: Optional[str]) -> Dict[str, Any]:
    # Client data integrity pack: sections first
    sections = case.get("sections") or []
    if sections and case.get("module_id") == "client_data_integrity":
        focus = next(
            (s for s in sections if s.get("status") in ("not_matched", "needs_review")),
            None,
        )
        if not focus:
            focus = sections[0]
        url = focus.get("detail_url") or (case.get("deep_links") or {}).get("data_integrity")
        return {
            "step_id": f"section:{focus.get('id')}",
            "message": (
                f"Overall {case.get('overall_label') or case.get('overall_status')}. "
                f"Focus: {focus.get('title')} — {focus.get('summary') or focus.get('status')}. "
                "Open the section, fix data yourself, then re-check."
            ),
            "why": "Deterministic fallback from data-integrity sections.",
            "cited_fact_ids": [
                it.get("fact_id") for it in (focus.get("items") or [])[:5] if it.get("fact_id")
            ],
            "primary_action": {
                "type": "open_url",
                "label": f"Open {focus.get('title')}",
                "url": url,
            },
            "secondary_actions": [],
            "asks_user": None,
            "done_when": "Return after edits and re-check.",
            "suggest_only": True,
            "module_id": case.get("module_id"),
        }

    checklist = case.get("checklist") or []
    step = None
    if step_id:
        step = next((s for s in checklist if s.get("step_id") == step_id), None)
    if not step and checklist:
        step = checklist[0]
    if not step:
        return {
            "step_id": "explain_status",
            "message": f"Status is {case.get('status') or case.get('overall_status')}. Open the integrity page and edit data yourself if needed.",
            "why": "Deterministic fallback (LLM unavailable).",
            "cited_fact_ids": [],
            "primary_action": {
                "type": "open_url",
                "label": "Open integrity page",
                "url": (case.get("deep_links") or {}).get("integrity")
                or (case.get("deep_links") or {}).get("data_integrity"),
            },
            "secondary_actions": [],
            "asks_user": None,
            "done_when": "Return after edits and re-check.",
            "suggest_only": True,
            "module_id": case.get("module_id"),
        }
    return {
        "step_id": step.get("step_id"),
        "message": f"{step.get('title')}. {step.get('hint') or ''} System only suggests — you make any edits.",
        "why": "Deterministic checklist step.",
        "cited_fact_ids": [step["fact_id"]] if step.get("fact_id") else [],
        "primary_action": {
            "type": "open_url" if step.get("url") else "rerun_diagnose",
            "label": "Open suggested screen" if step.get("url") else "Re-check after your edits",
            "url": step.get("url"),
        },
        "secondary_actions": [],
        "asks_user": "Did you complete this check?",
        "done_when": "Click Continue / re-check on the integrity page.",
        "suggest_only": True,
        "module_id": case.get("module_id"),
    }


def handle_chat(
    *,
    client_id: int,
    message: str,
    module_id: Optional[str] = None,
    page_hint: Optional[str] = None,
    step_id: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    from services.guided_chat_modules import registry

    resolved, clarify = route_module(module_id=module_id, message=message, page_hint=page_hint)
    if clarify:
        return clarify
    assert resolved
    mod = registry.get(resolved)
    if not mod:
        return _clarify_turn("Module not found.")

    case = mod.build_case_pack(client_id)
    allowlisted = set()
    for key in (
        "large_day_deviations",
        "small_day_deviations",
        "suspect_days",
        "suspect_cashflows",
        "suspect_trades",
    ):
        for row in case.get(key) or []:
            if row.get("fact_id"):
                allowlisted.add(row["fact_id"])
            if row.get("id") is not None and key == "suspect_cashflows":
                allowlisted.add(f"cf:{row['id']}")
            if row.get("id") is not None and key == "suspect_trades":
                allowlisted.add(f"tx:{row['id']}")
    for sec in case.get("sections") or []:
        for row in sec.get("items") or []:
            if row.get("fact_id"):
                allowlisted.add(row["fact_id"])
    g8 = case.get("g8") or {}
    for key in ("suspect_cashflows", "suspect_trades", "large_day_deviations"):
        for row in g8.get(key) or []:
            if row.get("fact_id"):
                allowlisted.add(row["fact_id"])

    guidelines = ""
    try:
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / mod.guidelines_relpath
        guidelines = path.read_text(encoding="utf-8")[:4000]
    except Exception:
        guidelines = "Suggest only. Cite facts. Never claim you edited data."

    turn: Dict[str, Any]
    try:
        from services.assistant_llm import generate_json, local_ai_available

        if not local_ai_available():
            turn = _deterministic_fallback(case, step_id)
        else:
            prompt = (
                "You are an ops guide. Suggest ONE next step. Never claim you fixed data. "
                "User must edit in the app. Return JSON only.\n"
                f"POLICY:\n{guidelines}\n"
                f"CASE_PACK:\n{case}\n"
                f"CURRENT_STEP:{step_id or ''}\n"
                f"USER:{message}\n"
                "JSON keys: step_id, message, why, cited_fact_ids, primary_action"
                "{type,label,url}, asks_user, done_when. "
                "primary_action.type in open_url|rerun_diagnose|mark_step_complete|show_fact."
            )
            raw = generate_json(prompt, temperature=0.1, max_tokens=400) or {}
            turn = _validate_turn(raw if isinstance(raw, dict) else {}, allowlisted)
            if not turn.get("message"):
                turn = _deterministic_fallback(case, step_id)
    except Exception as exc:
        logger.warning("Guided chat LLM failed: %s", exc)
        turn = _deterministic_fallback(case, step_id)

    turn["module_id"] = resolved
    turn["case_status"] = case.get("status") or case.get("overall_status")
    try:
        from services.audit_service import log_audit_event

        log_audit_event(
            "guided_case_chat_turn",
            user_id=user_id,
            resource_type="guided_chat",
            resource_id=resolved,
            client_id=client_id,
            details={"step_id": turn.get("step_id"), "module_id": resolved},
        )
    except Exception:
        pass
    return turn
