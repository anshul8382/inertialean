"""Sync company uploads and research notes to a Google Shared Drive folder.

Requires one-time auth (`iis drive auth`). Failures never block local ingest/note gen.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
from datetime import date
from pathlib import Path
from typing import Any

from iis.config import settings

logger = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
# Full Drive scope is required to write into an existing Shared Drive the app did not create.
SCOPES = ["https://www.googleapis.com/auth/drive"]


def company_folder_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:180] or "Unknown Company"


def _safe_company_folder_name(name: str) -> str:
    return company_folder_name(name)


def _index_path() -> Path:
    path = settings.google_drive_index_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_folder_index() -> dict[str, Any]:
    path = _index_path()
    if not path.exists():
        return {"companies": {}, "paths": {}}
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"companies": {}, "paths": {}}
    # Back-compat: old format was { "Company Name": "folderId" }
    if raw and "companies" not in raw and "paths" not in raw:
        return {"companies": {k: v for k, v in raw.items() if isinstance(v, str)}, "paths": {}}
    return {
        "companies": dict(raw.get("companies") or {}),
        "paths": dict(raw.get("paths") or {}),
    }


def _save_folder_index(index: dict[str, Any]) -> None:
    payload = {
        "companies": index.get("companies") or {},
        "paths": index.get("paths") or {},
    }
    _index_path().write_text(json.dumps(payload, indent=2))


def _company_alias_key(name: str) -> str:
    """Normalize Ltd/Limited/punctuation so we reuse one Drive folder."""
    n = company_folder_name(name).lower()
    n = re.sub(r"\b(limited|ltd|llp|pvt|private)\b\.?", "", n)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def drive_configured() -> bool:
    """True only when we can actually talk to Drive (SA or OAuth token present)."""
    if not settings.google_drive_enabled:
        return False
    if not settings.google_drive_folder_id:
        return False
    sa = settings.google_drive_service_account_path
    if sa and sa.exists():
        return True
    creds = settings.google_drive_credentials_path
    token = settings.google_drive_token_path
    # credentials.json alone is not enough — OAuth token required
    return creds.exists() and token.exists()


def drive_auth_needed() -> bool:
    """Enabled + folder set, but no usable token/SA yet."""
    if not settings.google_drive_enabled or not settings.google_drive_folder_id:
        return False
    return not drive_configured()


def drive_status_dict() -> dict[str, Any]:
    configured = drive_configured()
    reachable = False
    folder_name = None
    error = None
    if configured:
        try:
            svc = get_drive_service()
            if svc:
                fid = settings.google_drive_folder_id
                try:
                    meta = svc.files().get(
                        fileId=fid, fields="id, name", supportsAllDrives=True
                    ).execute()
                    reachable = True
                    folder_name = meta.get("name")
                except Exception:
                    try:
                        drive = svc.drives().get(driveId=fid, fields="id, name").execute()
                        reachable = True
                        folder_name = drive.get("name")
                    except Exception as e:
                        error = str(e)
            else:
                error = "Could not build Drive client"
        except Exception as e:
            error = str(e)
    return {
        "enabled": settings.google_drive_enabled,
        "folder_id": settings.google_drive_folder_id,
        "credentials_ok": settings.google_drive_credentials_path.exists(),
        "token_ok": settings.google_drive_token_path.exists(),
        "configured": configured,
        "auth_needed": drive_auth_needed(),
        "reachable": reachable,
        "folder_name": folder_name,
        "error": error,
        "hint": (
            "Run in terminal: iis drive auth"
            if drive_auth_needed()
            else ("Drive OK" if reachable else (error or "Check iis drive status"))
        ),
    }


def _build_credentials() -> Any | None:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google.oauth2 import service_account
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        logger.warning("Google Drive libs missing — pip install google-api-python-client google-auth-oauthlib")
        return None

    sa_path = settings.google_drive_service_account_path
    if sa_path and sa_path.exists():
        return service_account.Credentials.from_service_account_file(
            str(sa_path),
            scopes=SCOPES,
        )

    creds_path = settings.google_drive_credentials_path
    token_path = settings.google_drive_token_path
    if not creds_path.exists():
        logger.warning("Google Drive credentials not found at %s — run: iis drive auth", creds_path)
        return None

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
        return creds

    if creds and creds.valid:
        return creds

    # Do not start a browser mid-upload — user must run `iis drive auth` once.
    logger.warning("Google Drive token missing/expired — run: iis drive auth")
    return None


def get_drive_service() -> Any | None:
    if not settings.google_drive_enabled or not settings.google_drive_folder_id:
        return None
    try:
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning("google-api-python-client not installed")
        return None

    creds = _build_credentials()
    if not creds:
        return None
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def run_oauth_setup() -> Path:
    """Force interactive OAuth and save token. Returns token path."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as e:
        raise RuntimeError(
            "Install Google Drive deps: pip install google-api-python-client google-auth-oauthlib"
        ) from e

    creds_path = settings.google_drive_credentials_path
    if not creds_path.exists():
        raise FileNotFoundError(
            f"OAuth client JSON missing: {creds_path}\n"
            "Create a Desktop OAuth client in Google Cloud Console, download JSON, and save it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path = settings.google_drive_token_path
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    return token_path


def _list_children(service: Any, parent_id: str) -> list[dict]:
    files: list[dict] = []
    page_token = None
    while True:
        res = (
            service.files()
            .list(
                q=f"'{parent_id}' in parents and trashed = false",
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, createdTime)",
                pageSize=100,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora="allDrives",
            )
            .execute()
        )
        files.extend(res.get("files") or [])
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return files


def _matches_named(children: list[dict], name: str, *, folders: bool | None = None) -> list[dict]:
    out = [c for c in children if c.get("name") == name]
    if folders is True:
        out = [c for c in out if c.get("mimeType") == FOLDER_MIME]
    elif folders is False:
        out = [c for c in out if c.get("mimeType") != FOLDER_MIME]
    # Prefer oldest so concurrent creates still converge on one id
    out.sort(key=lambda c: c.get("createdTime") or "")
    return out


def _find_child_folder(service: Any, parent_id: str, name: str) -> str | None:
    """Return existing folder id by exact name; never invent a new one here."""
    matches = _matches_named(_list_children(service, parent_id), name, folders=True)
    if not matches:
        return None
    if len(matches) > 1:
        logger.warning(
            "Drive has %s folders named %r under %s — reusing oldest %s",
            len(matches),
            name,
            parent_id,
            matches[0]["id"],
        )
    return matches[0]["id"]


def _folder_still_exists(service: Any, folder_id: str) -> bool:
    try:
        meta = (
            service.files()
            .get(fileId=folder_id, fields="id, trashed", supportsAllDrives=True)
            .execute()
        )
        return bool(meta.get("id")) and not meta.get("trashed")
    except Exception:
        return False


def _reuse_or_create_folder(service: Any, parent_id: str, name: str) -> str | None:
    """Idempotent folder ensure: list → reuse if present → create only if missing."""
    existing = _find_child_folder(service, parent_id, name)
    if existing:
        return existing
    created = (
        service.files()
        .create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
            fields="id, name",
            supportsAllDrives=True,
        )
        .execute()
    )
    # Re-list in case another process created the same name concurrently
    again = _find_child_folder(service, parent_id, name)
    if again and again != created.get("id"):
        logger.info("Concurrent folder create for %r — using existing %s", name, again)
        return again
    return created.get("id")


def find_company_folder(company_name: str, service: Any | None = None) -> str | None:
    """Resolve company folder on Shared Drive without creating anything."""
    folder_name = _safe_company_folder_name(company_name)
    alias = _company_alias_key(company_name)
    index = _load_folder_index()
    companies = index.get("companies") or {}

    service = service or get_drive_service()
    if not service:
        return None

    cached = companies.get(folder_name) or companies.get(alias)
    if cached and _folder_still_exists(service, cached):
        return cached

    parent = settings.google_drive_folder_id
    children = _list_children(service, parent)
    # Exact name first
    exact = _matches_named(children, folder_name, folders=True)
    if exact:
        fid = exact[0]["id"]
        companies[folder_name] = fid
        companies[alias] = fid
        index["companies"] = companies
        _save_folder_index(index)
        return fid
    # Alias match (Ltd vs Limited, etc.)
    for child in children:
        if child.get("mimeType") != FOLDER_MIME:
            continue
        if _company_alias_key(child.get("name") or "") == alias:
            fid = child["id"]
            companies[folder_name] = fid
            companies[alias] = fid
            companies[child["name"]] = fid
            index["companies"] = companies
            _save_folder_index(index)
            logger.info("Reusing Drive folder %r for %r", child.get("name"), folder_name)
            return fid
    return None


def ensure_company_folder(company_name: str, service: Any | None = None) -> str | None:
    """Resolve or create a single company folder under the shared Drive root."""
    folder_name = _safe_company_folder_name(company_name)
    service = service or get_drive_service()
    if not service:
        return None

    existing = find_company_folder(company_name, service=service)
    if existing:
        return existing

    parent = settings.google_drive_folder_id
    folder_id = _reuse_or_create_folder(service, parent, folder_name)
    if not folder_id:
        return None
    index = _load_folder_index()
    companies = index.get("companies") or {}
    companies[folder_name] = folder_id
    companies[_company_alias_key(company_name)] = folder_id
    index["companies"] = companies
    _save_folder_index(index)
    logger.info("Created Drive folder %s (%s)", folder_name, folder_id)
    return folder_id


def _find_file_in_folder(service: Any, parent_id: str, name: str) -> str | None:
    matches = _matches_named(_list_children(service, parent_id), name, folders=False)
    if not matches:
        return None
    if len(matches) > 1:
        logger.warning(
            "Drive has %s files named %r under %s — using %s",
            len(matches),
            name,
            parent_id,
            matches[0]["id"],
        )
    return matches[0]["id"]


def _ensure_nested_folder(service: Any, parent_id: str, relative: str) -> str | None:
    """Create capital_iq/financials style path under parent; reuse if already present."""
    index = _load_folder_index()
    paths = index.get("paths") or {}
    cache_key = f"{parent_id}::{relative.replace(chr(92), '/').strip('/')}"
    cached = paths.get(cache_key)
    if cached and _folder_still_exists(service, cached):
        return cached

    current = parent_id
    built: list[str] = []
    for part in relative.replace("\\", "/").split("/"):
        part = part.strip()
        if not part:
            continue
        built.append(part)
        step_key = f"{parent_id}::{'/'.join(built)}"
        step_cached = paths.get(step_key)
        if step_cached and _folder_still_exists(service, step_cached):
            current = step_cached
            continue
        current = _reuse_or_create_folder(service, current, part) or current
        paths[step_key] = current

    paths[cache_key] = current
    index["paths"] = paths
    _save_folder_index(index)
    return current


def ensure_telegram_inbox_folder(service: Any | None = None) -> str | None:
    """Reuse or create the Shared Drive inbox folder for Telegram PDFs."""
    if not settings.google_drive_enabled or not settings.google_drive_folder_id:
        return None
    service = service or get_drive_service()
    if not service:
        return None
    name = (settings.telegram_drive_inbox_name or "Telegram PDFs").strip() or "Telegram PDFs"
    try:
        return _reuse_or_create_folder(service, settings.google_drive_folder_id, name)
    except Exception:
        logger.exception("Failed to ensure Telegram inbox folder %r", name)
        return None


def upload_file_to_folder(
    local_path: Path | str,
    folder_id: str,
    *,
    remote_name: str | None = None,
    skip_if_exists: bool = True,
) -> str | None:
    """Upload a local file into an existing Drive folder (Shared Drive–safe)."""
    if not settings.google_drive_enabled:
        return None
    path = Path(local_path)
    if not path.exists() or not path.is_file():
        return None
    if not folder_id:
        return None
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return None
    try:
        service = get_drive_service()
        if not service:
            return None
        name = remote_name or path.name
        if skip_if_exists:
            existing = _find_file_in_folder(service, folder_id, name)
            if existing:
                logger.debug("Drive file exists in folder %s, skip: %s", folder_id, name)
                return existing
        mime, _ = mimetypes.guess_type(name)
        media = MediaFileUpload(str(path), mimetype=mime or "application/octet-stream", resumable=True)
        meta = {"name": name, "parents": [folder_id]}
        created = (
            service.files()
            .create(body=meta, media_body=media, fields="id, name", supportsAllDrives=True)
            .execute()
        )
        again = _find_file_in_folder(service, folder_id, name)
        logger.info("Uploaded to Drive folder %s: %s", folder_id, name)
        return again or created.get("id")
    except Exception:
        logger.exception("Google Drive upload_file_to_folder failed for %s", path)
        return None


def upload_local_file(
    local_path: Path | str,
    company_name: str,
    *,
    remote_name: str | None = None,
    skip_if_exists: bool = True,
    doc_kind: str | None = None,
    relative_folder: str | None = None,
) -> str | None:
    """Upload a file into the company's Drive folder (typed subfolder when known)."""
    if not settings.google_drive_enabled:
        return None

    path = Path(local_path)
    if not path.exists() or not path.is_file():
        return None

    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return None

    try:
        from iis.library.layout import folder_for_kind

        service = get_drive_service()
        if not service:
            return None

        folder_id = ensure_company_folder(company_name, service=service)
        if not folder_id:
            return None

        rel = relative_folder or (folder_for_kind(doc_kind) if doc_kind else None)
        if rel and rel != "files":
            nested = _ensure_nested_folder(service, folder_id, rel)
            if nested:
                folder_id = nested

        name = remote_name or path.name
        if skip_if_exists:
            existing = _find_file_in_folder(service, folder_id, name)
            if existing:
                logger.debug("Drive file exists, skip upload: %s / %s", rel or "", name)
                return existing

        # Final race check right before create
        existing = _find_file_in_folder(service, folder_id, name)
        if existing and skip_if_exists:
            return existing

        mime, _ = mimetypes.guess_type(name)
        media = MediaFileUpload(str(path), mimetype=mime or "application/octet-stream", resumable=True)
        if existing:
            service.files().update(fileId=existing, media_body=media, supportsAllDrives=True).execute()
            logger.info("Updated Drive file: %s / %s / %s", _safe_company_folder_name(company_name), rel or "", name)
            return existing
        meta = {"name": name, "parents": [folder_id]}
        created = (
            service.files()
            .create(body=meta, media_body=media, fields="id, name", supportsAllDrives=True)
            .execute()
        )
        # If duplicates appeared, keep the oldest and treat as success
        again = _find_file_in_folder(service, folder_id, name)
        logger.info("Uploaded to Drive: %s / %s / %s", _safe_company_folder_name(company_name), rel or "", name)
        return again or created.get("id")
    except Exception:
        logger.exception("Google Drive upload failed for %s", path)
        return None


def upload_text_as_file(
    content: str,
    company_name: str,
    remote_name: str,
    *,
    mime_type: str = "text/markdown",
) -> str | None:
    """Write content to a temp file under ~/.iis and upload to the company folder."""
    if not settings.google_drive_enabled:
        return None

    safe_co = _safe_company_folder_name(company_name)
    staging = settings.data_dir / "drive_staging" / safe_co
    staging.mkdir(parents=True, exist_ok=True)
    local = staging / remote_name
    local.write_text(content, encoding="utf-8")
    return upload_local_file(local, company_name, remote_name=remote_name, skip_if_exists=False)


def sync_upload_for_company(
    company_name: str | None,
    local_path: Path | str,
    *,
    doc_kind: str | None = None,
) -> str | None:
    """Best-effort sync of an archived upload. Never raises."""
    if not company_name:
        return None
    try:
        return upload_local_file(local_path, company_name, doc_kind=doc_kind)
    except Exception:
        logger.exception("Drive sync skipped for upload %s", local_path)
        return None


def push_company_library_to_drive(company_key: str | None = None, company_name: str | None = None) -> dict[str, Any]:
    """
    Upload all archived documents for a company (or every company) to Shared Drive.
    Use after `iis drive auth` when files were ingested while Drive was unauthenticated.
    """
    from iis.library.store import CompanyLibraryStore

    if not drive_configured():
        return {
            "ok": False,
            "uploaded": 0,
            "skipped": 0,
            "failed": 0,
            "error": "Drive not authenticated — run: iis drive auth",
            "companies": [],
        }

    store = CompanyLibraryStore()
    if company_key:
        keys = [company_key]
    elif company_name:
        slug_hint = re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-")
        keys = []
        for k in store.list_companies():
            lib = store.load(k)
            n = (lib.company.name if lib.company else "") or ""
            if n.lower() == company_name.lower() or slug_hint in k or company_name.lower() in n.lower():
                keys.append(k)
        if not keys:
            keys = store.list_companies()
    else:
        keys = store.list_companies()
    total_up = total_skip = total_fail = 0
    details: list[dict] = []

    for key in keys:
        if not key:
            continue
        lib = store.load(key)
        name = company_name or (lib.company.name if lib.company else None) or key
        up = skip = fail = 0
        for doc in lib.documents:
            path = Path(doc.stored_path) if doc.stored_path else None
            if not path or not path.exists():
                fail += 1
                continue
            try:
                from iis.library.layout import folder_for_kind

                service = get_drive_service()
                if not service:
                    fail += 1
                    continue
                company_id = ensure_company_folder(name, service=service)
                if not company_id:
                    fail += 1
                    continue
                dest_folder = company_id
                rel = folder_for_kind(doc.doc_kind) if doc.doc_kind else None
                if rel and rel != "files":
                    nested = _ensure_nested_folder(service, company_id, rel)
                    if nested:
                        dest_folder = nested
                existing = _find_file_in_folder(service, dest_folder, path.name)
                if existing:
                    skip += 1
                    continue
                fid = upload_local_file(path, name, doc_kind=doc.doc_kind, skip_if_exists=True)
                if fid:
                    # Distinguishes brand-new vs already-there when race lost
                    if _find_file_in_folder(service, dest_folder, path.name):
                        up += 1
                    else:
                        up += 1
                else:
                    fail += 1
            except Exception:
                logger.exception("Push failed for %s", path)
                fail += 1
        total_up += up
        total_skip += skip
        total_fail += fail
        details.append({
            "company_key": key,
            "company": name,
            "uploaded": up,
            "skipped": skip,
            "failed": fail,
        })
        logger.info("Drive push %s: %s uploaded, %s already present, %s failed", name, up, skip, fail)

    return {
        "ok": total_fail == 0 and (total_up > 0 or total_skip > 0),
        "uploaded": total_up,
        "skipped": total_skip,
        "failed": total_fail,
        "error": None if drive_configured() else "not configured",
        "companies": details,
    }


def pull_company_from_drive(company_name: str, local_root: Path) -> int:
    """
    Download files from the company's Drive folder into the local fixed layout.
    Skips any filename that already exists locally. Does not create a Drive
    company folder if none exists yet.
    Returns number of newly downloaded files.
    """
    if not settings.google_drive_enabled:
        return 0
    service = get_drive_service()
    if not service:
        return 0

    from iis.library.layout import SECTION_FOLDERS

    root = Path(local_root)
    root.mkdir(parents=True, exist_ok=True)
    for rel in SECTION_FOLDERS:
        (root / rel).mkdir(parents=True, exist_ok=True)

    # Find only — never create empty company folders just to pull
    company_id = find_company_folder(company_name, service=service)
    if not company_id:
        return 0

    downloaded = 0
    seen_names: set[str] = set()  # dedupe across duplicate Drive folders

    def walk(folder_id: str, rel_prefix: str = "") -> None:
        nonlocal downloaded
        # Group children by name so duplicate folders/files are only processed once
        by_name: dict[str, list[dict]] = {}
        for item in _list_children(service, folder_id):
            by_name.setdefault(item["name"], []).append(item)

        for name, items in by_name.items():
            folders = [i for i in items if i.get("mimeType") == FOLDER_MIME]
            files = [i for i in items if i.get("mimeType") != FOLDER_MIME]
            # Walk every duplicate folder (content may be split) but prefer oldest first
            folders.sort(key=lambda c: c.get("createdTime") or "")
            for folder in folders:
                child_rel = f"{rel_prefix}/{name}".strip("/") if rel_prefix else name
                walk(folder["id"], child_rel)

            if not files:
                continue
            files.sort(key=lambda c: c.get("createdTime") or "")
            item = files[0]  # one download per filename
            dedupe_key = f"{rel_prefix}::{name}"
            if dedupe_key in seen_names:
                continue
            seen_names.add(dedupe_key)

            dest_rel = rel_prefix.replace("\\", "/") if rel_prefix else ""
            matched = None
            for folder in SECTION_FOLDERS:
                if dest_rel == folder or dest_rel.startswith(folder + "/"):
                    matched = folder
                    break
            if matched:
                dest_dir = root / matched
            elif dest_rel:
                dest_dir = root / dest_rel
            else:
                dest_dir = root / "files"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / name
            if dest.exists():
                continue
            # Also skip if same filename already present anywhere under company root
            if any(p.name == name for p in root.rglob(name) if p.is_file()):
                continue
            try:
                from googleapiclient.http import MediaIoBaseDownload
                import io

                request = service.files().get_media(fileId=item["id"], supportsAllDrives=True)
                buf = io.BytesIO()
                downloader = MediaIoBaseDownload(buf, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                dest.write_bytes(buf.getvalue())
                downloaded += 1
                logger.info("Downloaded from Drive: %s → %s", name, dest)
            except Exception:
                logger.exception("Failed to download %s", name)

    walk(company_id)
    return downloaded


def sync_research_note(company_name: str | None, note: str, *, on_date: date | None = None) -> str | None:
    """Save research note as `{Company}_Research_Note_YYYY-MM-DD.md` on Drive under reports/."""
    if not company_name or not note:
        return None
    try:
        d = (on_date or date.today()).isoformat()
        safe = _safe_company_folder_name(company_name).replace(" ", "_")
        remote = f"{safe}_Research_Note_{d}.md"
        # Stage locally then upload into reports/
        safe_co = _safe_company_folder_name(company_name)
        staging = settings.data_dir / "drive_staging" / safe_co
        staging.mkdir(parents=True, exist_ok=True)
        local = staging / remote
        local.write_text(note, encoding="utf-8")
        return upload_local_file(
            local,
            company_name,
            remote_name=remote,
            skip_if_exists=False,
            doc_kind="research_note",
        )
    except Exception:
        logger.exception("Drive sync skipped for research note (%s)", company_name)
        return None
