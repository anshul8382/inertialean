"""Hive relevance pass over earnings-call units (prepared remarks / Q&A / chunks)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from iis.config import settings
from iis.finance.inertia_methodology import gaps_prompt_block, methodology_prompt_block
from iis.models.enums import PdfDocType
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

CONCALL_MODES = ("prepared_remarks", "qa", "outlook_guidance", "other_keep")

_CONCALL_RUBRIC_EXTRA = """
CONCALL-SPECIFIC:
KEEP: guidance (any type), margins, volumes, mix, capacity/capex, competition,
working capital, order book, management credibility signals, risk call-outs,
walk-backs or raises vs prior guidance.
SKIP: moderator script, participant roll-call, pure safe-harbour, dial-in numbers,
repeated greetings, ads for next call.
SKIM: mixed blocks with some signal under fluff.
""".strip()


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


def _norm(row: dict[str, Any], unit_id: str, default_mode: str) -> dict[str, Any]:
    decision = str(row.get("decision") or "skim").strip().lower()
    if decision not in ("keep", "skim", "skip"):
        decision = "skim"
    mode = str(row.get("mode") or default_mode).strip().lower()
    if mode not in CONCALL_MODES:
        mode = default_mode
    try:
        conf = float(row.get("confidence") if row.get("confidence") is not None else 0.5)
    except (TypeError, ValueError):
        conf = 0.5
    return {
        "unit_id": unit_id,
        "decision": decision,
        "why": str(row.get("why") or "")[:500],
        "mode": mode,
        "checklist_ids": [str(x) for x in (row.get("checklist_ids") or [])][:12],
        "confidence": max(0.0, min(1.0, conf)),
    }


def build_concall_units(session: ResearchSession) -> list[dict[str, Any]]:
    """Build text units from transcript PDFs + Q&A pairs (accuracy-first, large caps)."""
    max_chars = max(8000, int(getattr(settings, "concall_unit_max_chars", 36000) or 36000))
    units: list[dict[str, Any]] = []

    files: set[str] = set()
    for d in session.pdf_documents or []:
        dt = str(getattr(d, "doc_type", "") or "").lower()
        if not d.file:
            continue
        if dt in (PdfDocType.TRANSCRIPT.value, "transcript") or "transcript" in dt:
            files.add(d.file)
        elif getattr(d, "qa_pairs", None):
            files.add(d.file)
    for c in session.pdf_chunks or []:
        dt = str(getattr(c, "doc_type", "") or "").lower()
        if c.file and (dt == PdfDocType.TRANSCRIPT.value or "transcript" in dt):
            files.add(c.file)

    for fname in sorted(files):
        chunks = [c for c in (session.pdf_chunks or []) if c.file == fname]
        qa_pairs = []
        for d in session.pdf_documents or []:
            if d.file == fname and getattr(d, "qa_pairs", None):
                qa_pairs = list(d.qa_pairs or [])
                break

        # Unit 1: Q&A from structured pairs
        if qa_pairs:
            parts = []
            pages = []
            used = 0
            for qa in qa_pairs:
                block = f"Q: {qa.question}\nA: {qa.answer}"
                if used + len(block) > max_chars:
                    break
                parts.append(block)
                used += len(block) + 2
                if getattr(qa, "page", None) is not None:
                    pages.append(int(qa.page))
            if parts:
                units.append(
                    {
                        "unit_id": f"{fname}::qa",
                        "file": fname,
                        "label": "Q&A",
                        "mode_hint": "qa",
                        "text": "\n\n".join(parts),
                        "pages": sorted(set(pages)),
                    }
                )

        # Unit 2: prepared / non-QA chunks (or all chunks if no QA)
        prepared_bits = []
        pages_p = []
        used = 0
        for c in chunks:
            hint = f"{getattr(c, 'section_hint', '') or ''} {getattr(c, 'section_title', '') or ''}".lower()
            if qa_pairs and ("q&a" in hint or "qa" == hint.strip()):
                continue
            text = (c.text or "").strip()
            if len(text) < 40:
                continue
            piece = f"[p.{c.page}] {text}"
            if used + len(piece) > max_chars:
                room = max_chars - used
                if room > 300:
                    prepared_bits.append(piece[:room])
                break
            prepared_bits.append(piece)
            used += len(piece) + 2
            if c.page is not None:
                pages_p.append(int(c.page))
        if prepared_bits:
            # If we already have QA unit, call this prepared; else whole-call
            mode_hint = "prepared_remarks" if qa_pairs else "other_keep"
            # Outlook-heavy filename/chunks → outlook mode hint if keywords dense
            blob = " ".join(prepared_bits)[:2000].lower()
            if sum(1 for k in ("outlook", "guidance", "expect", "for the year") if k in blob) >= 2:
                if mode_hint == "prepared_remarks":
                    pass  # keep prepared; outlook extracted in deep-read
            units.append(
                {
                    "unit_id": f"{fname}::prepared",
                    "file": fname,
                    "label": "Prepared remarks" if qa_pairs else "Call transcript",
                    "mode_hint": mode_hint,
                    "text": "\n\n".join(prepared_bits),
                    "pages": sorted(set(pages_p)),
                }
            )

    return units


def score_unit_relevance(
    session: ResearchSession,
    *,
    company: str,
    unit: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    from iis.context.manager import llm_agent
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    llm = get_llm_client(finance=True)
    system = (
        methodology_prompt_block()
        + "\n\n"
        + _CONCALL_RUBRIC_EXTRA
        + "\n\nRespond with JSON only:\n"
        '{"decision":"keep|skim|skip","why":"...","mode":"prepared_remarks|qa|outlook_guidance|other_keep",'
        '"checklist_ids":[],"confidence":0.0}'
    )
    user = (
        f"Company: {company}\n"
        f"File: {unit.get('file')}\n"
        f"Unit: {unit.get('label')} ({unit.get('unit_id')})\n"
        f"Mode hint: {unit.get('mode_hint')}\n\n"
        f"{gaps_prompt_block(session)}\n\n"
        "=== CONCALL TEXT ===\n"
        f"{unit.get('text') or ''}\n"
    )
    with llm_agent("concall_relevance"):
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
                timeout=timeout,
                max_tokens=int(getattr(settings, "concall_relevance_max_tokens", 600) or 600),
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
    parsed = _norm(
        _parse_json(raw or ""),
        str(unit.get("unit_id")),
        str(unit.get("mode_hint") or "other_keep"),
    )
    parsed["file"] = unit.get("file")
    parsed["label"] = unit.get("label")
    parsed["pages"] = unit.get("pages") or []
    parsed["chars_scored"] = len(unit.get("text") or "")
    parsed["text"] = unit.get("text") or ""
    return parsed


def run_concall_relevance_pass(
    session: ResearchSession,
    *,
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    if not bool(getattr(settings, "concall_relevance_llm_enabled", True)):
        return {"ok": False, "skipped": True, "reason": "concall_relevance_llm_enabled=false"}

    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    if not get_llm_client(finance=True).is_available():
        return {"ok": False, "error": "llm_unavailable"}

    units = build_concall_units(session)
    if not units:
        return {"ok": False, "error": "no_concall_units", "units": []}

    company = session.company.name if session.company else (session.company_key or "Company")
    timeout = float(getattr(settings, "concall_relevance_timeout_sec", 90.0) or 90.0)
    results: list[dict[str, Any]] = []
    total = len(units)
    for i, unit in enumerate(units):
        _notify(
            progress_cb,
            f"Concall relevance {i + 1}/{total}: {str(unit.get('label'))[:24]} · "
            f"{str(unit.get('file') or '')[:36]}",
        )
        try:
            results.append(
                score_unit_relevance(session, company=company, unit=unit, timeout=timeout)
            )
        except LLMAbortError:
            raise
        except Exception as exc:
            logger.info("Concall relevance failed %s: %s", unit.get("unit_id"), exc)
            results.append(
                {
                    "unit_id": unit.get("unit_id"),
                    "file": unit.get("file"),
                    "label": unit.get("label"),
                    "decision": "skim",
                    "why": f"relevance_error: {exc}"[:240],
                    "mode": unit.get("mode_hint") or "other_keep",
                    "checklist_ids": [],
                    "confidence": 0.2,
                    "pages": unit.get("pages") or [],
                    "text": unit.get("text") or "",
                    "chars_scored": len(unit.get("text") or ""),
                    "error": str(exc)[:200],
                }
            )

    counts = {"keep": 0, "skim": 0, "skip": 0}
    for r in results:
        counts[r.get("decision") or "skim"] = counts.get(r.get("decision") or "skim", 0) + 1
    return {"ok": True, "units": results, "counts": counts}


def units_to_deep_read(relevance: dict[str, Any], *, include_all_skims: bool = True) -> list[dict[str, Any]]:
    out = []
    for row in relevance.get("units") or []:
        if not isinstance(row, dict):
            continue
        d = row.get("decision")
        if d == "keep" or (d == "skim" and include_all_skims):
            out.append(row)
    return out
