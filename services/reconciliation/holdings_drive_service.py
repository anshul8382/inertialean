"""
Client Drive Holdings/ folder: list statements, upload if missing, start recon from Drive.
"""
from __future__ import annotations

import logging
import mimetypes
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DRIVE_HOLDINGS_FOLDER = "Holdings"

_HOLDINGS_EXTS = (".csv", ".xlsx", ".xls", ".pdf")


def _client_drive_root(client_id: int) -> Optional[str]:
    from models import Client

    c = Client.query.get(int(client_id))
    if not c:
        return None
    return (getattr(c, "google_drive_folder_id", None) or "").strip() or None


def ensure_holdings_folder(client_id: int) -> Dict[str, Any]:
    """Ensure Holdings/ under the client Drive root. Returns folder_id or error."""
    from services.client_google_drive_service import ensure_subfolder

    root = _client_drive_root(client_id)
    if not root:
        return {
            "ok": False,
            "error": "Client has no Google Drive folder linked.",
            "needs_drive": True,
        }
    res = ensure_subfolder(root, DRIVE_HOLDINGS_FOLDER)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or "Could not ensure Holdings/"}
    return {
        "ok": True,
        "folder_id": res.get("folder_id"),
        "created": bool(res.get("created")),
        "root_folder_id": root,
    }


def list_client_holdings_files(client_id: int) -> Dict[str, Any]:
    """List statement-like files in Drive Holdings/ (newest first)."""
    from services.client_google_drive_service import list_folder_files

    ens = ensure_holdings_folder(client_id)
    if not ens.get("ok"):
        return {
            "ok": False,
            "error": ens.get("error"),
            "needs_drive": ens.get("needs_drive"),
            "files": [],
        }
    listed = list_folder_files(ens["folder_id"])
    if not listed.get("ok"):
        return {
            "ok": False,
            "error": listed.get("error"),
            "files": [],
            "folder_id": ens.get("folder_id"),
        }
    files = []
    for f in listed.get("files") or []:
        name = (f.get("name") or "").strip()
        low = name.lower()
        if not any(low.endswith(ext) for ext in _HOLDINGS_EXTS):
            continue
        files.append(
            {
                "id": f.get("id"),
                "name": name,
                "modified_time": f.get("modifiedTime"),
                "size": f.get("size"),
                "web_view_link": f.get("webViewLink"),
                "mime_type": f.get("mimeType"),
            }
        )
    return {
        "ok": True,
        "files": files,
        "folder_id": ens.get("folder_id"),
        "count": len(files),
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
    Upload statement into Drive Holdings/ only if the same filename is not already there.
    Soft-fail: never blocks local recon.
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
    """Download a Holdings/ file from Drive and start a recon run."""
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
