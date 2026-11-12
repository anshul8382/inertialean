"""Hive relevance pass over brokerage PDF units — STREET only."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from iis.config import settings
from iis.finance.inertia_methodology import gaps_prompt_block, methodology_prompt_block
from iis.models.enums import PdfDocType
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

BROKER_MODES = ("investment_thesis", "financial_estimates", "valuation", "other_keep")

_BROKER_RUBRIC = """
BROKERAGE / STREET-SPECIFIC:
These documents are STREET opinions — never treat as company filings or CIQ actuals.
KEEP: rating/TP, thesis, estimate drivers, valuation method, catalysts, risks,
estimate revisions, peer comps with company-specific takeaways.
SKIP: rating-scale definitions, legal disclosures, analyst bios, blank pages,
generic macro primers with no company link, marketing tear-sheets without thesis.
SKIM: mixed pages (some thesis buried in fluff).
Always prefer mode that matches content; default other_keep if unclear.
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
    if mode not in BROKER_MODES:
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
        "street": True,
    }


def _broker_files(session: ResearchSession) -> list[str]:
    files: set[str] = set()
    for d in session.pdf_documents or []:
        dt = str(getattr(d, "doc_type", "") or "").lower()
        if d.file and (
            dt == PdfDocType.BROKERAGE_REPORT.value or "broker" in dt
        ):
            files.add(d.file)
    for c in session.pdf_chunks or []:
        dt = str(getattr(c, "doc_type", "") or "").lower()
        if c.file and (
            dt == PdfDocType.BROKERAGE_REPORT.value or "broker" in dt
        ):
            files.add(c.file)
    # Cap for accuracy-first but bounded wall time
    max_files = int(getattr(settings, "broker_hive_max_files", 6) or 6)
    return sorted(files)[: max(1, max_files)]


def build_broker_units(session: ResearchSession) -> list[dict[str, Any]]:
    """One narrative unit per broker file (front pages) + optional valuation slice."""
    max_chars = max(8000, int(getattr(settings, "broker_unit_max_chars", 36000) or 36000))
    max_page = int(getattr(settings, "broker_hive_max_page", 8) or 8)
    units: list[dict[str, Any]] = []

    for fname in _broker_files(session):
        chunks = [
            c
            for c in (session.pdf_chunks or [])
            if c.file == fname and (c.page is None or int(c.page) <= max_page)
        ]
        chunks.sort(key=lambda c: (int(c.page or 0), int(getattr(c, "chunk_index", 0) or 0)))
        parts: list[str] = []
        pages: list[int] = []
        used = 0
        for c in chunks:
            text = (c.text or "").strip()
            if len(text) < 40:
                continue
            piece = f"[p.{c.page}] {text}"
            if used + len(piece) > max_chars:
                room = max_chars - used
                if room > 300:
                    parts.append(piece[:room])
                break
            parts.append(piece)
            used += len(piece) + 2
            if c.page is not None:
                pages.append(int(c.page))
        if not parts:
            continue
        blob = "\n\n".join(parts)
        low = blob[:3000].lower()
        mode_hint = "investment_thesis"
        if sum(1 for k in ("target price", "we recommend", "initiate", "upgrade", "downgrade") if k in low) >= 1:
            mode_hint = "investment_thesis"
        elif sum(1 for k in ("valuation", "dcf", "ev/ebitda", "target multiple") if k in low) >= 2:
            mode_hint = "valuation"
        elif sum(1 for k in ("fy2", "estimate", "we model", "our forecast") if k in low) >= 2:
            mode_hint = "financial_estimates"
        units.append(
            {
                "unit_id": f"{fname}::front",
                "file": fname,
                "label": f"Broker note · {Path(fname).name}",
                "mode_hint": mode_hint,
                "text": blob,
                "pages": sorted(set(pages)),
                "street": True,
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
        + _BROKER_RUBRIC
        + "\n\nRespond with JSON only:\n"
        '{"decision":"keep|skim|skip","why":"...","mode":"investment_thesis|financial_estimates|valuation|other_keep",'
        '"checklist_ids":[],"confidence":0.0}'
    )
    user = (
        f"Company: {company}\n"
        f"File: {unit.get('file')}  [STREET / brokerage]\n"
        f"Unit: {unit.get('label')} ({unit.get('unit_id')})\n"
        f"Mode hint: {unit.get('mode_hint')}\n\n"
        f"{gaps_prompt_block(session)}\n\n"
        "=== BROKER TEXT (STREET) ===\n"
        f"{unit.get('text') or ''}\n"
    )
    with llm_agent("broker_relevance"):
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
                timeout=timeout,
                max_tokens=int(getattr(settings, "broker_relevance_max_tokens", 600) or 600),
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


def run_broker_relevance_pass(
    session: ResearchSession,
    *,
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    if not bool(getattr(settings, "broker_relevance_llm_enabled", True)):
        return {"ok": False, "skipped": True, "reason": "broker_relevance_llm_enabled=false"}

    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    if not get_llm_client(finance=True).is_available():
        return {"ok": False, "error": "llm_unavailable"}

    units = build_broker_units(session)
    if not units:
        return {"ok": False, "error": "no_broker_units", "units": []}

    company = session.company.name if session.company else (session.company_key or "Company")
    timeout = float(getattr(settings, "broker_relevance_timeout_sec", 90.0) or 90.0)
    results: list[dict[str, Any]] = []
    total = len(units)
    for i, unit in enumerate(units):
        _notify(
            progress_cb,
            f"Broker relevance {i + 1}/{total}: {Path(str(unit.get('file') or '')).name[:40]}",
        )
        try:
            results.append(
                score_unit_relevance(session, company=company, unit=unit, timeout=timeout)
            )
        except LLMAbortError:
            raise
        except Exception as exc:
            logger.info("Broker relevance failed %s: %s", unit.get("unit_id"), exc)
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
                    "street": True,
                    "error": str(exc)[:200],
                }
            )

    counts = {"keep": 0, "skim": 0, "skip": 0}
    for r in results:
        counts[r.get("decision") or "skim"] = counts.get(r.get("decision") or "skim", 0) + 1
    return {"ok": True, "units": results, "counts": counts, "street": True}


def units_to_deep_read(relevance: dict[str, Any], *, include_all_skims: bool = True) -> list[dict[str, Any]]:
    out = []
    for row in relevance.get("units") or []:
        if not isinstance(row, dict):
            continue
        d = row.get("decision")
        if d == "keep" or (d == "skim" and include_all_skims):
            out.append(row)
    return out
