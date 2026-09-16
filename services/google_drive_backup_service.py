"""
Upload a local file to Google Drive via service account.

IMPORTANT (Google policy):
  Service accounts have **no My Drive storage quota**. Sharing a personal folder
  with the SA email is NOT enough — uploads fail with storageQuotaExceeded.

  Use a **Shared drive** (Google Workspace Team Drive):
  1. Drive → Shared drives → New (needs Workspace admin / Shared drive create right)
  2. Add the service account `client_email` as **Content manager** (or Contributor)
  3. Optionally create a subfolder inside that Shared drive
  4. Put that folder/drive ID in CODEBASE_BACKUP_DRIVE_FOLDER_ID
  5. .env:
       CODEBASE_BACKUP_DRIVE_FOLDER_ID=<id>
       CODEBASE_BACKUP_DRIVE_ENABLED=true

Requires: google-api-python-client + google-auth.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Full drive scope needed for Shared drives uploads
DRIVE_SCOPE = ("https://www.googleapis.com/auth/drive",)


def _service_account_path(app_config: Optional[dict] = None) -> Path:
    cfg = app_config or {}
    raw = (
        cfg.get("GOOGLE_SERVICE_ACCOUNT_FILE")
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        or ""
    ).strip()
    if raw:
        return Path(raw).expanduser().resolve()
    root = Path(__file__).resolve().parent.parent
    return (root / "service_account.json").resolve()


def drive_upload_enabled(app_config: Optional[dict] = None) -> bool:
    cfg = app_config or {}
    raw = (
        str(cfg.get("CODEBASE_BACKUP_DRIVE_ENABLED") or "")
        or os.environ.get("CODEBASE_BACKUP_DRIVE_ENABLED")
        or ""
    ).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return bool(drive_folder_id(app_config))


def drive_folder_id(app_config: Optional[dict] = None) -> str:
    cfg = app_config or {}
    return (
        str(cfg.get("CODEBASE_BACKUP_DRIVE_FOLDER_ID") or "")
        or os.environ.get("CODEBASE_BACKUP_DRIVE_FOLDER_ID")
        or ""
    ).strip()


def upload_file_to_drive_folder(
    local_path: Path,
    *,
    folder_id: Optional[str] = None,
    app_config: Optional[dict] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upload (or replace same-named file in folder) via Drive API.
    Uses supportsAllDrives for Shared drive targets.
    """
    path = Path(local_path)
    if not path.is_file():
        return {"ok": False, "error": f"File not found: {path}"}

    folder = (folder_id or drive_folder_id(app_config) or "").strip()
    if not folder:
        return {"ok": False, "error": "CODEBASE_BACKUP_DRIVE_FOLDER_ID not set"}

    sa = _service_account_path(app_config)
    if not sa.is_file():
        return {"ok": False, "error": f"service account missing: {sa}"}

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        return {
            "ok": False,
            "error": (
                f"Missing Drive libs ({e}). On VPS: "
                f"/opt/Inertia2026v1/venv/bin/pip install google-api-python-client"
            ),
        }

    try:
        creds = service_account.Credentials.from_service_account_file(
            str(sa), scopes=list(DRIVE_SCOPE)
        )
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        upload_name = name or path.name

        existing_id = None
        safe_name = upload_name.replace("\\", "\\\\").replace("'", "\\'")
        q = f"name = '{safe_name}' and '{folder}' in parents and trashed = false"
        found = (
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
        files = found.get("files") or []
        if files:
            existing_id = files[0]["id"]

        media = MediaFileUpload(str(path), mimetype="application/gzip", resumable=True)
        if existing_id:
            meta = (
                drive.files()
                .update(
                    fileId=existing_id,
                    media_body=media,
                    fields="id,name,webViewLink,size",
                    supportsAllDrives=True,
                )
                .execute()
            )
        else:
            meta = (
                drive.files()
                .create(
                    body={"name": upload_name, "parents": [folder]},
                    media_body=media,
                    fields="id,name,webViewLink,size",
                    supportsAllDrives=True,
                )
                .execute()
            )

        logger.info(
            "Uploaded %s to Drive folder %s as %s",
            path.name,
            folder,
            meta.get("id"),
        )
        return {
            "ok": True,
            "file_id": meta.get("id"),
            "name": meta.get("name"),
            "web_view_link": meta.get("webViewLink"),
            "size": meta.get("size"),
            "folder_id": folder,
            "replaced": bool(existing_id),
        }
    except Exception as e:
        msg = str(e)
        if "storageQuotaExceeded" in msg or "storage quota" in msg.lower():
            msg = (
                "Service accounts cannot upload to personal My Drive folders. "
                "Create a Google Workspace Shared drive, add the SA client_email "
                "as Content manager, set CODEBASE_BACKUP_DRIVE_FOLDER_ID to that "
                f"Shared drive (or a folder inside it). Original: {e}"
            )
        logger.exception("Drive upload failed: %s", e)
        return {"ok": False, "error": msg}
