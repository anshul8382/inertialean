"""One Hive call over a packed AR/concall/broker text dossier.

Replaces the multi-pass relevance→deep-read→critic loops for Analyze speed
(target: whole Analyze ≤ 10–15 min).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from iis.config import settings
from iis.finance.unified_doc_pack import build_unified_doc_pack
from iis.models.artifacts import AgentArtifact
from iis.models.enums import EventType, SourceTier
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

_SYSTEM = """You are an equity research analyst for Indian listed companies.
You receive EXTRACTED PDF text (annual reports, earnings concalls, broker notes).

Rules:
- Capital IQ / Python numbers are authoritative for ₹ totals — do NOT invent financials.
- Broker notes are STREET opinion only; never treat broker ₹ as filings.
- Quote short evidence; cite file names when possible.
- Be concise. Prefer bullets over prose.
- Separate facts (from filings/transcripts) from management outlook and street views.
"""


def _notify(cb: ProgressCb | None, msg: str) -> None:
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def _parse_sections(raw: str) -> dict[str, str]:
    """Split markdown response into named blocks."""
    text = (raw or "").strip()
    if not text:
        return {}
    parts = re.split(r"\n(?=##\s+)", text)
    out: dict[str, str] = {}
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"##\s+(.+?)\n([\s\S]*)", part)
        if m:
            key = re.sub(r"[^a-z0-9]+", "_", m.group(1).strip().lower()).strip("_")
            out[key] = m.group(2).strip()
        else:
            out.setdefault("body", "")
            out["body"] = ((out.get("body") or "") + "\n" + part).strip()
    if not out:
        out["body"] = text
    return out


def run_one_shot_doc_synthesis(
    session: ResearchSession,
    *,
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    """Build pack → one Hive call → store ``unified_doc_synthesis`` artifact."""
    if not bool(getattr(settings, "unified_doc_llm_enabled", True)):
        return {"ok": False, "skipped": True, "reason": "unified_doc_llm_enabled=false"}

    from iis.context.manager import llm_agent
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    llm = get_llm_client(finance=True)
    if not llm.is_available():
        return {"ok": False, "skipped": True, "reason": "llm_unavailable"}

    _notify(progress_cb, "Packing AR / concall / broker text for one Hive pass…")
    pack = build_unified_doc_pack(session)
    pack_text = (pack.get("text") or "").strip()
    stats = dict(pack.get("stats") or {})
    if len(pack_text) < 800:
        return {
            "ok": False,
            "skipped": True,
            "reason": "pack_too_small",
            "stats": stats,
        }

    company = session.company.name if session.company else (session.company_key or "Company")
    focus = ""
    try:
        from iis.finance.research_planner import research_plan_focus_block

        focus = research_plan_focus_block(session) or ""
    except Exception:
        focus = ""
    focus_block = f"\n{focus}\n\n" if focus else "\n"
    prompt = (
        f"Company: {company}\n"
        f"{focus_block}"
        "Using ONLY the document pack below, write a structured research digest with these "
        "exact markdown headings (##):\n"
        "## Business & strategy (from AR)\n"
        "## Risks & governance (from AR)\n"
        "## Management outlook (from concalls)\n"
        "## Guidance & Q&A highlights (from concalls)\n"
        "## Street view (from brokers — opinion only)\n"
        "## Open questions for further work\n\n"
        "Prioritize the focus bullets above when the pack has evidence. "
        "Keep each section to tight bullets. No invented ₹ figures.\n\n"
        "=== DOCUMENT PACK ===\n"
        f"{pack_text}\n"
    )

    timeout = float(getattr(settings, "unified_doc_timeout_sec", 180.0) or 180.0)
    timeout = min(180.0, max(60.0, timeout))
    max_tokens = int(getattr(settings, "unified_doc_max_tokens", 4500) or 4500)

    _notify(
        progress_cb,
        f"Hive one-shot digest · ~{stats.get('chars', 0):,} chars · "
        f"{stats.get('ar_files', 0)} AR / {stats.get('concall_files', 0)} concall / "
        f"{stats.get('broker_files', 0)} broker…",
    )

    raw = ""
    with llm_agent("unified_doc_synthesis"):
        try:
            try:
                raw = llm.chat(
                    [
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.15,
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
                    temperature=0.15,
                    timeout=timeout,
                )
        except LLMAbortError:
            raise
        except Exception as exc:
            logger.info("unified_doc_synthesis failed: %s", exc)
            return {
                "ok": False,
                "error": str(exc)[:300],
                "stats": stats,
            }

    sections = _parse_sections(raw or "")
    md = (raw or "").strip()
    result: dict[str, Any] = {
        "ok": bool(md) and len(md) > 200,
        "markdown": md,
        "sections": sections,
        "stats": stats,
        "path": "unified_one_shot",
    }

    session.agent_artifacts = [
        a for a in session.agent_artifacts if a.artifact_type != "unified_doc_synthesis"
    ]
    session.agent_artifacts.append(
        AgentArtifact(
            agent_id="unified_doc_synthesis",
            artifact_type="unified_doc_synthesis",
            payload=result,
            confidence=0.75 if result.get("ok") else 0.2,
            provenance=[
                f"ar:{stats.get('ar_files', 0)}",
                f"concall:{stats.get('concall_files', 0)}",
                f"broker:{stats.get('broker_files', 0)}",
            ],
            source_tier=SourceTier.EXTRACTED,
        )
    )
    try:
        session.add_event(
            EventType.INFERENCE,
            agent="unified_doc_synthesis",
            details={
                "ok": result.get("ok"),
                "chars_in": stats.get("chars"),
                "chars_out": len(md),
                "path": "unified_one_shot",
            },
        )
    except Exception:
        pass

    _notify(
        progress_cb,
        f"One-shot digest done · {len(md):,} chars out"
        if result.get("ok")
        else "One-shot digest empty / failed — Python digests kept",
    )
    return result


def unified_doc_synthesis_markdown(session: ResearchSession) -> list[str]:
    """Lines for the research report from the one-shot artifact."""
    art = None
    for a in session.agent_artifacts or []:
        if a.artifact_type == "unified_doc_synthesis":
            art = a
            break
    if not art or not isinstance(art.payload, dict):
        return []
    md = (art.payload.get("markdown") or "").strip()
    if len(md) < 80:
        return []
    lines = [
        "_Single Hive pass over packed AR / concall / broker text "
        "(multi-pass section loops disabled for speed)._",
        "",
    ]
    lines.extend(md.splitlines())
    lines.append("")
    return lines
