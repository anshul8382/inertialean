"""Shared retrieval helpers for research packs."""
from __future__ import annotations

import re
from typing import Any, Iterable

from iis.ingest.pdf import search_pdf_chunks, search_transcript_qa
from iis.models.artifacts import PdfChunk
from iis.models.enums import PdfDocType
from iis.models.session import ResearchSession
from iis.finance.packs.models import new_claim

_PCT = re.compile(
    r"(?i)(\d{1,3}(?:\.\d+)?)\s*%|"
    r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(cr|crore|mn|million|bn|billion)?"
)
_EXPLICIT = re.compile(
    r"(?i)\b(we expect|we guide|guidance of|target of|will be|expect(?:ing)?|"
    r"outlook of|around\s+\d|approximately\s+\d|between\s+\d)\b"
)
_ASPIRATIONAL = re.compile(
    r"(?i)\b(want to become|aspire|ambition|aim to be|vision to|market leader|"
    r"world[- ]class|best[- ]in[- ]class)\b"
)
_QUAL = re.compile(
    r"(?i)\b(optimistic|confident|cautious|challenging|remain positive|"
    r"comfortable|hope|believe demand)\b"
)


def clip(text: str, n: int = 520) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) <= n:
        return t
    return t[: n - 1].rstrip() + "…"


def classify_forward_claim(text: str) -> tuple[str, str]:
    """Return (claim_type, reliability) for management/broker forward language."""
    t = text or ""
    if _ASPIRATIONAL.search(t) and not _EXPLICIT.search(t):
        return "aspirational", "low"
    if _EXPLICIT.search(t) and _PCT.search(t):
        return "explicit", "high"
    if _EXPLICIT.search(t):
        return "explicit", "medium"
    if _QUAL.search(t):
        return "qualitative", "medium"
    if _PCT.search(t):
        return "excerpt", "medium"
    return "excerpt", "low"


def extract_number_hint(text: str) -> tuple[Any, str | None]:
    m = _PCT.search(text or "")
    if not m:
        return None, None
    if m.group(1):
        try:
            return float(m.group(1)), "pct"
        except ValueError:
            return m.group(1), "pct"
    raw = (m.group(2) or "").replace(",", "")
    unit = (m.group(3) or "").lower() or "inr"
    try:
        return float(raw), unit
    except ValueError:
        return raw, unit


def hit_claims(
    session: ResearchSession,
    *,
    section: str,
    parameter: str,
    queries: Iterable[str],
    doc_types: list[str] | None,
    limit_per_query: int = 3,
    max_claims: int = 6,
    claim_type_default: str = "excerpt",
    classify_forward: bool = False,
) -> list[dict[str, Any]]:
    """Keyword-retrieve PDF chunks → cited claims."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for q in queries:
        hits = search_pdf_chunks(
            session.pdf_chunks or [],
            q,
            limit=limit_per_query,
            doc_types=doc_types,
        )
        for c in hits:
            key = f"{c.file}:{c.page}:{(c.text or '')[:80]}"
            if key in seen:
                continue
            seen.add(key)
            quote = clip(c.text or "", 560)
            if len(quote) < 40:
                continue
            ctype = claim_type_default
            rel = "medium"
            val, unit = extract_number_hint(quote)
            if classify_forward:
                ctype, rel = classify_forward_claim(quote)
            out.append(
                new_claim(
                    section=section,
                    parameter=parameter,
                    quote=quote,
                    source_file=getattr(c, "file", None),
                    page=getattr(c, "page", None),
                    claim_type=ctype,
                    value=val,
                    unit=unit,
                    reliability=rel,
                    extra={"query": q, "section_hint": getattr(c, "section_hint", None)},
                )
            )
            if len(out) >= max_claims:
                return out
    return out


def qa_claims(
    session: ResearchSession,
    *,
    section: str,
    parameter: str,
    queries: Iterable[str],
    limit_per_query: int = 3,
    max_claims: int = 8,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for q in queries:
        hits = search_transcript_qa(session.pdf_documents or [], q, limit=limit_per_query)
        for qa in hits:
            key = (qa.question or "")[:60]
            if key in seen:
                continue
            seen.add(key)
            quote = clip(f"Q: {qa.question}\nA: {qa.answer}", 700)
            ctype, rel = classify_forward_claim(qa.answer or "")
            val, unit = extract_number_hint(qa.answer or "")
            out.append(
                new_claim(
                    section=section,
                    parameter=parameter,
                    quote=quote,
                    source_file=getattr(qa, "file", None),
                    page=getattr(qa, "page", None),
                    claim_type=ctype,
                    value=val,
                    unit=unit,
                    reliability=rel,
                    speaker=getattr(qa, "speaker", None),
                    extra={"question": (qa.question or "")[:300]},
                )
            )
            if len(out) >= max_claims:
                return out
    return out


def chunks_by_type(session: ResearchSession, doc_type: str) -> list[PdfChunk]:
    return [c for c in (session.pdf_chunks or []) if getattr(c, "doc_type", None) == doc_type]


DOC_AR = [PdfDocType.ANNUAL_REPORT.value]
DOC_CC = [PdfDocType.TRANSCRIPT.value]
DOC_BR = [PdfDocType.BROKERAGE_REPORT.value]
