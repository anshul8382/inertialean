"""Global company-wise file index so peer uploads are findable without re-upload.

Source of truth remains each company's library.json. This index is a fast lookup
layer: filename / content_hash / ticker / name → company_key + document list.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from iis.config import settings

logger = logging.getLogger(__name__)

INDEX_NAME = "_index.json"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def index_path(base_dir: Path | None = None) -> Path:
    root = base_dir or (settings.data_dir / "companies")
    return root / INDEX_NAME


def rebuild_file_index(store: Any) -> dict[str, Any]:
    """Scan all company libraries and write ~/.iis/companies/_index.json."""
    companies: list[dict[str, Any]] = []
    by_hash: dict[str, list[dict[str, str]]] = {}
    by_filename: dict[str, list[dict[str, str]]] = {}
    by_alias: dict[str, str] = {}

    for key in store.list_companies():
        try:
            lib = store.load(key)
        except Exception as e:
            logger.warning("index skip %s: %s", key, e)
            continue
        co = lib.company
        name = co.name if co else key
        aliases = {_norm(key), _norm(name)}
        if co:
            if co.nse_ticker:
                aliases.add(_norm(co.nse_ticker))
            if co.bse_code:
                aliases.add(_norm(str(co.bse_code)))
                aliases.add(_norm(f"bse-{co.bse_code}"))
        for a in aliases:
            if a:
                by_alias[a] = key

        docs: list[dict[str, Any]] = []
        for d in lib.documents:
            row = {
                "filename": d.filename,
                "doc_kind": d.doc_kind,
                "content_hash": d.content_hash,
                "stored_path": d.stored_path,
                "fiscal_year": d.fiscal_year,
                "fiscal_period": d.fiscal_period,
                "status": d.status,
            }
            docs.append(row)
            ref = {"company_key": key, "name": name, "filename": d.filename, "doc_kind": d.doc_kind}
            if d.content_hash:
                by_hash.setdefault(d.content_hash, []).append(ref)
            fn = (d.filename or "").lower()
            if fn:
                by_filename.setdefault(fn, []).append(ref)

        companies.append(
            {
                "company_key": key,
                "name": name,
                "nse_ticker": co.nse_ticker if co else None,
                "bse_code": co.bse_code if co else None,
                "document_count": len(docs),
                "line_items": len(lib.financial_line_items),
                "pdf_documents": len(lib.pdf_documents),
                "guidance_records": len(lib.guidance_records),
                "documents": docs,
                "updated_at": lib.updated_at.isoformat() if lib.updated_at else None,
            }
        )

    companies.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
    payload = {
        "version": 1,
        "updated_at": datetime.utcnow().isoformat(),
        "company_count": len(companies),
        "companies": companies,
        "by_content_hash": by_hash,
        "by_filename": by_filename,
        "by_alias": by_alias,
    }
    path = index_path(store.base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def load_file_index(base_dir: Path | None = None) -> dict[str, Any]:
    path = index_path(base_dir)
    if not path.exists():
        return {"version": 1, "companies": [], "by_content_hash": {}, "by_filename": {}, "by_alias": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "companies": [], "by_content_hash": {}, "by_filename": {}, "by_alias": {}}


def find_in_index(
    query: str,
    *,
    store: Any | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Find company libraries / files by name, ticker, filename, or hash prefix."""
    q = (query or "").strip()
    if not q:
        return {"matched": False, "query": q, "companies": [], "files": []}

    idx = load_file_index(base_dir or (store.base_dir if store else None))
    if store and not idx.get("companies"):
        idx = rebuild_file_index(store)

    nq = _norm(q)
    ql = q.lower()
    companies: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []

    # Alias / company match
    alias_key = (idx.get("by_alias") or {}).get(nq)
    seen_keys: set[str] = set()
    for c in idx.get("companies") or []:
        key = c.get("company_key")
        if not key:
            continue
        hit = False
        if alias_key and key == alias_key:
            hit = True
        elif nq and nq in _norm(c.get("name") or ""):
            hit = True
        elif nq and nq in _norm(key):
            hit = True
        elif ql and any(ql in (d.get("filename") or "").lower() for d in (c.get("documents") or [])):
            hit = True
        if hit and key not in seen_keys:
            seen_keys.add(key)
            companies.append(
                {
                    "company_key": key,
                    "name": c.get("name"),
                    "nse_ticker": c.get("nse_ticker"),
                    "bse_code": c.get("bse_code"),
                    "document_count": c.get("document_count"),
                    "line_items": c.get("line_items"),
                    "documents": c.get("documents") or [],
                }
            )

    # Filename exact
    for ref in (idx.get("by_filename") or {}).get(ql, []):
        files.append(ref)
    # Filename substring
    if not files:
        for fn, refs in (idx.get("by_filename") or {}).items():
            if ql in fn:
                files.extend(refs)

    # Hash prefix
    if len(q) >= 8 and re.fullmatch(r"[a-f0-9]+", ql):
        for h, refs in (idx.get("by_content_hash") or {}).items():
            if h.startswith(ql):
                files.extend(refs)

    return {
        "matched": bool(companies or files),
        "query": q,
        "companies": companies[:20],
        "files": files[:40],
    }
