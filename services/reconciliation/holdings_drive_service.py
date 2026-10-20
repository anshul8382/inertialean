"""
Client Drive holdings statements: list / upload / start recon from Drive.

Looks under common folder names (Holdings, Holding Statement, …) and one
nested level — many clients keep files in "Holding Statement/" while an empty
app-created "Holdings/" also exists.
"""
from __future__ import annotations

import logging
import mimetypes
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Preferred upload target first; aliases used when listing.
DRIVE_HOLDINGS_FOLDER = "Holdings"
_HOLDINGS_FOLDER_ALIASES = (
    "Holdings",
    "Holding Statement",
    "Holding Statements",
    "Holdings Statement",
    "Holding",
)

_HOLDINGS_EXTS = (".csv", ".xlsx", ".xls", ".pdf")
_SKIP_NESTED = frozenset(
    {
        "emails",
        "email",
        "trade details",
        "trades",
        "trade",
        "contract notes",
    }
)


def _client_drive_root(client_id: int) -> Optional[str]:
    from models import Client

    c = Client.query.get(int(client_id))
    if not c:
        return None
    return (getattr(c, "google_drive_folder_id", None) or "").strip() or None


def _norm_folder_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _is_holdings_folder_name(name: str) -> bool:
    n = _norm_folder_name(name)
    if not n or n in _SKIP_NESTED:
        return False
    aliases = {_norm_folder_name(a) for a in _HOLDINGS_FOLDER_ALIASES}
    if n in aliases:
        return True
    # e.g. "Holding Statement Oct 2026"
    if n.startswith("holding") and "statement" in n:
        return True
    return False


def _discover_holdings_folders(root_id: str) -> List[Dict[str, Any]]:
    """
    Find holdings-like folders under the client root (and one nested Holdings
    under Holding Statement). Prefer folders that already have statement files.
    """
    from services.client_google_drive_service import list_folder_children

    root_kids = list_folder_children(root_id)
    if not root_kids.get("ok"):
        return []

    folder_mime = "application/vnd.google-apps.folder"
    candidates: List[Dict[str, Any]] = []
    for item in root_kids.get("files") or []:
        if (item.get("mimeType") or "") != folder_mime:
            continue
        name = item.get("name") or ""
        if not _is_holdings_folder_name(name):
            continue
        candidates.append(
            {
                "id": item.get("id"),
                "name": name,
                "path": name,
            }
        )
        # Nested Holdings/ under Holding Statement (common layout)
        nested = list_folder_children(item["id"])
        if not nested.get("ok"):
            continue
        for child in nested.get("files") or []:
            if (child.get("mimeType") or "") != folder_mime:
                continue
            cname = child.get("name") or ""
            cn = _norm_folder_name(cname)
            if cn in _SKIP_NESTED:
                continue
            if cn == "holdings" or _is_holdings_folder_name(cname):
                candidates.append(
                    {
                        "id": child.get("id"),
                        "name": cname,
                        "path": f"{name}/{cname}",
                    }
                )
    return candidates


def ensure_holdings_folder(client_id: int) -> Dict[str, Any]:
    """
    Return a folder for uploads. Prefer an existing non-empty holdings-like
    folder; otherwise ensure top-level Holdings/.
    """
    from services.client_google_drive_service import (
        ensure_subfolder,
        list_folder_files,
    )

    root = _client_drive_root(client_id)
    if not root:
        return {
            "ok": False,
            "error": "Client has no Google Drive folder linked.",
            "needs_drive": True,
        }

    discovered = _discover_holdings_folders(root)
    scored: List[tuple] = []
    for fol in discovered:
        listed = list_folder_files(fol["id"])
        n = 0
        if listed.get("ok"):
            for f in listed.get("files") or []:
                low = (f.get("name") or "").lower()
                if any(low.endswith(ext) for ext in _HOLDINGS_EXTS):
                    n += 1
        scored.append((n, fol))
    scored.sort(key=lambda t: (-t[0], t[1].get("path") or ""))
    if scored and scored[0][0] > 0:
        best = scored[0][1]
        return {
            "ok": True,
            "folder_id": best["id"],
            "created": False,
            "root_folder_id": root,
            "folder_name": best.get("path") or best.get("name"),
            "source_folders": [
                {"id": f["id"], "path": f.get("path"), "file_count": n}
                for n, f in scored
            ],
        }

    # Fall back to canonical Holdings/ (create if missing)
    res = ensure_subfolder(root, DRIVE_HOLDINGS_FOLDER)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or "Could not ensure Holdings/"}
    return {
        "ok": True,
        "folder_id": res.get("folder_id"),
        "created": bool(res.get("created")),
        "root_folder_id": root,
        "folder_name": DRIVE_HOLDINGS_FOLDER,
    }


def list_client_holdings_files(client_id: int) -> Dict[str, Any]:
    """List statement-like files in holdings-like Drive folders (newest first)."""
    from services.client_google_drive_service import list_folder_files

    root = _client_drive_root(client_id)
    if not root:
        return {
            "ok": False,
            "error": "Client has no Google Drive folder linked.",
            "needs_drive": True,
            "files": [],
        }

    discovered = _discover_holdings_folders(root)
    if not discovered:
        ens = ensure_holdings_folder(client_id)
        if not ens.get("ok"):
            return {
                "ok": False,
                "error": ens.get("error"),
                "needs_drive": ens.get("needs_drive"),
                "files": [],
            }
        discovered = [
            {
                "id": ens["folder_id"],
                "name": ens.get("folder_name") or DRIVE_HOLDINGS_FOLDER,
                "path": ens.get("folder_name") or DRIVE_HOLDINGS_FOLDER,
            }
        ]

    seen: Set[str] = set()
    files: List[Dict[str, Any]] = []
    folder_ids: List[str] = []
    for fol in discovered:
        fid = fol.get("id")
        if not fid or fid in folder_ids:
            continue
        folder_ids.append(fid)
        listed = list_folder_files(fid)
        if not listed.get("ok"):
            continue
        for f in listed.get("files") or []:
            name = (f.get("name") or "").strip()
            low = name.lower()
            if not any(low.endswith(ext) for ext in _HOLDINGS_EXTS):
                continue
            iid = f.get("id")
            if not iid or iid in seen:
                continue
            seen.add(iid)
            files.append(
                {
                    "id": iid,
                    "name": name,
                    "modified_time": f.get("modifiedTime"),
                    "size": f.get("size"),
                    "web_view_link": f.get("webViewLink"),
                    "mime_type": f.get("mimeType"),
                    "folder_path": fol.get("path") or fol.get("name"),
                }
            )

    files.sort(key=lambda r: r.get("modified_time") or "", reverse=True)
    return {
        "ok": True,
        "files": files,
        "folder_id": folder_ids[0] if folder_ids else None,
        "folder_ids": folder_ids,
        "count": len(files),
        "source_paths": [f.get("path") for f in discovered],
    }


def _guess_mime(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename or "")
    return mime or "application/octet-stream"


def upload_statement_if_missing(
    client_id: int,
    *,
    filename: str,
    data: bytes,
) -> Dict[str, Any]:
    """
    Upload statement into the preferred holdings folder only if the same
    filename is not already there. Soft-fail: never blocks local recon.
    """
    from services.client_google_drive_service import find_file_by_name, upload_bytes

    fname = (filename or "").strip() or "holdings_statement.bin"
    if not data:
        return {"ok": False, "error": "Empty file", "skipped": True}
    ens = ensure_holdings_folder(client_id)
    if not ens.get("ok"):
        return {
            "ok": False,
            "error": ens.get("error"),
            "needs_drive": ens.get("needs_drive"),
            "skipped": True,
        }
    folder_id = ens["folder_id"]
    existing = find_file_by_name(folder_id, fname)
    if existing:
        return {
            "ok": True,
            "already_present": True,
            "file_id": existing.get("id"),
            "name": existing.get("name"),
            "folder_id": folder_id,
            "web_view_link": existing.get("webViewLink"),
        }
    # Also skip if same name exists in any discovered holdings folder
    for fol in _discover_holdings_folders(ens.get("root_folder_id") or ""):
        if fol.get("id") == folder_id:
            continue
        hit = find_file_by_name(fol["id"], fname)
        if hit:
            return {
                "ok": True,
                "already_present": True,
                "file_id": hit.get("id"),
                "name": hit.get("name"),
                "folder_id": fol["id"],
                "web_view_link": hit.get("webViewLink"),
            }
    up = upload_bytes(
        folder_id,
        fname,
        data,
        mime_type=_guess_mime(fname),
    )
    if not up.get("ok"):
        return {"ok": False, "error": up.get("error"), "folder_id": folder_id}
    return {
        "ok": True,
        "already_present": False,
        "uploaded": True,
        "file_id": up.get("file_id"),
        "name": up.get("name"),
        "folder_id": folder_id,
        "web_view_link": up.get("web_view_link"),
    }


def create_run_from_drive_file(
    *,
    client_id: int,
    file_id: str,
    broker: Optional[str],
    user_id: int,
) -> Dict[str, Any]:
    """Download a holdings statement from Drive and start a recon run."""
    from services.client_google_drive_service import download_file_bytes
    from services.reconciliation.run_service import create_run_from_bytes

    dl = download_file_bytes(file_id)
    if not dl.get("ok"):
        raise ValueError(dl.get("error") or "Could not download Drive file")
    name = dl.get("name") or "drive_holdings.bin"
    data = dl.get("content") or b""
    run = create_run_from_bytes(
        client_id=client_id,
        filename=name,
        data=data,
        broker=broker,
        user_id=user_id,
        drive_file_id=file_id,
        drive_synced=True,
    )
    return run
