"""
Per-client Google Drive folder helpers + suitability Doc upload/export.

Uses the same service account as codebase backups. Prefer Shared drive folders
shared with the SA as Content manager (My Drive uploads often hit storageQuotaExceeded).
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

DRIVE_SCOPE = ("https://www.googleapis.com/auth/drive",)
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GDOC_MIME = "application/vnd.google-apps.document"

_FOLDER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{10,}$")


def _service_account_path(app_config: Optional[dict] = None) -> Path:
    from services.google_drive_backup_service import _service_account_path as _sa

    return _sa(app_config)


def service_account_email(app_config: Optional[dict] = None) -> str:
    sa = _service_account_path(app_config)
    if not sa.is_file():
        return ""
    try:
        data = json.loads(sa.read_text(encoding="utf-8"))
        return (data.get("client_email") or "").strip()
    except Exception:
        return ""


def normalize_folder_id(raw: Optional[str]) -> str:
    """Accept a bare folder ID or a Drive folder URL."""
    s = (raw or "").strip()
    if not s:
        return ""
    # https://drive.google.com/drive/folders/XXXX?usp=sharing
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    m2 = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", s)
    if m2:
        return m2.group(1)
    if _FOLDER_ID_RE.match(s):
        return s
    return s.split("/")[-1].split("?")[0].strip()


def folder_url(folder_id: Optional[str]) -> str:
    fid = normalize_folder_id(folder_id)
    if not fid:
        return ""
    return f"https://drive.google.com/drive/folders/{fid}"


def client_drive_columns_ready() -> bool:
    try:
        from flask import has_app_context, current_app
        from sqlalchemy import inspect
        from extensions import db

        def _check():
            cols = {c["name"] for c in inspect(db.engine).get_columns("client")}
            return "google_drive_folder_id" in cols

        if has_app_context():
            return _check()
        with current_app.app_context():
            return _check()
    except Exception:
        return False


def suitability_table_ready() -> bool:
    try:
        from flask import has_app_context, current_app
        from sqlalchemy import inspect
        from extensions import db

        def _check():
            return inspect(db.engine).has_table("suitability_report")

        if has_app_context():
            return _check()
        with current_app.app_context():
            return _check()
    except Exception:
        return False


def _drive_service(app_config: Optional[dict] = None):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    sa = _service_account_path(app_config)
    if not sa.is_file():
        raise FileNotFoundError(f"service account missing: {sa}")
    creds = service_account.Credentials.from_service_account_file(
        str(sa), scopes=list(DRIVE_SCOPE)
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def ensure_subfolder(
    parent_id: str,
    name: str,
    *,
    app_config: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Return (or create) a child folder named ``name`` under parent_id.
    """
    parent = normalize_folder_id(parent_id)
    folder_name = (name or "").strip()
    if not parent or not folder_name:
        return {"ok": False, "error": "parent_id and name are required"}
    try:
        drive = _drive_service(app_config)
        # Escape single quotes in name for Drive query
        safe = folder_name.replace("'", "\\'")
        q = (
            f"name = '{safe}' and '{parent}' in parents and "
            f"mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        listed = (
            drive.files()
            .list(
                q=q,
                spaces="drive",
                fields="files(id,name)",
                pageSize=5,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files = listed.get("files") or []
        if files:
            return {"ok": True, "folder_id": files[0]["id"], "created": False}
        meta = (
            drive.files()
            .create(
                body={
                    "name": folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent],
                },
                fields="id,name",
                supportsAllDrives=True,
            )
            .execute()
        )
        return {"ok": True, "folder_id": meta.get("id"), "created": True}
    except Exception as e:
        logger.exception("ensure_subfolder failed")
        return {"ok": False, "error": str(e)}


def upload_bytes(
    folder_id: str,
    name: str,
    data: bytes,
    *,
    mime_type: str = "text/html",
    app_config: Optional[dict] = None,
) -> Dict[str, Any]:
    """Upload raw bytes into a Drive folder (no Google Docs conversion)."""
    folder = normalize_folder_id(folder_id)
    fname = (name or "").strip()
    if not folder or not fname:
        return {"ok": False, "error": "folder_id and name are required"}
    if data is None:
        return {"ok": False, "error": "data is required"}
    try:
        from googleapiclient.http import MediaIoBaseUpload
    except ImportError as e:
        return {"ok": False, "error": f"Missing Drive libs: {e}"}
    try:
        drive = _drive_service(app_config)
        media = MediaIoBaseUpload(
            io.BytesIO(data), mimetype=mime_type or "application/octet-stream", resumable=True
        )
        meta = (
            drive.files()
            .create(
                body={"name": fname, "parents": [folder]},
                media_body=media,
                fields="id,name,webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        return {
            "ok": True,
            "file_id": meta.get("id"),
            "name": meta.get("name"),
            "web_view_link": meta.get("webViewLink"),
            "folder_id": folder,
        }
    except Exception as e:
        msg = str(e)
        if "storageQuotaExceeded" in msg or "storage quota" in msg.lower():
            sa_email = service_account_email(app_config) or "the app service account"
            msg = (
                "Service accounts cannot upload to personal My Drive folders. "
                f"Put the client folder on a Shared drive, add {sa_email} as Content manager, "
                f"then retry. Original: {e}"
            )
        logger.exception("Drive upload_bytes failed")
        return {"ok": False, "error": msg}


def upload_docx_as_google_doc(
    local_path: Path,
    *,
    folder_id: str,
    name: str,
    share_writer_email: Optional[str] = None,
    app_config: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Upload a .docx into folder_id and convert to a Google Doc.
    Optionally grant writer access to share_writer_email.
    """
    path = Path(local_path)
    if not path.is_file():
        return {"ok": False, "error": f"File not found: {path}"}
    folder = normalize_folder_id(folder_id)
    if not folder:
        return {"ok": False, "error": "Google Drive folder ID is required"}

    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        return {"ok": False, "error": f"Missing Drive libs: {e}"}

    try:
        drive = _drive_service(app_config)
        media = MediaFileUpload(str(path), mimetype=DOCX_MIME, resumable=True)
        meta = (
            drive.files()
            .create(
                body={
                    "name": name,
                    "parents": [folder],
                    "mimeType": GDOC_MIME,
                },
                media_body=media,
                fields="id,name,webViewLink,mimeType",
                supportsAllDrives=True,
            )
            .execute()
        )
        file_id = meta.get("id")
        if share_writer_email and file_id:
            email = share_writer_email.strip().lower()
            if email and "@" in email:
                try:
                    drive.permissions().create(
                        fileId=file_id,
                        body={
                            "type": "user",
                            "role": "writer",
                            "emailAddress": email,
                        },
                        sendNotificationEmail=False,
                        supportsAllDrives=True,
                        fields="id",
                    ).execute()
                except Exception as perm_exc:
                    logger.warning(
                        "Could not share Drive file %s with %s: %s",
                        file_id,
                        email,
                        perm_exc,
                    )
        return {
            "ok": True,
            "file_id": file_id,
            "name": meta.get("name"),
            "web_view_link": meta.get("webViewLink"),
            "folder_id": folder,
        }
    except Exception as e:
        msg = str(e)
        if "storageQuotaExceeded" in msg or "storage quota" in msg.lower():
            sa_email = service_account_email(app_config) or "the app service account"
            msg = (
                "Service accounts cannot upload to personal My Drive folders. "
                f"Put the client folder on a Shared drive, add {sa_email} as Content manager, "
                f"then retry. Original: {e}"
            )
        logger.exception("Drive suitability upload failed")
        return {"ok": False, "error": msg}


def export_google_doc_as_docx(
    file_id: str,
    *,
    app_config: Optional[dict] = None,
) -> Dict[str, Any]:
    """Export a Google Doc to DOCX bytes."""
    fid = (file_id or "").strip()
    if not fid:
        return {"ok": False, "error": "Missing Drive file id"}
    try:
        drive = _drive_service(app_config)
        data = (
            drive.files()
            .export(fileId=fid, mimeType=DOCX_MIME)
            .execute()
        )
        if isinstance(data, bytes):
            content = data
        else:
            content = bytes(data)
        return {"ok": True, "content": content, "mimetype": DOCX_MIME}
    except Exception as e:
        # Fallback: if still a native docx, download media
        try:
            drive = _drive_service(app_config)
            from googleapiclient.http import MediaIoBaseDownload

            request = drive.files().get_media(fileId=fid, supportsAllDrives=True)
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return {"ok": True, "content": buf.getvalue(), "mimetype": DOCX_MIME}
        except Exception as e2:
            logger.exception("Drive export failed")
            return {"ok": False, "error": f"{e}; fallback: {e2}"}


def list_folder_files(
    folder_id: str,
    *,
    app_config: Optional[dict] = None,
    page_size: int = 100,
) -> Dict[str, Any]:
    """List non-trashed files (not folders) in a Drive folder."""
    folder = normalize_folder_id(folder_id)
    if not folder:
        return {"ok": False, "error": "folder_id required", "files": []}
    try:
        drive = _drive_service(app_config)
        q = (
            f"'{folder}' in parents and trashed = false and "
            f"mimeType != 'application/vnd.google-apps.folder'"
        )
        listed = (
            drive.files()
            .list(
                q=q,
                spaces="drive",
                fields="files(id,name,mimeType,modifiedTime,size,webViewLink)",
                pageSize=max(1, min(int(page_size), 200)),
                orderBy="modifiedTime desc",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files = listed.get("files") or []
        return {"ok": True, "files": files, "folder_id": folder}
    except Exception as e:
        logger.exception("list_folder_files failed")
        return {"ok": False, "error": str(e), "files": []}


def list_folder_children(
    folder_id: str,
    *,
    app_config: Optional[dict] = None,
    page_size: int = 100,
) -> Dict[str, Any]:
    """List non-trashed children (files and folders) directly under folder_id."""
    folder = normalize_folder_id(folder_id)
    if not folder:
        return {"ok": False, "error": "folder_id required", "files": []}
    try:
        drive = _drive_service(app_config)
        q = f"'{folder}' in parents and trashed = false"
        listed = (
            drive.files()
            .list(
                q=q,
                spaces="drive",
                fields="files(id,name,mimeType,modifiedTime,size,webViewLink)",
                pageSize=max(1, min(int(page_size), 200)),
                orderBy="folder,name",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        return {
            "ok": True,
            "files": listed.get("files") or [],
            "folder_id": folder,
        }
    except Exception as e:
        logger.exception("list_folder_children failed")
        return {"ok": False, "error": str(e), "files": []}


def walk_folder_tree(
    folder_id: str,
    *,
    app_config: Optional[dict] = None,
    max_depth: int = 6,
    max_nodes: int = 500,
) -> Dict[str, Any]:
    """
    Recursively list all files under folder_id (all subfolder nodes).

    Each file row includes ``path`` (slash-joined folder names from root)
    and ``parent_id``. Folders themselves are not returned in ``files``.
    """
    root = normalize_folder_id(folder_id)
    if not root:
        return {"ok": False, "error": "folder_id required", "files": [], "folders": []}

    folder_mime = "application/vnd.google-apps.folder"
    files_out: List[Dict[str, Any]] = []
    folders_out: List[Dict[str, Any]] = []
    errors: List[str] = []
    visited: Set[str] = set()
    # (folder_id, depth, path_prefix)
    queue: List[tuple] = [(root, 0, "")]
    nodes_seen = 0

    while queue and nodes_seen < int(max_nodes):
        fid, depth, path_prefix = queue.pop(0)
        if fid in visited:
            continue
        visited.add(fid)
        nodes_seen += 1
        listed = list_folder_children(fid, app_config=app_config)
        if not listed.get("ok"):
            errors.append(listed.get("error") or f"list failed for {fid}")
            continue
        for item in listed.get("files") or []:
            iid = item.get("id")
            name = item.get("name") or ""
            mime = item.get("mimeType") or ""
            if mime == folder_mime:
                child_path = f"{path_prefix}/{name}" if path_prefix else name
                folders_out.append(
                    {
                        "id": iid,
                        "name": name,
                        "path": child_path,
                        "parent_id": fid,
                    }
                )
                if depth < int(max_depth) and iid:
                    queue.append((iid, depth + 1, child_path))
                continue
            file_path = f"{path_prefix}/{name}" if path_prefix else name
            files_out.append(
                {
                    **item,
                    "path": file_path,
                    "parent_id": fid,
                    "folder_path": path_prefix or "",
                }
            )

    return {
        "ok": not errors or bool(files_out or folders_out),
        "files": files_out,
        "folders": folders_out,
        "folder_id": root,
        "nodes_visited": nodes_seen,
        "errors": errors or None,
    }


def find_file_by_name(
    folder_id: str,
    name: str,
    *,
    app_config: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    """Return first non-trashed file with exact name under folder, or None."""
    folder = normalize_folder_id(folder_id)
    fname = (name or "").strip()
    if not folder or not fname:
        return None
    try:
        drive = _drive_service(app_config)
        safe = fname.replace("'", "\\'")
        q = (
            f"name = '{safe}' and '{folder}' in parents and trashed = false"
        )
        listed = (
            drive.files()
            .list(
                q=q,
                spaces="drive",
                fields="files(id,name,mimeType,modifiedTime,size,webViewLink)",
                pageSize=5,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files = listed.get("files") or []
        return files[0] if files else None
    except Exception:
        logger.exception("find_file_by_name failed")
        return None


def download_file_bytes(
    file_id: str,
    *,
    app_config: Optional[dict] = None,
) -> Dict[str, Any]:
    """Download binary file content (not Google Docs export)."""
    fid = (file_id or "").strip()
    if not fid:
        return {"ok": False, "error": "Missing Drive file id"}
    try:
        from googleapiclient.http import MediaIoBaseDownload

        drive = _drive_service(app_config)
        meta = (
            drive.files()
            .get(
                fileId=fid,
                fields="id,name,mimeType,size",
                supportsAllDrives=True,
            )
            .execute()
        )
        mime = meta.get("mimeType") or ""
        if mime.startswith("application/vnd.google-apps."):
            return {
                "ok": False,
                "error": "Google Docs/Sheets cannot be used as holdings statements — upload CSV/XLSX/PDF.",
            }
        request = drive.files().get_media(fileId=fid, supportsAllDrives=True)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return {
            "ok": True,
            "content": buf.getvalue(),
            "name": meta.get("name"),
            "mime_type": mime,
            "file_id": fid,
        }
    except Exception as e:
        logger.exception("download_file_bytes failed")
        return {"ok": False, "error": str(e)}
