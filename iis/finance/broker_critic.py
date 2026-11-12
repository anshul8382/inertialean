"""Independent Hive critic for brokerage deep-reads — enforce STREET ≠ filings."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from iis.config import settings
from iis.finance.inertia_methodology import methodology_prompt_block
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


def run_broker_critic(
    session: ResearchSession,
    *,
    company: str,
    unit_id: str,
    label: str,
    source_excerpt: str,
    draft_bullets: str,
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    if not bool(getattr(settings, "broker_critic_llm_enabled", True)):
        return {"ok": False, "skipped": True}

    from iis.context.manager import llm_agent
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    llm = get_llm_client(finance=True)
    if not llm.is_available():
        return {"ok": False, "error": "llm_unavailable"}

    max_src = int(getattr(settings, "broker_critic_source_chars", 28000) or 28000)
    _notify(progress_cb, f"Broker critic (STREET): {label[:40]}")

    system = (
        methodology_prompt_block()
        + "\n\nYou are an independent critic of a SELL-SIDE / BROKER deep-read.\n"
        "Rules:\n"
        "- STREET ≠ company filings ≠ CIQ actuals.\n"
        "- Flag any bullet that presents broker opinion as company fact.\n"
        "- Flag invented numbers, missing [STREET] tags, and unsupported TP/rating claims.\n"
        "- CIQ actuals/consensus (if mentioned in draft) must not be conflated with broker model.\n"
        "Respond JSON only:\n"
        '{"verdict":"pass|revise|reject","issues":[{"severity":"high|medium|low",'
        '"issue":"...","fix":"..."}],'
        '"missing_from_source":["..."],'
        '"street_hygiene":"ok|needs_retag|conflates_filings",'
        '"revised_bullets":"markdown or empty if pass"}'
    )
    user = (
        f"Company: {company}\nUnit: {label} ({unit_id}) [STREET]\n\n"
        f"=== SOURCE EXCERPT (broker PDF) ===\n{(source_excerpt or '')[:max_src]}\n\n"
        f"=== DRAFT BULLETS ===\n{(draft_bullets or '')[:12000]}\n"
    )
    timeout = float(getattr(settings, "broker_critic_timeout_sec", 120.0) or 120.0)
    with llm_agent("broker_critic"):
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
                timeout=timeout,
                max_tokens=int(getattr(settings, "broker_critic_max_tokens", 2500) or 2500),
                allow_reasoning_fallback=False,
                format_json=True,
            )
        except TypeError:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
                timeout=timeout,
                format_json=True,
            )
        except LLMAbortError:
            raise
        except Exception as exc:
            logger.info("Broker critic failed %s: %s", unit_id, exc)
            return {"ok": False, "error": str(exc)[:240]}

    data = _parse_json(raw or "")
    verdict = str(data.get("verdict") or "revise").lower()
    if verdict not in ("pass", "revise", "reject"):
        verdict = "revise"
    hygiene = str(data.get("street_hygiene") or "ok").lower()
    if hygiene not in ("ok", "needs_retag", "conflates_filings"):
        hygiene = "ok"
    issues = [x for x in (data.get("issues") or []) if isinstance(x, dict)][:12]
    missing = [str(x) for x in (data.get("missing_from_source") or [])][:12]
    revised = str(data.get("revised_bullets") or "").strip()
    return {
        "ok": True,
        "unit_id": unit_id,
        "verdict": verdict,
        "issues": issues,
        "missing_from_source": missing,
        "street_hygiene": hygiene,
        "revised_bullets": revised,
        "street": True,
    }
