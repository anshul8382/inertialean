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
from typing import Any, Dict, Optional

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
