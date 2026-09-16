"""Lead document attachments (CV, KYC, etc.) stored under uploads/leads/<id>/.

No new DB table — files plus a small manifest JSON. Safe to deploy ahead of
any schema cutover.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from werkzeug.datastructures import FileStorage

from services.secure_upload import save_upload_to_directory

MANIFEST_NAME = "_manifest.json"
LEAD_DOC_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".txt",
    ".rtf",
}

NOTE_DOCUMENT_TYPES = frozenset({"notes", "handwritten notes", "handwritten note"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif"})


def is_image_name(filename: Optional[str]) -> bool:
    return Path(filename or "").suffix.lower() in IMAGE_EXTENSIONS


def is_note_document_type(document_type: Optional[str]) -> bool:
    return (document_type or "").strip().lower() in NOTE_DOCUMENT_TYPES


def _upload_root(upload_root: Optional[str | Path] = None) -> Path:
    if upload_root is not None:
        return Path(upload_root)
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            configured = current_app.config.get("UPLOAD_FOLDER")
            if configured:
                return Path(configured)
    except Exception:
        pass
    return Path("uploads")


def lead_documents_dir(lead_id: int, upload_root: Optional[str | Path] = None) -> Path:
    return _upload_root(upload_root) / "leads" / str(int(lead_id))


def _manifest_path(folder: Path) -> Path:
    return folder / MANIFEST_NAME


def _read_manifest(folder: Path) -> List[Dict[str, Any]]:
    path = _manifest_path(folder)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _write_manifest(folder: Path, entries: List[Dict[str, Any]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    _manifest_path(folder).write_text(
        json.dumps(entries, indent=2, default=str),
        encoding="utf-8",
    )


def list_lead_documents(
    lead_id: int, upload_root: Optional[str | Path] = None
) -> List[Dict[str, Any]]:
    folder = lead_documents_dir(lead_id, upload_root)
    if not folder.is_dir():
        return []
    entries = _read_manifest(folder)
    known = {str(item.get("stored_name") or "") for item in entries}
    out: List[Dict[str, Any]] = []
    for item in entries:
        stored = str(item.get("stored_name") or "")
        if not stored or stored == MANIFEST_NAME:
            continue
        path = folder / stored
        if path.is_file():
            row = dict(item)
            row["exists"] = True
            row["is_image"] = is_image_name(stored)
            row["is_note"] = is_note_document_type(row.get("document_type"))
            out.append(row)
    for child in sorted(folder.iterdir()):
        if not child.is_file() or child.name in known or child.name == MANIFEST_NAME:
            continue
        out.append(
            {
                "stored_name": child.name,
                "original_name": child.name,
                "document_type": "Other",
                "uploaded_at": datetime.fromtimestamp(
                    child.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
                "source": "file",
                "exists": True,
                "is_image": is_image_name(child.name),
                "is_note": False,
            }
        )
    out.sort(key=lambda row: str(row.get("uploaded_at") or ""), reverse=True)
    return out


def save_lead_document(
    lead_id: int,
    file: FileStorage,
    *,
    document_type: str = "Other",
    source: str = "ui",
    upload_root: Optional[str | Path] = None,
) -> Dict[str, Any]:
    folder = lead_documents_dir(lead_id, upload_root)
    abs_path, _rel = save_upload_to_directory(
        file,
        folder,
        allowed_extensions=LEAD_DOC_EXTENSIONS,
        prefix="",
    )
    stored_name = Path(abs_path).name
    original = os.path.basename(file.filename or stored_name)
    entry = {
        "stored_name": stored_name,
        "original_name": original,
        "document_type": (document_type or "Other").strip() or "Other",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }
    entries = _read_manifest(folder)
    entries.append(entry)
    _write_manifest(folder, entries)
    return entry


def resolve_lead_document_path(
    lead_id: int,
    stored_name: str,
    upload_root: Optional[str | Path] = None,
) -> Optional[Path]:
    """Absolute path if stored_name is a real file in this lead's folder."""
    name = (stored_name or "").replace("\\", "/").strip()
    if not name or name == MANIFEST_NAME or "/" in name or name.startswith("."):
        return None
    if ".." in name.split("/"):
        return None
    folder = lead_documents_dir(lead_id, upload_root).resolve()
    candidate = (folder / name).resolve()
    try:
        candidate.relative_to(folder)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate
