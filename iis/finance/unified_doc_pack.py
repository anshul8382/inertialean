"""Pack AR / concall / broker PDF text into one budgeted dossier for a single Hive call.

Speed path: no per-section relevance/deep-read. Python already extracted page text
into ``session.pdf_chunks``; we concatenate with clear file/section headers.
"""
from __future__ import annotations

from typing import Any

from iis.config import settings
from iis.models.enums import PdfDocType
from iis.models.session import ResearchSession


def _clip(text: str, n: int) -> str:
    t = " ".join((text or "").split())
    if len(t) <= n:
        return t
    return t[: max(0, n - 1)].rstrip() + "…"


def _doc_type(c: Any) -> str:
    return str(getattr(c, "doc_type", "") or "").lower()


def _group_chunks(session: ResearchSession, want: set[str]) -> dict[str, list[Any]]:
    by_file: dict[str, list[Any]] = {}
    for c in session.pdf_chunks or []:
        dt = _doc_type(c)
        fname = getattr(c, "file", None) or ""
        if not fname:
            continue
        ok = False
        if "annual" in want and (
            dt == PdfDocType.ANNUAL_REPORT.value or "annual" in dt
        ):
            ok = True
        if "transcript" in want and (
            dt == PdfDocType.TRANSCRIPT.value or "transcript" in dt or "concall" in dt
        ):
            ok = True
        if "broker" in want and (
            dt == PdfDocType.BROKERAGE_REPORT.value or "broker" in dt
        ):
            ok = True
        if ok:
            by_file.setdefault(fname, []).append(c)
    for fname in by_file:
        by_file[fname].sort(
            key=lambda c: (int(getattr(c, "page", 0) or 0), int(getattr(c, "chunk_index", 0) or 0))
        )
    return by_file


def _file_block(fname: str, chunks: list[Any], *, max_chars: int, label: str) -> str:
    parts: list[str] = [f"### {label}: `{fname}`"]
    used = len(parts[0])
    last_sec = None
    for c in chunks:
        text = (getattr(c, "text", None) or "").strip()
        if len(text) < 40:
            continue
        sec = getattr(c, "section_title", None) or getattr(c, "section_hint", None)
        page = getattr(c, "page", None)
        head = ""
        if sec and sec != last_sec:
            head = f"\n#### {sec}\n"
            last_sec = sec
        piece = f"{head}[p.{page}] {text}" if page is not None else f"{head}{text}"
        if used + len(piece) > max_chars:
            room = max_chars - used
            if room > 400:
                parts.append(piece[:room])
            break
        parts.append(piece)
        used += len(piece) + 1
    return "\n".join(parts) if len(parts) > 1 else ""


def build_unified_doc_pack(
    session: ResearchSession,
    *,
    max_total_chars: int | None = None,
) -> dict[str, Any]:
    """Return ``{text, stats}`` — one markdown pack for a single LLM synthesis call."""
    budget = max(
        20000,
        int(
            max_total_chars
            if max_total_chars is not None
            else getattr(settings, "unified_pack_max_chars", 180000) or 180000
        ),
    )
    # Allocate budget: AR heavy, then concall, then broker
    ar_budget = int(budget * 0.55)
    cc_budget = int(budget * 0.30)
    br_budget = max(8000, budget - ar_budget - cc_budget)

    blocks: list[str] = []
    stats = {"ar_files": 0, "concall_files": 0, "broker_files": 0, "chars": 0}

    company = session.company.name if session.company else (session.company_key or "Company")
    blocks.append(f"# Document pack — {company}\n")
    blocks.append(
        "_Extracted PDF text (Python). CIQ financials are authoritative for ₹ totals; "
        "broker text is STREET opinion only._\n"
    )

    ar = _group_chunks(session, {"annual"})
    # Prefer newest / longest files first
    ar_files = sorted(
        ar.items(),
        key=lambda kv: -sum(len(getattr(c, "text", "") or "") for c in kv[1]),
    )
    max_ar = max(1, int(getattr(settings, "unified_pack_max_ar_files", 2) or 2))
    per_ar = max(8000, ar_budget // max(1, min(max_ar, len(ar_files) or 1)))
    for fname, chunks in ar_files[:max_ar]:
        block = _file_block(fname, chunks, max_chars=per_ar, label="ANNUAL REPORT")
        if block:
            blocks.append(block)
            stats["ar_files"] += 1

    cc = _group_chunks(session, {"transcript"})
    max_cc = max(1, int(getattr(settings, "unified_pack_max_concall_files", 4) or 4))
    cc_files = sorted(
        cc.items(),
        key=lambda kv: -sum(len(getattr(c, "text", "") or "") for c in kv[1]),
    )
    per_cc = max(4000, cc_budget // max(1, min(max_cc, len(cc_files) or 1)))
    for fname, chunks in cc_files[:max_cc]:
        # Prefer Q&A pairs if present
        qa_bits = []
        for d in session.pdf_documents or []:
            if d.file == fname and getattr(d, "qa_pairs", None):
                for qa in (d.qa_pairs or [])[:40]:
                    qa_bits.append(f"Q: {qa.question}\nA: {qa.answer}")
                break
        if qa_bits:
            blob = f"### CONCALL Q&A: `{fname}`\n" + "\n\n".join(qa_bits)
            blocks.append(_clip(blob, per_cc))
        else:
            block = _file_block(fname, chunks, max_chars=per_cc, label="CONCALL")
            if block:
                blocks.append(block)
        stats["concall_files"] += 1

    br = _group_chunks(session, {"broker"})
    max_br = max(1, int(getattr(settings, "unified_pack_max_broker_files", 3) or 3))
    br_files = sorted(
        br.items(),
        key=lambda kv: -sum(len(getattr(c, "text", "") or "") for c in kv[1]),
    )[:max_br]
    per_br = max(3000, br_budget // max(1, len(br_files) or 1))
    for fname, chunks in br_files:
        block = _file_block(fname, chunks, max_chars=per_br, label="BROKER (STREET)")
        if block:
            blocks.append(block)
            stats["broker_files"] += 1

    text = "\n\n".join(blocks)
    if len(text) > budget:
        text = text[:budget] + "\n\n_[pack truncated to budget]_"
    stats["chars"] = len(text)
    return {"text": text, "stats": stats}
