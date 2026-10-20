"""
Per-client sent-email archive: IMAP Sent + EmailLog → local var/ + optional Drive Emails/.

Incremental: skip known message_ids; fetch since last_sent_at - 1 day.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

ARCHIVE_SUBDIR = "client_email_archive"
DRIVE_EMAILS_FOLDER = "Emails"
# First sync: 90 days (was 365 — full Sent download hung for hours). Incremental uses watermark.
DEFAULT_LOOKBACK_DAYS = 90


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def archive_root(client_id: int) -> Path:
    root = _repo_root() / "var" / ARCHIVE_SUBDIR / f"client_{int(client_id)}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "messages").mkdir(exist_ok=True)
    return root


def index_path(client_id: int) -> Path:
    return archive_root(client_id) / "index.json"


def load_index(client_id: int) -> Dict[str, Any]:
    path = index_path(client_id)
    if not path.is_file():
        return _empty_index(client_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_index(client_id)
        data.setdefault("client_id", int(client_id))
        data.setdefault("messages", [])
        data.setdefault("message_ids", [])
        return data
    except Exception as exc:
        logger.warning("Failed reading email archive index for %s: %s", client_id, exc)
        return _empty_index(client_id)


def save_index(client_id: int, index: Dict[str, Any]) -> None:
    path = index_path(client_id)
    index = dict(index)
    index["client_id"] = int(client_id)
    index["updated_at"] = datetime.utcnow().isoformat() + "Z"
    # Keep message_ids in sync
    ids = []
    for m in index.get("messages") or []:
        mid = m.get("message_id")
        if mid and mid not in ids:
            ids.append(mid)
    index["message_ids"] = ids
    # Watermark = max sent_at
    dates = []
    for m in index.get("messages") or []:
        d = _as_date(m.get("sent_at"))
        if d:
            dates.append(d)
    index["last_sent_at"] = max(dates).isoformat() if dates else index.get("last_sent_at")
    path.write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")


def _empty_index(client_id: int) -> Dict[str, Any]:
    return {
        "client_id": int(client_id),
        "messages": [],
        "message_ids": [],
        "last_sent_at": None,
        "last_synced_at": None,
        "client_emails": [],
        "drive_emails_folder_id": None,
    }


def _as_date(val) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except Exception:
        return None


def client_email_addresses(client_id: int) -> List[str]:
    from models import Client

    c = Client.query.get(int(client_id))
    if not c:
        return []
    out: List[str] = []
    primary = (c.email or "").strip().lower()
    if primary:
        out.append(primary)
    for part in (c.secondary_emails or "").split(","):
        e = part.strip().lower()
        if e and e not in out:
            out.append(e)
    return out


def _safe_file_id(message_id: str) -> str:
    raw = (message_id or "").strip()
    if not raw:
        raw = "unknown"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", raw)[:40].strip("_")
    return f"{slug}_{digest}" if slug else digest


def known_message_ids(index: Dict[str, Any]) -> Set[str]:
    ids = set(index.get("message_ids") or [])
    for m in index.get("messages") or []:
        mid = m.get("message_id")
        if mid:
            ids.add(mid)
        eid = m.get("email_log_id")
        if eid is not None:
            ids.add(f"emaillog:{int(eid)}")
    return ids


def _write_local_body(
    client_id: int,
    *,
    message_id: str,
    html: Optional[str],
    text: Optional[str],
    raw_rfc822: Optional[bytes],
) -> Dict[str, str]:
    msgs = archive_root(client_id) / "messages"
    safe = _safe_file_id(message_id)
    paths: Dict[str, str] = {}
    if html:
        p = msgs / f"{safe}.html"
        p.write_text(html, encoding="utf-8")
        paths["html_path"] = str(p.relative_to(_repo_root()))
    elif text:
        p = msgs / f"{safe}.txt"
        p.write_text(text, encoding="utf-8")
        paths["text_path"] = str(p.relative_to(_repo_root()))
    if raw_rfc822:
        p = msgs / f"{safe}.eml"
        p.write_bytes(raw_rfc822)
        paths["eml_path"] = str(p.relative_to(_repo_root()))
    return paths


def _ensure_drive_emails_folder(client_id: int, index: Dict[str, Any]) -> Optional[str]:
    from models import Client
    from services.client_google_drive_service import ensure_subfolder

    c = Client.query.get(int(client_id))
    folder = (getattr(c, "google_drive_folder_id", None) or "").strip() if c else ""
    if not folder:
        return None
    existing = index.get("drive_emails_folder_id")
    if existing:
        return existing
    res = ensure_subfolder(folder, DRIVE_EMAILS_FOLDER)
    if not res.get("ok"):
        logger.warning(
            "Could not ensure Drive Emails folder for client %s: %s",
            client_id,
            res.get("error"),
        )
        return None
    index["drive_emails_folder_id"] = res.get("folder_id")
    return index["drive_emails_folder_id"]


def _upload_to_drive(
    *,
    drive_folder_id: str,
    local_rel: Optional[str],
    display_name: str,
    mime: str,
) -> Optional[str]:
    if not local_rel:
        return None
    from services.client_google_drive_service import upload_bytes

    abs_path = _repo_root() / local_rel
    if not abs_path.is_file():
        return None
    data = abs_path.read_bytes()
    res = upload_bytes(drive_folder_id, display_name, data, mime_type=mime)
    if not res.get("ok"):
        logger.warning("Drive email upload failed: %s", res.get("error"))
        return None
    return res.get("file_id")


def merge_imap_into_index(
    client_id: int,
    index: Dict[str, Any],
    imap_rows: List[Dict[str, Any]],
    *,
    upload_drive: bool = True,
) -> int:
    """Pure-ish merge of IMAP rows into index; returns count added."""
    known = known_message_ids(index)
    drive_folder = (
        _ensure_drive_emails_folder(client_id, index) if upload_drive else None
    )
    added = 0
    messages = list(index.get("messages") or [])
    for row in imap_rows:
        mid = (row.get("gmail_message_id") or "").strip()
        if not mid or mid in known:
            continue
        paths = _write_local_body(
            client_id,
            message_id=mid,
            html=row.get("html"),
            text=row.get("text"),
            raw_rfc822=row.get("raw_rfc822"),
        )
        sent = row.get("sent_at") or (
            row["advice_date"].isoformat()
            if hasattr(row.get("advice_date"), "isoformat")
            else row.get("advice_date")
        )
        entry = {
            "message_id": mid,
            "sent_at": str(sent)[:10] if sent else None,
            "subject": (row.get("subject") or "")[:500],
            "recipient": (row.get("to_raw") or row.get("to_email") or "")[:300],
            "recipient_emails": row.get("recipient_emails") or [],
            "email_type": "imap_sent",
            "source": "imap_sent",
            "status": "sent",
            **paths,
            "drive_file_id": None,
        }
        if drive_folder:
            fname = f"{entry['sent_at'] or 'unknown'}_{_safe_file_id(mid)[:24]}.html"
            if paths.get("html_path"):
                entry["drive_file_id"] = _upload_to_drive(
                    drive_folder_id=drive_folder,
                    local_rel=paths.get("html_path"),
                    display_name=fname,
                    mime="text/html",
                )
            elif paths.get("eml_path"):
                entry["drive_file_id"] = _upload_to_drive(
                    drive_folder_id=drive_folder,
                    local_rel=paths.get("eml_path"),
                    display_name=fname.replace(".html", ".eml"),
                    mime="message/rfc822",
                )
        messages.append(entry)
        known.add(mid)
        added += 1
    messages.sort(key=lambda m: m.get("sent_at") or "", reverse=True)
    index["messages"] = messages
    return added


def merge_emaillog_into_index(
    client_id: int,
    index: Dict[str, Any],
    *,
    since: date,
    before: date,
    upload_drive: bool = True,
) -> int:
    """Fill gaps from EmailLog not already archived."""
    from models import EmailLog
    from sqlalchemy import or_

    addresses = client_email_addresses(client_id)
    start = datetime.combine(since, datetime.min.time())
    end = datetime.combine(before, datetime.min.time())
    clauses = [EmailLog.client_id == int(client_id)]
    for addr in addresses:
        clauses.append(EmailLog.recipient_email.ilike(f"%{addr}%"))

    rows = (
        EmailLog.query.filter(
            or_(*clauses),
            EmailLog.sent_at >= start,
            EmailLog.sent_at < end,
        )
        .order_by(EmailLog.sent_at.desc())
        .limit(500)
        .all()
    )
    known = known_message_ids(index)
    drive_folder = (
        _ensure_drive_emails_folder(client_id, index) if upload_drive else None
    )
    added = 0
    messages = list(index.get("messages") or [])
    for e in rows:
        mid = f"emaillog:{int(e.id)}"
        if mid in known:
            continue
        # Also skip if subject+date already from IMAP (loose)
        sent_d = _as_date(e.sent_at)
        paths = _write_local_body(
            client_id,
            message_id=mid,
            html=e.email_html,
            text=e.custom_message,
            raw_rfc822=None,
        )
        entry = {
            "message_id": mid,
            "email_log_id": e.id,
            "sent_at": sent_d.isoformat() if sent_d else None,
            "subject": (e.subject or "")[:500],
            "recipient": (e.recipient_email or "")[:300],
            "recipient_emails": [],
            "email_type": e.email_type,
            "source": "email_log",
            "status": e.status,
            **paths,
            "drive_file_id": None,
        }
        if drive_folder and paths.get("html_path"):
            fname = f"{entry['sent_at'] or 'unknown'}_elog_{e.id}.html"
            entry["drive_file_id"] = _upload_to_drive(
                drive_folder_id=drive_folder,
                local_rel=paths.get("html_path"),
                display_name=fname,
                mime="text/html",
            )
        messages.append(entry)
        known.add(mid)
        added += 1
    messages.sort(key=lambda m: m.get("sent_at") or "", reverse=True)
    index["messages"] = messages
    return added


def sync_window(
    index: Dict[str, Any],
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    today: Optional[date] = None,
) -> tuple:
    """Return (since, before) dates for incremental pull."""
    today = today or date.today()
    before = today + timedelta(days=1)  # exclusive upper for IMAP BEFORE
    last = _as_date(index.get("last_sent_at"))
    if last:
        since = last - timedelta(days=1)  # overlap
    else:
        since = today - timedelta(days=int(lookback_days))
    return since, before


def _drive_link_status(client_id: int) -> Dict[str, Any]:
    """Whether the client has a Google Drive folder linked for Emails upload."""
    from models import Client
    from services.client_google_drive_service import (
        client_drive_columns_ready,
        folder_url,
    )

    ready = client_drive_columns_ready()
    c = Client.query.get(int(client_id))
    folder_id = (
        (getattr(c, "google_drive_folder_id", None) or "").strip()
        if c and ready
        else ""
    )
    linked = bool(folder_id)
    return {
        "drive_linked": linked,
        "drive_folder_id": folder_id or None,
        "drive_folder_url": folder_url(folder_id) if folder_id else "",
        "drive_columns_ready": ready,
        "needs_drive_details": not linked,
        "drive_flag": (
            None
            if linked
            else (
                "Client Google Drive folder is not linked. "
                "Add the folder ID on Client detail → Google Drive so archived "
                "emails can be stored under Emails/ in Drive."
            )
        ),
    }


def _looks_like_email_file(name: str, mime: str = "") -> bool:
    """Heuristic: HTML/EML (and similar) under any client Drive node."""
    n = (name or "").strip().lower()
    m = (mime or "").strip().lower()
    if m in ("text/html", "message/rfc822") or m.startswith("text/html"):
        return True
    if n.endswith((".html", ".htm", ".eml", ".mhtml")):
        return True
    # Common archive upload names (even if extension odd)
    if any(tok in n for tok in ("elog_", "emaillog_", "portfolio investment recos")):
        return True
    return False


def pull_emails_from_drive_tree(client_id: int) -> Dict[str, Any]:
    """
    Walk the whole client Drive folder tree (all nodes, not only Emails/)
    and import any email-like files missing from the local archive.

    New uploads still go under Emails/; this only *reads* everywhere so
    investigation can find data already stored in other subfolders.
    """
    drive_status = _drive_link_status(client_id)
    if not drive_status.get("drive_linked"):
        return {
            "ok": False,
            "imported": 0,
            "scanned": 0,
            "reason": "drive_not_linked",
            **drive_status,
        }

    from services.client_google_drive_service import (
        download_file_bytes,
        walk_folder_tree,
    )

    root_id = drive_status.get("drive_folder_id")
    walked = walk_folder_tree(root_id, max_depth=6, max_nodes=400)
    if not walked.get("ok") and not walked.get("files"):
        return {
            "ok": False,
            "imported": 0,
            "scanned": 0,
            "reason": walked.get("error") or "walk_failed",
            "errors": walked.get("errors"),
            **drive_status,
        }

    index = load_index(client_id)
    known_drive: Set[str] = set()
    for m in index.get("messages") or []:
        fid = m.get("drive_file_id")
        if fid:
            known_drive.add(str(fid))
    known_ids = known_message_ids(index)

    candidates = [
        f
        for f in (walked.get("files") or [])
        if _looks_like_email_file(f.get("name") or "", f.get("mimeType") or "")
    ]
    imported = 0
    messages = list(index.get("messages") or [])
    folders_hit: Set[str] = set()

    for f in candidates:
        fid = f.get("id")
        if not fid or str(fid) in known_drive:
            continue
        mid = f"drive:{fid}"
        if mid in known_ids:
            continue
        dl = download_file_bytes(fid)
        if not dl.get("ok"):
            continue
        raw = dl.get("content") or b""
        if not raw:
            continue
        name = f.get("name") or f"{fid}.html"
        mime = (f.get("mimeType") or "").lower()
        html = None
        text = None
        eml = None
        if name.lower().endswith(".eml") or "rfc822" in mime:
            eml = raw
        elif name.lower().endswith((".html", ".htm", ".mhtml")) or "html" in mime:
            try:
                html = raw.decode("utf-8", errors="replace")
            except Exception:
                html = None
        else:
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                text = None
        paths = _write_local_body(
            client_id,
            message_id=mid,
            html=html,
            text=text,
            raw_rfc822=eml,
        )
        # Best-effort date from filename prefix YYYY-MM-DD_
        sent_at = None
        m = re.match(r"^(\d{4}-\d{2}-\d{2})_", name)
        if m:
            sent_at = m.group(1)
        folder_path = f.get("folder_path") or ""
        if folder_path:
            folders_hit.add(folder_path.split("/")[0] if "/" in folder_path else folder_path)
        entry = {
            "message_id": mid,
            "sent_at": sent_at,
            "subject": name[:500],
            "recipient": "",
            "recipient_emails": [],
            "email_type": "drive_import",
            "source": "drive_tree",
            "status": "imported",
            "drive_path": f.get("path") or name,
            "drive_folder_path": folder_path,
            **paths,
            "drive_file_id": fid,
        }
        messages.append(entry)
        known_ids.add(mid)
        known_drive.add(str(fid))
        imported += 1

    if imported:
        messages.sort(key=lambda m: m.get("sent_at") or "", reverse=True)
        index["messages"] = messages
        save_index(client_id, index)

    return {
        "ok": True,
        "imported": imported,
        "scanned": len(candidates),
        "files_in_tree": len(walked.get("files") or []),
        "folders_visited": len(walked.get("folders") or []),
        "nodes_visited": walked.get("nodes_visited"),
        "folders_with_emails": sorted(folders_hit),
        "total_archived": len(index.get("messages") or []),
        **drive_status,
    }


def upload_pending_emails_to_drive(client_id: int) -> Dict[str, Any]:
    """
    Create Drive Emails/ if needed and upload any local archive messages
    that do not yet have drive_file_id.
    """
    index = load_index(client_id)
    drive_status = _drive_link_status(client_id)
    if not drive_status.get("drive_linked"):
        return {
            "ok": False,
            "uploaded": 0,
            "reason": "drive_not_linked",
            **drive_status,
        }
    drive_folder = _ensure_drive_emails_folder(client_id, index)
    if not drive_folder:
        return {
            "ok": False,
            "uploaded": 0,
            "reason": "emails_folder_failed",
            **drive_status,
        }
    uploaded = 0
    messages = list(index.get("messages") or [])
    for entry in messages:
        if entry.get("drive_file_id"):
            continue
        mid = entry.get("message_id") or "msg"
        sent = entry.get("sent_at") or "unknown"
        fname = f"{sent}_{_safe_file_id(mid)[:24]}.html"
        fid = None
        if entry.get("html_path"):
            fid = _upload_to_drive(
                drive_folder_id=drive_folder,
                local_rel=entry.get("html_path"),
                display_name=fname,
                mime="text/html",
            )
        elif entry.get("eml_path"):
            fid = _upload_to_drive(
                drive_folder_id=drive_folder,
                local_rel=entry.get("eml_path"),
                display_name=fname.replace(".html", ".eml"),
                mime="message/rfc822",
            )
        elif entry.get("text_path"):
            fid = _upload_to_drive(
                drive_folder_id=drive_folder,
                local_rel=entry.get("text_path"),
                display_name=fname.replace(".html", ".txt"),
                mime="text/plain",
            )
        if fid:
            entry["drive_file_id"] = fid
            uploaded += 1
    index["messages"] = messages
    index["drive_emails_folder_id"] = drive_folder
    save_index(client_id, index)
    return {
        "ok": True,
        "uploaded": uploaded,
        "drive_emails_folder_id": drive_folder,
        "total_archived": len(messages),
        **drive_status,
    }


def archive_needs_deeper_history(
    client_id: int, *, lookback_days: int = 200
) -> Dict[str, Any]:
    """
    True when oldest archived Sent mail is newer than today−lookback
    (incremental watermark never walks backward — Jun can be missing forever).
    """
    index = load_index(client_id)
    msgs = index.get("messages") or []
    target = (date.today() - timedelta(days=int(lookback_days))).isoformat()
    if not msgs:
        return {"needed": True, "oldest": None, "target": target, "reason": "empty"}
    dates = [str(m.get("sent_at") or "")[:10] for m in msgs if m.get("sent_at")]
    dates = [d for d in dates if len(d) >= 10]
    if not dates:
        return {"needed": True, "oldest": None, "target": target, "reason": "no_dates"}
    oldest = min(dates)
    needed = oldest > target
    return {
        "needed": needed,
        "oldest": oldest,
        "target": target,
        "reason": "oldest_newer_than_lookback" if needed else "ok",
    }


def ensure_emails_ready_for_investigation(
    client_id: int,
    *,
    max_age_minutes: int = 120,
    lookback_days: int = 200,
) -> Dict[str, Any]:
    """
    Make sure local email archive is fresh enough before holdings-recon diagnosis.

    Uses lookback 200d. If archive only has recent months (e.g. Aug–Oct) but
    lookback needs June, force a full backfill — incremental sync alone never
    fetches older than last_sent_at. Uploads to Drive Emails/ when linked.
    """
    status = archive_status(client_id)
    depth = archive_needs_deeper_history(client_id, lookback_days=lookback_days)
    last = status.get("last_synced_at")
    fresh = False
    if last and status.get("has_archive") and not depth.get("needed"):
        try:
            ts = datetime.fromisoformat(str(last).replace("Z", ""))
            age_min = (datetime.utcnow() - ts).total_seconds() / 60.0
            fresh = age_min <= float(max_age_minutes)
        except Exception:
            fresh = False
    def _scan_drive_nodes() -> Dict[str, Any]:
        """Search all client Drive subfolders for email-like files."""
        try:
            if status.get("drive_linked") or _drive_link_status(client_id).get(
                "drive_linked"
            ):
                return pull_emails_from_drive_tree(int(client_id))
        except Exception as exc:
            logger.warning("Drive tree email scan failed: %s", exc)
            return {"ok": False, "imported": 0, "error": str(exc)}
        return {"ok": False, "imported": 0, "reason": "drive_not_linked"}

    if fresh:
        drive_up = {"uploaded": 0}
        try:
            if status.get("drive_linked"):
                drive_up = upload_pending_emails_to_drive(int(client_id))
        except Exception as exc:
            drive_up = {"uploaded": 0, "error": str(exc)}
        drive_pull = _scan_drive_nodes()
        return {
            "ok": True,
            "skipped": True,
            "reason": "archive_fresh",
            "max_age_minutes": max_age_minutes,
            "depth": depth,
            "drive_upload": drive_up,
            "drive_tree_scan": drive_pull,
            **archive_status(client_id),
        }
    # Backfill when history is shallow; else incremental from watermark
    force_full = bool(depth.get("needed") or not status.get("has_archive"))
    result = sync_client_emails(
        int(client_id),
        lookback_days=lookback_days,
        upload_drive=True,
        force_full=force_full,
        max_messages=200,
    )
    try:
        drive_up = upload_pending_emails_to_drive(int(client_id))
        result["drive_upload"] = drive_up
    except Exception as exc:
        result["drive_upload"] = {"uploaded": 0, "error": str(exc)}
    result["drive_tree_scan"] = _scan_drive_nodes()
    result["skipped"] = False
    result["reason"] = (
        "backfill_for_investigation" if force_full else "synced_for_investigation"
    )
    result["depth"] = depth
    result["force_full"] = force_full
    return result


def sync_client_emails(
    client_id: int,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    upload_drive: bool = True,
    force_full: bool = False,
    max_messages: int = 150,
) -> Dict[str, Any]:
    """
    Pull IMAP Sent + EmailLog into local archive (and Drive Emails/ when linked).
    Always archives locally; flags needs_drive_details when Drive is not linked.

    force_full=True ignores watermark and pulls lookback_days from today
    (still TO/CC filtered to the client — not the whole Sent mailbox).
    """
    from services.gmail_sent_imap_service import fetch_sent_to_addresses

    addresses = client_email_addresses(client_id)
    index = load_index(client_id)
    index["client_emails"] = addresses
    if force_full:
        # Ignore watermark for a backfill; still address-filtered.
        tmp = {**index, "last_sent_at": None}
        since, before = sync_window(tmp, lookback_days=lookback_days)
    else:
        since, before = sync_window(index, lookback_days=lookback_days)
    drive_status = _drive_link_status(client_id)
    # Only attempt Drive upload when linked
    do_drive = bool(upload_drive and drive_status.get("drive_linked"))

    imap_added = 0
    imap_error = None
    imap_fetched = 0
    if addresses:
        try:
            rows = fetch_sent_to_addresses(
                addresses=addresses,
                since=since,
                before=before,
                max_messages=int(max_messages),
            )
            imap_fetched = len(rows)
            imap_added = merge_imap_into_index(
                client_id, index, rows, upload_drive=do_drive
            )
        except Exception as exc:
            imap_error = str(exc)
            logger.warning("IMAP sync failed for client %s: %s", client_id, exc)
    else:
        imap_error = "Client has no email addresses on file"

    elog_added = merge_emaillog_into_index(
        client_id, index, since=since, before=before, upload_drive=do_drive
    )

    index["last_synced_at"] = datetime.utcnow().isoformat() + "Z"
    index["needs_drive_details"] = drive_status["needs_drive_details"]
    save_index(client_id, index)

    drive_uploaded = 0
    if do_drive:
        try:
            up = upload_pending_emails_to_drive(int(client_id))
            drive_uploaded = int(up.get("uploaded") or 0)
            index = load_index(client_id)
        except Exception as exc:
            logger.warning("Drive pending email upload failed: %s", exc)

    return {
        "ok": True,
        "client_id": int(client_id),
        "client_emails": addresses,
        "since": since.isoformat(),
        "before": before.isoformat(),
        "imap_fetched": imap_fetched,
        "imap_added": imap_added,
        "emaillog_added": elog_added,
        "total_archived": len(index.get("messages") or []),
        "last_sent_at": index.get("last_sent_at"),
        "drive_emails_folder_id": index.get("drive_emails_folder_id"),
        "drive_uploaded": drive_uploaded,
        "imap_error": imap_error,
        **drive_status,
        "stored_locally": True,
        "stored_on_drive": do_drive and bool(index.get("drive_emails_folder_id")),
    }


def list_archived_emails(
    client_id: int,
    *,
    near_as_of: Optional[date] = None,
    window_days: int = 45,
    limit: int = 40,
) -> Dict[str, Any]:
    """List archived messages for diagnosis UI."""
    index = load_index(client_id)
    messages = list(index.get("messages") or [])
    if near_as_of is not None:
        start = near_as_of - timedelta(days=window_days)
        end = near_as_of + timedelta(days=window_days)
        filtered = []
        for m in messages:
            d = _as_date(m.get("sent_at"))
            if d and start <= d <= end:
                filtered.append(m)
        messages = filtered
    messages = messages[: int(limit)]
    return {
        "client_id": int(client_id),
        "emails": messages,
        "total_archived": len(index.get("messages") or []),
        "last_sent_at": index.get("last_sent_at"),
        "last_synced_at": index.get("last_synced_at"),
        "client_emails": index.get("client_emails") or client_email_addresses(client_id),
        "drive_emails_folder_id": index.get("drive_emails_folder_id"),
    }


def archive_status(client_id: int) -> Dict[str, Any]:
    index = load_index(client_id)
    drive_status = _drive_link_status(client_id)
    return {
        "client_id": int(client_id),
        "total_archived": len(index.get("messages") or []),
        "last_sent_at": index.get("last_sent_at"),
        "last_synced_at": index.get("last_synced_at"),
        "client_emails": index.get("client_emails") or client_email_addresses(client_id),
        "drive_emails_folder_id": index.get("drive_emails_folder_id"),
        "has_archive": bool(index.get("messages")),
        **drive_status,
    }
