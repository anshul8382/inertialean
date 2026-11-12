"""Incremental ingest into company library."""
from __future__ import annotations

from pathlib import Path

from iis.library.detect import (
    classify_excel_kind,
    classify_image_kind,
    classify_pdf_kind,
    classify_upload_kind,
    company_key_from_info,
    detect_fiscal_period,
    detect_fiscal_year,
)
from iis.library.merge import (
    merge_consensus_metrics,
    merge_financial_line_items,
    merge_guidance_records,
    merge_pdf,
    sync_session_from_library,
)
from iis.integrations.gdrive import sync_upload_for_company
from iis.library.models import CompanyLibrary
from iis.library.store import CompanyLibraryStore, file_content_hash
from iis.models.session import ResearchSession


def _company_display_name(session: ResearchSession, library: CompanyLibrary | None = None) -> str | None:
    if session.company and session.company.name:
        return session.company.name
    if library and library.company and library.company.name:
        return library.company.name
    return None


def _sync_drive_upload(
    session: ResearchSession,
    library: CompanyLibrary,
    stored_path: str,
    *,
    doc_kind: str | None = None,
) -> None:
    name = _company_display_name(session, library)
    if name:
        sync_upload_for_company(name, stored_path, doc_kind=doc_kind)


def ingest_file_to_library(
    session: ResearchSession,
    path: Path,
    store: CompanyLibraryStore | None = None,
    *,
    forced_kind: str | None = None,
) -> tuple[CompanyLibrary | None, str]:
    """
    Parse one file, merge into company library, sync session.
    Returns (library, status) where status is ingested | skipped_duplicate | no_company.
    """
    store = store or CompanyLibraryStore()
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))

    forced = forced_kind or (session.upload_file_kinds or {}).get(path.name)
    content_hash = file_content_hash(path)
    suffix = path.suffix.lower()

    if suffix in (".xls", ".xlsx"):
        return _ingest_excel_path(session, path, store, content_hash, forced_kind=forced)
    if suffix == ".pdf":
        return _ingest_pdf_path(session, path, store, content_hash, forced_kind=forced)
    if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return _ingest_image_path(session, path, store, content_hash, forced_kind=forced)
    return None, "unsupported"


def _resolve_key_after_company(session: ResearchSession, store: CompanyLibraryStore) -> str | None:
    key = store.resolve_key(session) or company_key_from_info(session.company)
    if key:
        session.company_key = key
    return key


def _is_different_company(subject, other) -> bool:
    """True when Excel identity is clearly another listed company (peer upload)."""
    if not subject or not other:
        return False
    if subject.bse_code and other.bse_code and str(subject.bse_code) != str(other.bse_code):
        return True
    if (
        subject.nse_ticker
        and other.nse_ticker
        and subject.nse_ticker.upper() != other.nse_ticker.upper()
    ):
        return True
    sn = (subject.name or "").strip().lower()
    on = (other.name or "").strip().lower()
    if sn and on and sn != on:
        # Require both have identity codes or clearly different first tokens
        if (subject.bse_code or subject.nse_ticker) and (other.bse_code or other.nse_ticker):
            return True
        s0 = sn.split()[0]
        o0 = on.split()[0]
        if len(s0) >= 4 and len(o0) >= 4 and s0 != o0:
            return True
    return False


def _ingest_excel_path(
    session: ResearchSession,
    path: Path,
    store: CompanyLibraryStore,
    content_hash: str,
    *,
    forced_kind: str | None = None,
) -> tuple[CompanyLibrary | None, str]:
    """Excel bootstraps company identity — no company_key required upfront.

    If the workbook is for a *different* company than the open session (peer upload),
    files are archived into that peer's library and the subject session is left intact.
    """
    subject_company = session.company
    subject_key = session.company_key

    scratch = CompanyLibrary(company_key="_parse")
    _parse_excel_into(session, scratch, path)

    file_company = scratch.company
    peer_mode = _is_different_company(subject_company, file_company)

    if peer_mode and file_company:
        # Route into peer library without hijacking the active research session
        peer_key = company_key_from_info(file_company)
        if not peer_key:
            session.company = subject_company
            session.company_key = subject_key
            return None, "no_company"

        library = store.load(peer_key)
        library.company = _merge_company(library.company, file_company)
        kind = forced_kind or classify_excel_kind(path.name)

        if store.find_duplicate(library, content_hash):
            stored = store.archive_file(peer_key, path, doc_kind=kind)
            store.register_document(
                library,
                filename=path.name,
                doc_kind=kind,
                content_hash=content_hash,
                skipped=True,
                stored_path=stored,
            )
            store.save(library)
            _sync_drive_upload(session, library, stored, doc_kind=kind)
            session.company = subject_company
            session.company_key = subject_key
            return library, "skipped_duplicate_peer"

        library.financial_line_items = merge_financial_line_items(
            library.financial_line_items, scratch.financial_line_items, path.name
        )
        library.consensus_metrics = merge_consensus_metrics(
            library.consensus_metrics, scratch.consensus_metrics, path.name
        )
        library.guidance_records = merge_guidance_records(
            library.guidance_records, scratch.guidance_records, path.name
        )
        line_count = (
            len(scratch.financial_line_items)
            + len(scratch.consensus_metrics)
            + len(scratch.guidance_records)
        )
        freq = None
        if kind == "capital_iq_financials":
            try:
                from iis.parsers.capital_iq.financials import detect_financials_frequency

                freq = detect_financials_frequency(path)
            except Exception:
                freq = None
        stored = store.archive_file(peer_key, path, doc_kind=kind)
        store.register_document(
            library,
            filename=path.name,
            doc_kind=kind,
            content_hash=content_hash,
            line_item_count=line_count,
            stored_path=stored,
            fiscal_period=(freq.title() if freq else None),
        )
        store.save(library)
        _sync_drive_upload(session, library, stored, doc_kind=kind)
        # Restore subject session identity/data
        session.company = subject_company
        session.company_key = subject_key
        return library, "ingested_peer"

    company_key = _resolve_key_after_company(session, store)
    if not company_key:
        return None, "no_company"

    library = store.load(company_key)
    library.company = _merge_company(library.company, scratch.company)

    kind = forced_kind or classify_excel_kind(path.name)
    if store.find_duplicate(library, content_hash):
        stored = store.archive_file(company_key, path, doc_kind=kind)
        store.register_document(
            library,
            filename=path.name,
            doc_kind=kind,
            content_hash=content_hash,
            skipped=True,
            stored_path=stored,
        )
        store.save(library)
        sync_session_from_library(session, library)
        session.company_key = company_key
        _sync_drive_upload(session, library, stored, doc_kind=kind)
        return library, "skipped_duplicate"

    library.financial_line_items = merge_financial_line_items(
        library.financial_line_items, scratch.financial_line_items, path.name
    )
    library.consensus_metrics = merge_consensus_metrics(
        library.consensus_metrics, scratch.consensus_metrics, path.name
    )
    library.guidance_records = merge_guidance_records(
        library.guidance_records, scratch.guidance_records, path.name
    )

    line_count = len(scratch.financial_line_items) + len(scratch.consensus_metrics) + len(scratch.guidance_records)
    freq = None
    if kind == "capital_iq_financials":
        try:
            from iis.parsers.capital_iq.financials import detect_financials_frequency

            freq = detect_financials_frequency(path)
        except Exception:
            freq = None
    stored = store.archive_file(company_key, path, doc_kind=kind)
    store.register_document(
        library,
        filename=path.name,
        doc_kind=kind,
        content_hash=content_hash,
        line_item_count=line_count,
        stored_path=stored,
        fiscal_period=(freq.title() if freq else None),
    )
    store.save(library)
    sync_session_from_library(session, library)
    session.company_key = company_key
    _sync_drive_upload(session, library, stored, doc_kind=kind)
    return library, "ingested"


def _ingest_pdf_path(
    session: ResearchSession,
    path: Path,
    store: CompanyLibraryStore,
    content_hash: str,
    *,
    forced_kind: str | None = None,
) -> tuple[CompanyLibrary | None, str]:
    """PDFs need company identity from CIQ or company name hint."""
    company_key = _resolve_key_after_company(session, store)
    if not company_key:
        return None, "no_company"

    library = store.load(company_key)
    if session.company:
        library.company = _merge_company(library.company, session.company)

    from iis.ingest.pdf import ingest_pdf

    # Peek PDF type for classification; still archive duplicates without full re-parse cost when known
    dup = store.find_duplicate(library, content_hash)
    if dup:
        known = getattr(dup, "doc_kind", None)
        if not known or known == "pdf_unknown":
            parsed = next((d.doc_type for d in library.pdf_documents if d.file == path.name), None)
            known = classify_upload_kind(path, pdf_doc_type=parsed) if parsed else None
        kind = forced_kind or (known if known and known != "pdf_unknown" else classify_upload_kind(path))
        stored = store.archive_file(company_key, path, doc_kind=kind)
        store.register_document(
            library,
            filename=path.name,
            doc_kind=kind,
            content_hash=content_hash,
            skipped=True,
            stored_path=stored,
        )
        store.save(library)
        sync_session_from_library(session, library)
        session.company_key = company_key
        _sync_drive_upload(session, library, stored, doc_kind=kind)
        return library, "skipped_duplicate"

    result = ingest_pdf(path)
    doc_kind = forced_kind or classify_upload_kind(path, pdf_doc_type=result.document.doc_type)
    sample = result.chunks[0].text if result.chunks else ""
    fy = detect_fiscal_year(path.name, sample, result.document.doc_type)
    fp = detect_fiscal_period(path.name)

    library.pdf_documents, library.pdf_chunks = merge_pdf(
        library.pdf_documents,
        library.pdf_chunks,
        result.document,
        result.chunks,
        path.name,
    )

    stored = store.archive_file(company_key, path, doc_kind=doc_kind)
    store.register_document(
        library,
        filename=path.name,
        doc_kind=doc_kind,
        content_hash=content_hash,
        fiscal_year=fy,
        fiscal_period=fp,
        page_count=result.document.page_count,
        chunk_count=len(result.chunks),
        stored_path=stored,
    )
    store.save(library)
    sync_session_from_library(session, library)
    session.company_key = company_key
    _sync_drive_upload(session, library, stored, doc_kind=doc_kind)
    return library, "ingested"


def _ingest_image_path(
    session: ResearchSession,
    path: Path,
    store: CompanyLibraryStore,
    content_hash: str,
    *,
    forced_kind: str | None = None,
) -> tuple[CompanyLibrary | None, str]:
    """Archive screenshots for visual guidance (no numeric parse)."""
    company_key = _resolve_key_after_company(session, store)
    if not company_key:
        return None, "no_company"

    library = store.load(company_key)
    if session.company:
        library.company = _merge_company(library.company, session.company)

    doc_kind = forced_kind or classify_image_kind(path.name)
    if store.find_duplicate(library, content_hash):
        stored = store.archive_file(company_key, path, doc_kind=doc_kind)
        store.register_document(
            library,
            filename=path.name,
            doc_kind=doc_kind,
            content_hash=content_hash,
            skipped=True,
            stored_path=stored,
        )
        store.save(library)
        sync_session_from_library(session, library)
        session.company_key = company_key
        _sync_drive_upload(session, library, stored, doc_kind=doc_kind)
        return library, "skipped_duplicate"

    stored = store.archive_file(company_key, path, doc_kind=doc_kind)
    store.register_document(
        library,
        filename=path.name,
        doc_kind=doc_kind,
        content_hash=content_hash,
        stored_path=stored,
    )
    store.save(library)
    sync_session_from_library(session, library)
    session.company_key = company_key
    _sync_drive_upload(session, library, stored, doc_kind=doc_kind)
    # Local vision: chart/table extract → session artifact + sidecar .chart.md
    _maybe_extract_chart(session, Path(stored) if stored else path)
    return library, "ingested"


def _maybe_extract_chart(session, image_path: Path) -> None:
    try:
        from iis.config import settings

        if not getattr(settings, "chart_vision_enabled", True):
            return
        from iis.vision.chart_extract import (
            extract_chart_image,
            store_chart_extract_on_session,
            write_sidecar_markdown,
        )

        result = extract_chart_image(image_path)
        if result.ok:
            store_chart_extract_on_session(session, result)
            write_sidecar_markdown(image_path, result)
    except Exception:
        pass


def _guess_kind(path: Path) -> str:
    return classify_upload_kind(path)


def _parse_excel_into(session: ResearchSession, library: CompanyLibrary, path: Path) -> None:
    from iis.models.artifacts import AgentArtifact
    from iis.models.enums import SourceTier
    from iis.parsers.capital_iq.estimates import parse_estimates_workbook
    from iis.parsers.capital_iq.financials import parse_financials_workbook

    name_lower = path.name.lower()

    if "financial" in name_lower:
        company, items = parse_financials_workbook(path)
        if company:
            library.company = _merge_company(library.company, company)
            session.company = library.company
        library.financial_line_items.extend(items)
    elif "estimate" in name_lower:
        company, consensus, guidance, summary = parse_estimates_workbook(path)
        if company:
            library.company = _merge_company(library.company, company)
            session.company = library.company
        library.consensus_metrics.extend(consensus)
        library.guidance_records.extend(guidance)
        session.agent_artifacts = [
            a for a in session.agent_artifacts if a.artifact_type != "estimates_summary"
        ]
        session.agent_artifacts.append(
            AgentArtifact(
                agent_id="data_extractor",
                artifact_type="estimates_summary",
                payload=summary,
                provenance=[path.name],
                source_tier=SourceTier.EXTRACTED,
            )
        )
    else:
        try:
            company, items = parse_financials_workbook(path)
            if items:
                if company:
                    library.company = _merge_company(library.company, company)
                    session.company = library.company
                library.financial_line_items.extend(items)
        except Exception:
            pass
        try:
            company, consensus, guidance, summary = parse_estimates_workbook(path)
            if consensus or guidance:
                if company:
                    library.company = _merge_company(library.company, company)
                    session.company = library.company
                library.consensus_metrics.extend(consensus)
                library.guidance_records.extend(guidance)
        except Exception:
            pass


def refresh_estimates_from_library(session: ResearchSession) -> ResearchSession:
    """Re-parse the latest CIQ Estimates workbook into session consensus + estimates_summary."""
    from iis.library.store import get_company_library_store
    from iis.models.artifacts import AgentArtifact
    from iis.models.enums import SourceTier
    from iis.parsers.capital_iq.estimates import parse_estimates_workbook

    if not session.company_key:
        return session
    store = get_company_library_store()
    lib = store.load(session.company_key)
    docs = [d for d in lib.documents if d.doc_kind == "capital_iq_estimates" and d.stored_path]
    docs.sort(key=lambda d: d.uploaded_at, reverse=True)
    for d in docs:
        path = Path(d.stored_path)
        if not path.exists():
            continue
        try:
            _company, consensus, guidance, summary = parse_estimates_workbook(path)
        except Exception:
            continue
        def _dump(rows):
            return [r.model_dump(mode="json") for r in rows]

        lib_dirty = False
        if consensus:
            session.consensus_metrics = consensus
            if _dump(lib.consensus_metrics) != _dump(consensus):
                lib.consensus_metrics = list(consensus)
                lib_dirty = True
        if guidance:
            session.guidance_records = guidance
            if _dump(lib.guidance_records) != _dump(guidance):
                lib.guidance_records = list(guidance)
                lib_dirty = True
        if lib_dirty:
            store.save(lib)
        if summary:
            session.agent_artifacts = [
                a for a in session.agent_artifacts if a.artifact_type != "estimates_summary"
            ]
            session.agent_artifacts.append(
                AgentArtifact(
                    agent_id="data_extractor",
                    artifact_type="estimates_summary",
                    payload=summary,
                    provenance=[path.name],
                    source_tier=SourceTier.EXTRACTED,
                )
            )
        break
    return session


def _merge_company(existing, new):
    from iis.models.artifacts import CompanyInfo

    if existing is None:
        return new
    if new is None:
        return existing
    return CompanyInfo(
        name=new.name or existing.name,
        bse_code=new.bse_code or existing.bse_code,
        nse_ticker=new.nse_ticker or existing.nse_ticker,
        accounting_standard=new.accounting_standard or existing.accounting_standard,
    )
