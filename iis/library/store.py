"""Persist company libraries to disk."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from iis.config import settings
from iis.library.detect import company_key_from_info, _slug
from iis.library.merge import sync_session_from_library
from iis.library.models import CompanyLibrary, LibraryDocument
from iis.models.session import ResearchSession


_PDF_KINDS = {"annual_report", "transcript", "earnings_presentation", "brokerage_report"}


def apply_library_doc_kinds(session: ResearchSession, library: CompanyLibrary) -> None:
    """Library doc_kind (user section choice / folder) overrides the PDF text classifier."""
    kinds = {
        Path(d.filename).name: d.doc_kind for d in library.documents if d.doc_kind in _PDF_KINDS
    }
    if not kinds:
        return
    for doc in session.pdf_documents:
        k = kinds.get(Path(doc.file).name)
        if k and doc.doc_type != k:
            doc.doc_type = k
    for chunk in session.pdf_chunks:
        k = kinds.get(Path(chunk.file).name)
        if k and chunk.doc_type != k:
            chunk.doc_type = k


def file_content_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


class CompanyLibraryStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (settings.data_dir / "companies")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, company_key: str) -> Path:
        safe = company_key.replace("/", "_")
        return self.base_dir / safe / "library.json"

    def load(self, company_key: str) -> CompanyLibrary:
        path = self._path(company_key)
        if not path.exists():
            return CompanyLibrary(company_key=company_key)
        return CompanyLibrary.model_validate_json(path.read_text())

    def save(self, library: CompanyLibrary) -> None:
        library.updated_at = datetime.utcnow()
        path = self._path(library.company_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(library.model_dump_json(indent=2))
        # Keep global file index fresh so peer uploads are findable later
        try:
            from iis.library.file_index import rebuild_file_index

            rebuild_file_index(self)
        except Exception:
            pass

    def list_companies(self) -> list[str]:
        keys: list[str] = []
        if not self.base_dir.exists():
            return keys
        for p in self.base_dir.iterdir():
            if p.is_dir() and (p / "library.json").exists():
                keys.append(p.name)
        return sorted(keys)

    def find_duplicate(self, library: CompanyLibrary, content_hash: str) -> LibraryDocument | None:
        for doc in library.documents:
            if doc.content_hash == content_hash:
                return doc
        return None

    def files_dir(self, company_key: str) -> Path:
        return self.base_dir / company_key.replace("/", "_") / "files"

    def company_dir(self, company_key: str) -> Path:
        from iis.library.layout import ensure_company_layout

        return ensure_company_layout(self.base_dir, company_key)

    def archive_file(self, company_key: str, source: Path, *, doc_kind: str | None = None) -> str:
        """Copy upload into fixed section folder; return absolute path."""
        from iis.library.layout import classify_filename_kind, ensure_company_layout, folder_for_kind

        root = ensure_company_layout(self.base_dir, company_key)
        kind = doc_kind or classify_filename_kind(source.name)
        rel = folder_for_kind(kind)
        dest_dir = root / rel
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_name = source.name.replace("/", "_")
        dest = dest_dir / safe_name
        if not dest.exists() or file_content_hash(dest) != file_content_hash(source):
            dest.write_bytes(source.read_bytes())
        return str(dest.resolve())

    def reclassify_document(
        self,
        company_key: str,
        filename: str,
        new_kind: str,
    ) -> dict:
        """Move an archived file into another section folder and update library.json."""
        from iis.library.layout import ensure_company_layout, folder_for_kind
        from iis.integrations.gdrive import sync_upload_for_company

        library = self.load(company_key)
        doc = next((d for d in library.documents if d.filename == filename), None)
        if not doc:
            raise FileNotFoundError(f"Document {filename} not in library")

        root = ensure_company_layout(self.base_dir, company_key)
        old_path = Path(doc.stored_path) if doc.stored_path else None
        if not old_path or not old_path.exists():
            # fall back to searching under company root
            matches = list(root.rglob(filename))
            old_path = matches[0] if matches else None
        if not old_path or not old_path.exists():
            raise FileNotFoundError(f"File missing on disk: {filename}")

        dest_dir = root / folder_for_kind(new_kind)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        if old_path.resolve() != dest.resolve():
            dest.write_bytes(old_path.read_bytes())
            if old_path.resolve() != dest.resolve() and old_path.exists():
                try:
                    old_path.unlink()
                except OSError:
                    pass

        doc.doc_kind = new_kind
        doc.stored_path = str(dest.resolve())
        self.save(library)

        company_name = library.company.name if library.company else None
        if company_name:
            sync_upload_for_company(company_name, dest, doc_kind=new_kind)

        return {
            "filename": filename,
            "doc_kind": new_kind,
            "stored_path": doc.stored_path,
            "folder": folder_for_kind(new_kind),
        }

    def find_library_for_session(self, session: ResearchSession) -> CompanyLibrary | None:
        """Resolve library by company_key, BSE/NSE, or fuzzy name match."""
        candidates: list[str] = []
        if session.company_key:
            candidates.append(session.company_key)
        if session.company:
            if session.company.bse_code:
                candidates.append(f"bse-{session.company.bse_code.strip()}")
            if session.company.nse_ticker:
                candidates.append(f"nse-{session.company.nse_ticker.lower().replace(':', '_')}")
            if session.company.name:
                candidates.append(_slug(session.company.name))

        seen: set[str] = set()
        for key in candidates:
            if not key or key in seen:
                continue
            seen.add(key)
            lib = self.load(key)
            if lib.financial_line_items or lib.documents:
                session.company_key = key
                return lib

        hint_tokens: set[str] = set()
        for key in candidates:
            hint_tokens.add(key.replace("-", " "))
            for part in key.split("-"):
                if len(part) >= 3:
                    hint_tokens.add(part)

        best: tuple[int, CompanyLibrary] | None = None
        for stored_key in self.list_companies():
            lib = self.load(stored_key)
            if not lib.financial_line_items and not lib.documents:
                continue
            score = self._match_score(candidates, stored_key, lib)
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, lib)

        if best:
            lib = best[1]
            session.company_key = lib.company_key
            return lib
        return None

    def _match_score(self, hints: list[str], stored_key: str, lib: CompanyLibrary) -> int:
        for hint in hints:
            if hint == stored_key:
                return 100
            if _slug(hint) == stored_key:
                return 95
        if not lib.company:
            return 0
        score = 0
        c = lib.company
        for hint in hints:
            h = hint.lower()
            if c.bse_code and h == f"bse-{c.bse_code.strip()}":
                score = max(score, 90)
            if c.nse_ticker and h == f"nse-{c.nse_ticker.lower()}":
                score = max(score, 90)
            if c.name:
                name_l = c.name.lower()
                slug = _slug(c.name)
                if h == slug or _slug(hint) == slug:
                    score = max(score, 85)
                if len(h) >= 4 and h.replace("-", " ") in name_l:
                    score = max(score, 70)
                if len(h) >= 4 and name_l.split()[0] in h.replace("-", " "):
                    score = max(score, 60)
        return score

    def register_document(
        self,
        library: CompanyLibrary,
        *,
        filename: str,
        doc_kind: str,
        content_hash: str,
        fiscal_year: str | None = None,
        fiscal_period: str | None = None,
        page_count: int | None = None,
        line_item_count: int = 0,
        chunk_count: int = 0,
        skipped: bool = False,
        stored_path: str | None = None,
    ) -> LibraryDocument:
        src_name = Path(filename).name
        library.documents = [d for d in library.documents if d.filename != src_name]
        doc = LibraryDocument(
            id=str(uuid4())[:12],
            filename=src_name,
            doc_kind=doc_kind,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            content_hash=content_hash,
            page_count=page_count,
            line_item_count=line_item_count,
            chunk_count=chunk_count,
            status="skipped_duplicate" if skipped else "ingested",
            stored_path=stored_path,
        )
        library.documents.append(doc)
        library.documents.sort(key=lambda d: (d.doc_kind, d.fiscal_year or "", d.fiscal_period or ""), reverse=True)
        return doc

    def resolve_key(self, session: ResearchSession) -> str | None:
        if session.company_key:
            key = session.company_key
        else:
            key = company_key_from_info(session.company)
        new_key = company_key_from_info(session.company)
        if new_key and key and new_key != key and self.load(key).documents:
            self._migrate_library(key, new_key)
            session.company_key = new_key
            return new_key
        if new_key:
            session.company_key = new_key
            return new_key
        return key

    def _migrate_library(self, old_key: str, new_key: str) -> None:
        """Merge library when company identity upgrades (hint → BSE code)."""
        from iis.library.merge import consensus_key, guidance_key, line_item_key

        old = self.load(old_key)
        new = self.load(new_key)
        if not new.company and old.company:
            new.company = old.company
        for doc in old.documents:
            if not any(d.content_hash == doc.content_hash for d in new.documents):
                new.documents.append(doc)

        li_map = {line_item_key(i): i for i in new.financial_line_items}
        for item in old.financial_line_items:
            li_map[line_item_key(item)] = item
        new.financial_line_items = list(li_map.values())

        cm_map = {consensus_key(i): i for i in new.consensus_metrics}
        for item in old.consensus_metrics:
            cm_map[consensus_key(item)] = item
        new.consensus_metrics = list(cm_map.values())

        gr_map = {guidance_key(i): i for i in new.guidance_records}
        for item in old.guidance_records:
            gr_map[guidance_key(item)] = item
        new.guidance_records = list(gr_map.values())

        seen_pdf = {d.file for d in new.pdf_documents}
        for doc in old.pdf_documents:
            if doc.file not in seen_pdf:
                new.pdf_documents.append(doc)
                seen_pdf.add(doc.file)
        new.pdf_chunks = new.pdf_chunks + [c for c in old.pdf_chunks if c.file in seen_pdf]

        self.save(new)
        old_path = self._path(old_key)
        if old_path.exists():
            old_path.unlink()

    def sync_to_session(self, session: ResearchSession) -> ResearchSession:
        library = self.find_library_for_session(session)
        if not library:
            key = self.resolve_key(session)
            if not key:
                return session
            library = self.load(key)
        if not library.documents and not library.financial_line_items:
            return session
        sync_session_from_library(session, library)
        session.company_key = library.company_key
        apply_library_doc_kinds(session, library)
        return session

    def inventory_for_session(self, session: ResearchSession) -> dict | None:
        library = self.find_library_for_session(session)
        if library:
            session.company_key = library.company_key
            key = library.company_key
        else:
            key = self.resolve_key(session)
        if not key:
            return None
        if not library:
            library = self.load(key)
        if not library.documents and not library.financial_line_items:
            return {"company_key": key, "annual_reports": [], "concalls": [], "documents": []}
        return library.inventory()

    def list_inventories(self) -> list[dict]:
        """Prefer companies/_index.json (fast). Fall back to per-library load only if needed."""
        try:
            from iis.library.file_index import load_file_index

            idx = load_file_index(self.base_dir)
            companies = idx.get("companies") or []
            if companies:
                rows = []
                for c in companies:
                    rows.append(
                        {
                            "company_key": c.get("company_key"),
                            "name": c.get("name") or c.get("company_key"),
                            "bse_code": c.get("bse_code"),
                            "nse_ticker": c.get("nse_ticker"),
                            "total_line_items": c.get("line_items") or 0,
                            "total_pdf_documents": c.get("pdf_documents") or 0,
                            "document_count": c.get("document_count") or 0,
                            "financial_periods": [],
                            "updated_at": idx.get("updated_at") or "",
                        }
                    )
                rows.sort(
                    key=lambda r: (r.get("total_line_items") or 0) + (r.get("total_pdf_documents") or 0),
                    reverse=True,
                )
                return rows
        except Exception:
            pass
        rows: list[dict] = []
        for key in self.list_companies():
            try:
                # Light peek — avoid full pydantic when possible
                path = self._path(key)
                raw = json.loads(path.read_text(encoding="utf-8"))
                company = raw.get("company") if isinstance(raw.get("company"), dict) else {}
                docs = raw.get("documents") or []
                rows.append(
                    {
                        "company_key": key,
                        "name": (company or {}).get("name") or key,
                        "bse_code": (company or {}).get("bse_code"),
                        "nse_ticker": (company or {}).get("nse_ticker"),
                        "total_line_items": len(raw.get("financial_line_items") or []),
                        "total_pdf_documents": sum(
                            1 for d in docs if (d or {}).get("doc_kind") in _PDF_KINDS
                        ),
                        "document_count": len(docs),
                        "financial_periods": [],
                        "updated_at": raw.get("updated_at") or "",
                    }
                )
            except Exception:
                continue
        rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        return rows


    def lookup_by_hint(self, hint: str) -> dict:
        """Resolve company library by name/ticker hint without a full session."""
        from iis.models.artifacts import CompanyInfo
        from iis.models.session import ResearchSession

        hint = (hint or "").strip()
        session = ResearchSession()
        session.company = CompanyInfo(name=hint)
        session.company_key = company_key_from_info(None, hint)
        lib = self.find_library_for_session(session)
        if not lib or (not lib.financial_line_items and not lib.documents):
            return {
                "matched": False,
                "hint": hint,
                "company_key": session.company_key,
                "company": None,
                "inventory": {
                    "company_key": session.company_key,
                    "annual_reports": [],
                    "concalls": [],
                    "capital_iq_financials": [],
                    "capital_iq_estimates": [],
                    "financial_periods": [],
                    "total_line_items": 0,
                    "total_pdf_documents": 0,
                },
                "missing": [
                    "Capital IQ Financials — Annual + Quarterly (.xls)",
                    "Capital IQ Estimates (.xls)",
                    "Annual report PDFs",
                    "Concall / presentation PDFs",
                ],
                "total_line_items": 0,
                "total_pdf_documents": 0,
            }
        inv = lib.inventory()
        missing: list[str] = []
        if not inv.get("capital_iq_financials"):
            missing.append("Capital IQ Financials — Annual + Quarterly (.xls)")
        elif len(inv.get("capital_iq_financials") or []) < 2:
            missing.append("Capital IQ Financials — add Quarterly (or Annual) workbook too")
        if not inv.get("capital_iq_estimates"):
            missing.append("Capital IQ Estimates (.xls)")
        # Prefer file presence over fiscal-year tags (years often unset after ingest)
        if not (inv.get("annual_report_files") or inv.get("annual_reports")):
            missing.append("Annual report PDFs")
        if not (inv.get("concall_files") or inv.get("concalls") or inv.get("presentations")):
            missing.append("Concall / presentation PDFs")
        return {
            "matched": True,
            "hint": hint,
            "company_key": lib.company_key,
            "company": lib.company.model_dump() if lib.company else None,
            "inventory": inv,
            "missing": missing,
            "total_line_items": len(lib.financial_line_items),
            "total_pdf_documents": len(lib.pdf_documents),
        }


_store: CompanyLibraryStore | None = None


def get_company_library_store() -> CompanyLibraryStore:
    global _store
    if _store is None:
        _store = CompanyLibraryStore()
    return _store
