"""Incremental merge for company library data."""
from __future__ import annotations

import re
from pathlib import Path

from iis.models.artifacts import (
    ConsensusMetric,
    FinancialLineItem,
    GuidanceRecord,
    PdfChunk,
    PdfDocument,
)


def _basename(file_ref: str) -> str:
    return Path(file_ref).name


def period_grain(item: FinancialLineItem) -> str:
    """Distinguish annual vs quarterly CIQ columns that share the same period_end.

    Capital IQ annual: ``12 months Mar-31-2026``
    Capital IQ quarterly: ``3 months FQ4/CQ1 Mar-31-2026`` / ``FQ1/CQ2 Jun-30-2025``
    Without this grain, uploading annual + quarterly Financials workbooks would
    overwrite FY figures with the matching quarter (and vice versa).
    """
    pl = (item.period_label or "").lower()
    if "ltm" in pl:
        return "LTM"
    if "3 months" in pl or re.search(r"\bfq\d\b", pl) or "/cq" in pl:
        return "Q"
    if "12 months" in pl or re.search(r"\bfy\s*\d{2,4}\b", pl):
        return "A"
    # Balance-sheet quarterly headers: "Press Release FQ1/CQ2 Jun-30-2025"
    if re.search(r"fq\d", pl):
        return "Q"
    return "A"


def line_item_key(item: FinancialLineItem) -> tuple:
    return (
        item.statement,
        item.line_item,
        item.period_end or "",
        item.period_type.value,
        period_grain(item),
    )


def consensus_key(item: ConsensusMetric) -> tuple:
    return (item.metric, item.period)


def guidance_key(item: GuidanceRecord) -> tuple:
    return (item.metric, item.fiscal_year)


def merge_financial_line_items(
    existing: list[FinancialLineItem],
    new_items: list[FinancialLineItem],
    source_filename: str,
) -> list[FinancialLineItem]:
    """Replace prior rows from this file; upsert by period+line item (keeps longer history)."""
    src = _basename(source_filename)
    kept = [i for i in existing if _basename(i.source.file) != src]
    by_key = {line_item_key(i): i for i in kept}
    for item in new_items:
        item.source.file = src
        by_key[line_item_key(item)] = item
    return sorted(by_key.values(), key=lambda x: (x.period_end or "", x.line_item))


def merge_consensus_metrics(
    existing: list[ConsensusMetric],
    new_items: list[ConsensusMetric],
    source_filename: str,
) -> list[ConsensusMetric]:
    src = _basename(source_filename)
    kept = [i for i in existing if _basename(i.source.file) != src]
    by_key = {consensus_key(i): i for i in kept}
    for item in new_items:
        item.source.file = src
        by_key[consensus_key(item)] = item
    return list(by_key.values())


def merge_guidance_records(
    existing: list[GuidanceRecord],
    new_items: list[GuidanceRecord],
    source_filename: str,
) -> list[GuidanceRecord]:
    src = _basename(source_filename)
    kept = [i for i in existing if _basename(i.source.file) != src]
    by_key = {guidance_key(i): i for i in kept}
    for item in new_items:
        item.source.file = src
        by_key[guidance_key(item)] = item
    return list(by_key.values())


def merge_pdf(
    existing_docs: list[PdfDocument],
    existing_chunks: list[PdfChunk],
    new_doc: PdfDocument,
    new_chunks: list[PdfChunk],
    source_filename: str,
) -> tuple[list[PdfDocument], list[PdfChunk]]:
    """Replace PDF data for same filename; keep other files."""
    src = _basename(source_filename)
    docs = [d for d in existing_docs if d.file != src]
    chunks = [c for c in existing_chunks if c.file != src]
    new_doc.file = src
    for c in new_chunks:
        c.file = src
    docs.append(new_doc)
    chunks.extend(new_chunks)
    return docs, chunks


def sync_session_from_library(session, library) -> None:
    """Copy canonical library data into a research session."""
    session.company_key = library.company_key
    if library.company:
        session.company = library.company
    session.financial_line_items = list(library.financial_line_items)
    session.consensus_metrics = list(library.consensus_metrics)
    session.guidance_records = list(library.guidance_records)
    session.pdf_documents = list(library.pdf_documents)
    session.pdf_chunks = list(library.pdf_chunks)
    # Surface archived source files so UI / reingest don't require re-upload
    archived = [d.stored_path for d in library.documents if d.stored_path and Path(d.stored_path).exists()]
    if archived:
        existing = {Path(p).resolve().as_posix() for p in session.uploaded_files}
        for p in archived:
            resolved = Path(p).resolve().as_posix()
            if resolved not in existing:
                session.uploaded_files.append(p)
                existing.add(resolved)
