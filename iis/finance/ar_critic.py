"""Independent reviewer — Hive challenges AR deep-read bullets before the report."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from iis.config import settings
from iis.finance.inertia_methodology import CRITIC_RUBRIC, gaps_prompt_block
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]


def _notify(cb: ProgressCb | None, msg: str) -> None:
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def _parse_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _handlers_blob(handlers: dict[str, Any]) -> str:
    lines: list[str] = []
    for hid, h in (handlers or {}).items():
        if not isinstance(h, dict):
            continue
        lines.append(f"## {h.get('title') or hid}")
        for b in (h.get("bullets") or [])[:30]:
            lines.append(f"- {b}")
        recon = h.get("capex_reconcile")
        if isinstance(recon, dict):
            lines.append("Capex reconcile:")
            for m in (recon.get("ar_capex_mentions") or [])[:6]:
                lines.append(f"  AR: {m}")
            for m in (recon.get("ciq_capex_lines") or [])[:6]:
                lines.append(f"  {m}")
        lines.append("")
    return "\n".join(lines)


def run_ar_critic(
    session: ResearchSession,
    *,
    filename: str,
    handlers: dict[str, Any],
    relevance: dict[str, Any] | None = None,
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    """Hive independent reviewer over structured AR bullets."""
    if not bool(getattr(settings, "ar_critic_llm_enabled", True)):
        return {"ok": False, "skipped": True, "reason": "ar_critic_llm_enabled=false"}
    if not handlers:
        return {"ok": False, "error": "no_handlers", "file": filename}

    from iis.context.manager import llm_agent
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    llm = get_llm_client(finance=True)
    if not llm.is_available():
        return {"ok": False, "error": "llm_unavailable", "file": filename}

    company = session.company.name if session.company else (session.company_key or "Company")
    timeout = float(getattr(settings, "ar_critic_timeout_sec", 120.0) or 120.0)
    blob = _handlers_blob(handlers)
    if len(blob) < 80:
        return {"ok": False, "error": "empty_bullets", "file": filename}

    index_lines = []
    for row in (relevance or {}).get("sections") or []:
        if isinstance(row, dict):
            index_lines.append(
                f"- {row.get('section')}: {row.get('decision')} ({row.get('mode')})"
            )
    index_block = "\n".join(index_lines[:80]) or "(no relevance index)"

    _notify(progress_cb, f"AR critic · independent review · {filename[:40]}")
    system = CRITIC_RUBRIC
    user = (
        f"Company: {company}\nFile: {filename}\n\n"
        f"{gaps_prompt_block(session)}\n\n"
        "AR section relevance index:\n"
        f"{index_block}\n\n"
        "=== DEEP-READ BULLETS TO REVIEW ===\n"
        f"{blob}\n"
    )
    with llm_agent("ar_critic"):
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.15,
                timeout=timeout,
                max_tokens=int(getattr(settings, "ar_critic_max_tokens", 2500) or 2500),
                allow_reasoning_fallback=False,
                format_json=True,
            )
        except TypeError:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.15,
                timeout=timeout,
                format_json=True,
            )
        except LLMAbortError:
            raise
        except Exception as exc:
            logger.info("AR critic failed %s: %s", filename, exc)
            return {"ok": False, "error": str(exc)[:300], "file": filename}

    data = _parse_json(raw or "")
    return {
        "ok": True,
        "file": filename,
        "headlines": list(data.get("headlines") or [])[:12],
        "facts_ok": list(data.get("facts_ok") or [])[:20],
        "assumptions": list(data.get("assumptions") or [])[:20],
        "contradictions": list(data.get("contradictions") or [])[:16],
        "drop_bullets": list(data.get("drop_bullets") or [])[:20],
        "missing_checklist_ids": [str(x) for x in (data.get("missing_checklist_ids") or [])][:16],
        "gap_fill_sections": [str(x) for x in (data.get("gap_fill_sections") or [])][:12],
        "confidence": data.get("confidence"),
        "raw_ok": bool(data),
    }


def critic_markdown(critic: dict[str, Any] | None) -> list[str]:
    if not critic or not critic.get("ok"):
        return []
    lines = ["### Independent reviewer (Hive)", ""]
    for h in critic.get("headlines") or []:
        lines.append(f"- **{h}**")
    if critic.get("contradictions"):
        lines.append("")
        lines.append("**Contradictions / challenges**")
        lines.append("")
        for c in critic["contradictions"]:
            lines.append(f"- {c}")
    if critic.get("assumptions"):
        lines.append("")
        lines.append("**Assumptions (not hard facts)**")
        lines.append("")
        for c in critic["assumptions"]:
            lines.append(f"- {c}")
    if critic.get("facts_ok"):
        lines.append("")
        lines.append("**Facts that held up**")
        lines.append("")
        for c in critic["facts_ok"][:10]:
            lines.append(f"- {c}")
    if critic.get("missing_checklist_ids"):
        lines.append("")
        lines.append(
            "**Still missing checklist ids:** "
            + ", ".join(str(x) for x in critic["missing_checklist_ids"])
        )
    if critic.get("gap_fill_sections"):
        lines.append("")
        lines.append(
            "**Gap-fill sections requested:** "
            + ", ".join(str(x) for x in critic["gap_fill_sections"])
        )
    lines.append("")
    return lines
