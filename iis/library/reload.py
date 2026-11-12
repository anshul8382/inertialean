"""Auto-load company files from fixed local folders (and optionally Google Drive)."""
from __future__ import annotations

import logging
from pathlib import Path

from iis.library.ingest import ingest_file_to_library
from iis.library.layout import ensure_company_layout, iter_data_files
from iis.library.store import CompanyLibraryStore, file_content_hash, get_company_library_store
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)


def sync_company_hint_from_drive(
    hint: str,
    store: CompanyLibraryStore | None = None,
    *,
    pull_drive: bool = True,
    pull_telegram: bool = False,
) -> dict:
    """
    Pull Shared Drive / Telegram files for a company into the local library.
    Safe to call on Continue / lookup — skips filenames that already exist locally.

    Telegram defaults off for fast Continue; research/start still pulls reports.
    """
    from iis.library.detect import company_key_from_info
    from iis.models.artifacts import CompanyInfo

    store = store or get_company_library_store()
    hint = (hint or "").strip()
    summary = {
        "hint": hint,
        "company_key": None,
        "drive_downloaded": 0,
        "ingested": 0,
        "skipped_duplicate": 0,
        "pulled": False,
        "telegram": None,
    }
    if not hint:
        return summary

    session = ResearchSession()
    session.company = CompanyInfo(name=hint)
    session.company_key = company_key_from_info(None, hint)
    # Prefer an existing library key if we already know this company
    existing = store.find_library_for_session(session)
    if existing:
        session.company_key = existing.company_key
        if existing.company and existing.company.name:
            session.company = existing.company

    key = session.company_key or company_key_from_info(session.company, hint)
    if not key:
        return summary
    session.company_key = key
    summary["company_key"] = key

    # Telegram group → brokerage_reports (opt-in; can be slow)
    if pull_telegram:
        try:
            from iis.config import settings
            from iis.integrations.telegram_inbox import sync_pdfs_for_company, telegram_session_exists

            if settings.telegram_enabled and telegram_session_exists() and settings.telegram_group:
                display = session.company.name if session.company and session.company.name else hint
                summary["telegram"] = sync_pdfs_for_company(
                    display, search_limit=80, max_files=25, ingest=True
                )
        except Exception as e:
            logger.warning("telegram company pull failed for %s: %s", hint, e)
            summary["telegram"] = {"error": str(e), "downloaded": 0, "ingested": 0}

    detail = reload_company_from_disk(session, store, pull_drive=pull_drive)
    summary["drive_downloaded"] = detail.get("drive_downloaded") or 0
    summary["ingested"] = detail.get("ingested") or 0
    summary["skipped_duplicate"] = detail.get("skipped_duplicate") or 0
    summary["pulled"] = True
    return summary


def reload_company_from_disk(
    session: ResearchSession,
    store: CompanyLibraryStore | None = None,
    *,
    pull_drive: bool = True,
) -> dict:
    """
    Ensure folder layout exists, optionally pull from Drive, then ingest any
    on-disk files not yet in the library. Safe to call on every session open.
    """
    store = store or get_company_library_store()
    summary = {
        "company_key": None,
        "scanned": 0,
        "ingested": 0,
        "skipped_duplicate": 0,
        "failed": 0,
        "drive_downloaded": 0,
        "folders": [],
    }

    key = store.resolve_key(session) or session.company_key
    if not key and session.company and session.company.name:
        from iis.library.detect import company_key_from_info

        key = company_key_from_info(session.company, session.company.name)
    if not key:
        return summary

    session.company_key = key
    summary["company_key"] = key
    root = ensure_company_layout(store.base_dir, key)
    summary["folders"] = [p.name for p in root.iterdir() if p.is_dir()]

    if pull_drive:
        try:
            from iis.integrations.gdrive import pull_company_from_drive

            name = session.company.name if session.company else None
            if name:
                summary["drive_downloaded"] = pull_company_from_drive(name, root) or 0
        except Exception:
            logger.exception("Drive pull skipped for %s", key)

    library = store.load(key)
    known_hashes = {d.content_hash for d in library.documents if d.content_hash}
    known_names = {d.filename for d in library.documents}

    for path, _kind in iter_data_files(root):
        summary["scanned"] += 1
        try:
            h = file_content_hash(path)
            if h in known_hashes or path.name in known_names:
                # Still attach path to session uploaded list for overview
                if str(path) not in session.uploaded_files:
                    session.uploaded_files.append(str(path))
                summary["skipped_duplicate"] += 1
                continue
            _, status = ingest_file_to_library(session, path, store)
            if status == "ingested":
                summary["ingested"] += 1
                known_hashes.add(h)
                known_names.add(path.name)
            elif status == "skipped_duplicate":
                summary["skipped_duplicate"] += 1
            else:
                summary["failed"] += 1
            if str(path) not in session.uploaded_files:
                session.uploaded_files.append(str(path))
        except Exception:
            logger.exception("Auto-load failed for %s", path)
            summary["failed"] += 1

    store.sync_to_session(session)
    return summary
