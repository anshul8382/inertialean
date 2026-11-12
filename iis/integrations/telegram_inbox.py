"""Pull PDF attachments from a Telegram group into Shared Drive inbox.

Uses a personal Telethon user session (not a bot). Drive upload reuses
iis.integrations.gdrive — failures never raise to callers of sync.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import timezone
from pathlib import Path
from typing import Any

from iis.config import settings

logger = logging.getLogger(__name__)


def _session_file() -> Path:
    path = settings.telegram_session_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _local_inbox() -> Path:
    path = settings.telegram_local_inbox
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_seen() -> dict[str, Any]:
    path = settings.telegram_seen_path
    if not path.exists():
        return {"ids": [], "file_unique_ids": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"ids": [], "file_unique_ids": []}
        data.setdefault("ids", [])
        data.setdefault("file_unique_ids", [])
        return data
    except Exception:
        return {"ids": [], "file_unique_ids": []}


def _save_seen(data: dict[str, Any]) -> None:
    path = settings.telegram_seen_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # Cap growth
    ids = list(data.get("ids") or [])[-5000:]
    fids = list(data.get("file_unique_ids") or [])[-5000:]
    path.write_text(json.dumps({"ids": ids, "file_unique_ids": fids}, indent=2), encoding="utf-8")


def telegram_credentials_ok() -> bool:
    return bool(settings.telegram_api_id and settings.telegram_api_hash)


def telegram_session_exists() -> bool:
    # Telethon appends ".session" to the session path string
    return Path(str(_session_file()) + ".session").exists()


def _resolve_group_entity(group: str) -> str | int:
    g = (group or "").strip()
    if not g:
        raise ValueError("IIS_TELEGRAM_GROUP is not set (use @username or numeric chat id)")
    if g.startswith("@"):
        return g
    if re.fullmatch(r"-?\d+", g):
        return int(g)
    return g


def _build_client():
    try:
        from telethon import TelegramClient
    except ImportError as e:
        raise ImportError("telethon is required — pip install telethon") from e
    if not telegram_credentials_ok():
        raise RuntimeError(
            "Set IIS_TELEGRAM_API_ID and IIS_TELEGRAM_API_HASH "
            "(from https://my.telegram.org) before auth/sync."
        )
    return TelegramClient(
        str(_session_file()),
        int(settings.telegram_api_id),
        str(settings.telegram_api_hash),
    )


def _safe_filename(name: str, fallback: str) -> str:
    base = Path(name or fallback).name
    base = re.sub(r"[^\w.\- ()\[\]]+", "_", base).strip("._ ") or fallback
    if not base.lower().endswith(".pdf"):
        base = f"{base}.pdf"
    return base[:180]


def _is_pdf_document(msg: Any) -> bool:
    doc = getattr(msg, "document", None)
    if not doc:
        return False
    mime = getattr(doc, "mime_type", None) or ""
    if mime == "application/pdf":
        return True
    for attr in getattr(doc, "attributes", None) or []:
        fname = getattr(attr, "file_name", None)
        if fname and str(fname).lower().endswith(".pdf"):
            return True
    return False


def _doc_file_name(msg: Any) -> str | None:
    doc = msg.document
    for attr in getattr(doc, "attributes", None) or []:
        fname = getattr(attr, "file_name", None)
        if fname:
            return str(fname)
    return None


def _file_unique_id(msg: Any) -> str | None:
    doc = getattr(msg, "document", None)
    if not doc:
        return None
    uid = getattr(doc, "id", None)
    return str(uid) if uid is not None else None


async def _auth_qr(*, timeout: int = 300) -> dict[str, Any]:
    """Login by scanning a QR code; auto-refreshes when the token expires (~30s)."""
    from telethon.errors import SessionPasswordNeededError

    client = _build_client()
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            return {
                "ok": True,
                "method": "existing_session",
                "user_id": getattr(me, "id", None),
                "username": getattr(me, "username", None),
                "session": str(_session_file()),
            }

        print("\n=== Telegram QR login ===")
        print("Do NOT paste tg:// links into Terminal.")
        print("Phone: Telegram → Settings → Devices → Link Desktop Device")
        print("A big QR image will open in Preview — scan that (tokens expire ~30s; we auto-refresh).\n")

        qr = await client.qr_login()
        deadline = asyncio.get_event_loop().time() + max(60, timeout)
        attempt = 0
        while True:
            attempt += 1
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    "QR login timed out. Run again: iis telegram auth --reset --method qr"
                )
            png = _write_login_qr_png(qr.url)
            print(f"[QR #{attempt}] Scan the Preview window now (expires in ~30s). Image: {png}")
            try:
                # Wait until this token expires (Telethon default) or remaining budget
                wait_for = min(remaining, 35.0)
                try:
                    # Prefer waiting until token expiry if shorter
                    exp = getattr(qr, "expires", None)
                    if exp is not None:
                        import datetime as _dt

                        secs = (exp - _dt.datetime.now(tz=_dt.timezone.utc)).total_seconds()
                        if secs > 1:
                            wait_for = min(wait_for, secs)
                except Exception:
                    pass
                await qr.wait(timeout=wait_for)
                break
            except asyncio.TimeoutError:
                print("Token expired — generating a fresh QR…")
                await qr.recreate()
                continue
            except SessionPasswordNeededError:
                import getpass

                pw = getpass.getpass("Two-step password (2FA): ")
                await client.sign_in(password=pw)
                break

        me = await client.get_me()
        return {
            "ok": True,
            "method": "qr",
            "user_id": getattr(me, "id", None),
            "username": getattr(me, "username", None),
            "session": str(_session_file()),
        }
    finally:
        await client.disconnect()


def _describe_sent_code(sent: Any) -> str:
    t = type(getattr(sent, "type", None) or sent).__name__
    if "App" in t:
        return (
            "Telegram sent the code to your ALREADY LOGGED-IN apps "
            "(this includes Telegram on this Mac) — chat Telegram / 777000. Not SMS."
        )
    if "Sms" in t:
        return "Telegram says it will send an SMS to your phone."
    if "Call" in t or "Flash" in t:
        return "Telegram will call you and speak/show the code."
    if "Email" in t:
        return "Telegram sent the code by email (check inbox / spam)."
    return f"Delivery type: {t}"


def _open_mac_telegram() -> None:
    try:
        import subprocess

        subprocess.run(["open", "-a", "Telegram"], check=False)
    except Exception:
        pass


async def _auth_phone(*, phone: str | None = None) -> dict[str, Any]:
    """Phone + login code. If Mac Telegram is logged in, the code lands THERE (777000)."""
    import getpass

    from telethon.errors import (
        FloodWaitError,
        PhoneCodeExpiredError,
        PhoneCodeInvalidError,
        SessionPasswordNeededError,
    )
    from telethon.tl.functions.auth import ResendCodeRequest

    client = _build_client()
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            return {
                "ok": True,
                "method": "existing_session",
                "user_id": getattr(me, "id", None),
                "username": getattr(me, "username", None),
                "session": str(_session_file()),
            }

        print("\n=== Telegram phone login ===")
        print("Because Telegram is already logged in on this Mac, the code almost always")
        print("appears IN THE MAC TELEGRAM APP → chat with “Telegram” or 777000.")
        print("It is usually NOT an SMS. Do NOT paste api_hash.\n")

        phone = (phone or getattr(settings, "telegram_phone", None) or "").strip()
        if not phone:
            phone = input("Phone number with country code (e.g. +9198xxxxxxxx): ").strip()
        if not phone.startswith("+"):
            raise ValueError("Phone must include country code, starting with +")

        sent = await client.send_code_request(phone)
        print("\n" + _describe_sent_code(sent))
        _open_mac_telegram()
        print("Opened Telegram on this Mac — look at the newest message from Telegram / 777000.\n")

        while True:
            print("Options: paste the digits, or type  sms  /  call  /  quit")
            raw = input("Login code: ").strip().lower()
            if raw in ("quit", "q", "exit"):
                raise RuntimeError("Auth cancelled.")
            if raw == "sms":
                try:
                    # Prefer force_sms on a fresh request when possible
                    try:
                        sent = await client.send_code_request(phone, force_sms=True)
                    except TypeError:
                        sent = await client(ResendCodeRequest(phone, sent.phone_code_hash))
                    print(_describe_sent_code(sent))
                    print("Requested SMS/resend. Check phone SMS, then paste digits.\n")
                except FloodWaitError as e:
                    print(f"Rate limited — wait {getattr(e, 'seconds', '?')}s then retry.")
                except Exception as e:
                    print(f"Could not force SMS/resend: {e}")
                continue
            if raw == "call":
                try:
                    sent = await client(ResendCodeRequest(phone, sent.phone_code_hash))
                    print(_describe_sent_code(sent))
                    print("Resend requested (may be call/SMS depending on Telegram).\n")
                except Exception as e:
                    print(f"Resend failed: {e}")
                continue

            code = re.sub(r"\D", "", raw)
            if not code:
                print("Enter digits only, or sms / call / quit")
                continue

            try:
                await client.sign_in(phone, code=code, phone_code_hash=sent.phone_code_hash)
                break
            except SessionPasswordNeededError:
                pw = getpass.getpass("Two-step verification password: ")
                await client.sign_in(password=pw)
                break
            except PhoneCodeInvalidError:
                print("INVALID CODE — use only the digits from Mac Telegram chat 777000 (or SMS). Try again.")
                continue
            except PhoneCodeExpiredError:
                print("Code expired — requesting a new one…")
                sent = await client.send_code_request(phone)
                print(_describe_sent_code(sent))
                _open_mac_telegram()
                continue
            except FloodWaitError as e:
                raise RuntimeError(
                    f"Telegram rate-limit: wait {getattr(e, 'seconds', '?')} seconds, then retry."
                ) from e

        me = await client.get_me()
        return {
            "ok": True,
            "method": "phone",
            "user_id": getattr(me, "id", None),
            "username": getattr(me, "username", None),
            "session": str(_session_file()),
        }
    finally:
        await client.disconnect()


def run_telegram_auth(
    *,
    method: str = "phone",
    timeout: int = 300,
    phone: str | None = None,
) -> dict[str, Any]:
    """Interactive login. Default is phone code (more reliable than QR)."""
    if method == "qr":
        return asyncio.run(_auth_qr(timeout=timeout))
    return asyncio.run(_auth_phone(phone=phone))


def clear_telegram_session() -> None:
    """Remove partial/broken Telethon session files so auth can retry cleanly."""
    base = _session_file()
    for p in (Path(str(base) + ".session"), Path(str(base) + ".session-journal")):
        if p.exists():
            p.unlink()


def _write_login_qr_png(url: str) -> Path:
    """Save a large PNG QR and open it in Preview for easy phone scanning."""
    out_dir = settings.data_dir / "telegram"
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / "login_qr.png"
    try:
        import qrcode

        img = qrcode.make(url, box_size=12, border=2)
        img.save(png)
    except Exception:
        # Minimal 1x1 placeholder should never happen if qrcode installed
        png.write_bytes(b"")
        raise RuntimeError("Could not generate QR PNG — pip install 'qrcode[pil]'") from None
    try:
        import subprocess

        subprocess.run(["open", "-a", "Preview", str(png)], check=False)
    except Exception:
        try:
            import subprocess

            subprocess.run(["open", str(png)], check=False)
        except Exception:
            print(f"Open this image manually and scan it: {png}")
    return png


async def _status_async() -> dict[str, Any]:
    from iis.integrations.gdrive import drive_configured, ensure_telegram_inbox_folder

    out: dict[str, Any] = {
        "enabled": settings.telegram_enabled,
        "credentials_ok": telegram_credentials_ok(),
        "session_ok": telegram_session_exists(),
        "group": settings.telegram_group,
        "drive_configured": drive_configured(),
        "drive_inbox_name": settings.telegram_drive_inbox_name,
        "drive_inbox_id": None,
        "group_ok": False,
        "group_title": None,
        "error": None,
    }
    if drive_configured():
        out["drive_inbox_id"] = ensure_telegram_inbox_folder()

    if not out["credentials_ok"]:
        out["error"] = "Missing IIS_TELEGRAM_API_ID / IIS_TELEGRAM_API_HASH"
        return out
    if not out["session_ok"]:
        out["error"] = "No session — run: iis telegram auth"
        return out
    if not settings.telegram_group:
        out["error"] = "Set IIS_TELEGRAM_GROUP=@group or chat id"
        return out

    try:
        client = _build_client()
        await client.connect()
        if not await client.is_user_authorized():
            out["error"] = "Session expired — run: iis telegram auth"
            await client.disconnect()
            return out
        entity = await client.get_entity(_resolve_group_entity(settings.telegram_group))
        out["group_ok"] = True
        out["group_title"] = getattr(entity, "title", None) or getattr(entity, "username", None)
        await client.disconnect()
    except Exception as e:
        out["error"] = str(e)
    return out


def telegram_status() -> dict[str, Any]:
    return asyncio.run(_status_async())


async def _sync_async(*, limit: int = 50) -> dict[str, Any]:
    from iis.integrations.gdrive import drive_configured, ensure_telegram_inbox_folder, upload_file_to_folder

    result: dict[str, Any] = {
        "scanned": 0,
        "pdfs_found": 0,
        "downloaded": 0,
        "uploaded": 0,
        "skipped_seen": 0,
        "errors": [],
        "files": [],
        "drive_inbox_id": None,
    }
    if not settings.telegram_enabled:
        result["errors"].append("telegram_enabled is false")
        return result
    if not settings.telegram_group:
        result["errors"].append("IIS_TELEGRAM_GROUP not set")
        return result

    seen = _load_seen()
    seen_ids = set(str(x) for x in seen.get("ids") or [])
    seen_fids = set(str(x) for x in seen.get("file_unique_ids") or [])

    inbox_id = None
    if drive_configured():
        inbox_id = ensure_telegram_inbox_folder()
        result["drive_inbox_id"] = inbox_id
    else:
        result["errors"].append("Google Drive not authenticated — PDFs saved locally only (iis drive auth)")

    local_dir = _local_inbox()
    client = _build_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            result["errors"].append("Not authorized — run: iis telegram auth")
            return result
        entity = await client.get_entity(_resolve_group_entity(settings.telegram_group))
        async for msg in client.iter_messages(entity, limit=max(1, limit)):
            result["scanned"] += 1
            if not _is_pdf_document(msg):
                continue
            result["pdfs_found"] += 1
            msg_key = f"{getattr(msg, 'chat_id', '')}:{msg.id}"
            fid = _file_unique_id(msg)
            if msg_key in seen_ids or (fid and fid in seen_fids):
                result["skipped_seen"] += 1
                continue

            raw_name = _doc_file_name(msg) or f"telegram_{msg.id}.pdf"
            safe = _safe_filename(raw_name, f"telegram_{msg.id}.pdf")
            # Prefix message id to reduce name collisions across posts
            remote_name = f"{msg.id}_{safe}"
            dest = local_dir / remote_name
            try:
                await client.download_media(msg, file=str(dest))
                result["downloaded"] += 1
            except Exception as e:
                result["errors"].append(f"download msg {msg.id}: {e}")
                continue

            drive_id = None
            if inbox_id and dest.exists():
                drive_id = upload_file_to_folder(dest, inbox_id, remote_name=remote_name, skip_if_exists=True)
                if drive_id:
                    result["uploaded"] += 1
                else:
                    result["errors"].append(f"Drive upload failed: {remote_name}")

            seen_ids.add(msg_key)
            if fid:
                seen_fids.add(fid)
            result["files"].append(
                {
                    "message_id": msg.id,
                    "local": str(dest),
                    "drive_id": drive_id,
                    "name": remote_name,
                }
            )

        seen["ids"] = list(seen_ids)
        seen["file_unique_ids"] = list(seen_fids)
        _save_seen(seen)
    except Exception as e:
        logger.exception("telegram sync failed")
        result["errors"].append(str(e))
    finally:
        await client.disconnect()
    return result


def sync_pdfs_to_drive(*, limit: int = 50) -> dict[str, Any]:
    """Download new PDFs from the configured group and upload to Drive inbox."""
    return asyncio.run(_sync_async(limit=limit))


_STOPWORDS = {
    "limited",
    "ltd",
    "ltd.",
    "pvt",
    "private",
    "the",
    "and",
    "of",
    "india",
    "industries",
    "company",
    "co",
    "corp",
    "corporation",
}


def company_search_terms(company: str) -> list[str]:
    """Build Telegram search strings from a company display name."""
    raw = (company or "").strip()
    if not raw:
        return []
    terms: list[str] = []
    # Full name
    terms.append(raw)
    # Without legal suffixes
    cleaned = re.sub(
        r"\b(limited|ltd\.?|pvt\.?|private|corporation|corp\.?)\b",
        "",
        raw,
        flags=re.I,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    if cleaned and cleaned.lower() != raw.lower():
        terms.append(cleaned)
    # Significant tokens (length >= 4), skip stopwords
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", cleaned or raw) if t]
    for t in tokens:
        if len(t) >= 4 and t.lower() not in _STOPWORDS:
            terms.append(t)
    # Dedupe preserving order
    out: list[str] = []
    seen: set[str] = set()
    for t in terms:
        key = t.lower()
        if key in seen or len(t) < 3:
            continue
        seen.add(key)
        out.append(t)
    return out[:6]


def _haystack(msg: Any) -> str:
    parts = [str(getattr(msg, "message", None) or "")]
    fname = _doc_file_name(msg)
    if fname:
        parts.append(fname)
    return " ".join(parts).lower()


def _matches_terms(hay: str, terms: list[str]) -> bool:
    if not hay or not terms:
        return False
    for t in terms:
        tl = t.lower()
        if len(tl) >= 4 and tl in hay:
            return True
        # short exact token
        if len(tl) < 4 and re.search(rf"\b{re.escape(tl)}\b", hay):
            return True
    return False


async def _iter_search_pdfs(
    client: Any,
    entity: Any,
    term: str,
    *,
    limit: int,
    terms: list[str],
    by_id: dict[int, Any],
) -> None:
    """One Telegram search query with a hard timeout (large groups can stall)."""
    async def _run() -> None:
        async for msg in client.iter_messages(entity, search=term, limit=limit):
            if not _is_pdf_document(msg):
                continue
            if not _matches_terms(_haystack(msg), terms):
                continue
            by_id[msg.id] = msg

    try:
        await asyncio.wait_for(_run(), timeout=45.0)
    except asyncio.TimeoutError:
        logger.warning("telegram search timed out for %r", term)
    except Exception as e:
        logger.warning("telegram search %r failed: %s", term, e)


async def _collect_company_pdf_messages(
    client: Any,
    entity: Any,
    terms: list[str],
    *,
    search_limit: int,
) -> list[Any]:
    """Search group via Telegram search (+ light recent backup); unique PDF messages."""
    by_id: dict[int, Any] = {}
    # Prefer distinctive terms first (longest), max 3 queries
    ranked = sorted({t for t in terms}, key=lambda t: (-len(t), t.lower()))[:3]
    per = max(20, min(search_limit, 80))
    for term in ranked:
        print(f"  Searching Telegram for “{term}”…", flush=True)
        await _iter_search_pdfs(client, entity, term, limit=per, terms=terms, by_id=by_id)
        if len(by_id) >= 25:
            break
    # Light recent backup only if search found almost nothing
    if len(by_id) < 3:
        print("  Few search hits — scanning recent PDFs…", flush=True)

        async def _recent() -> None:
            async for msg in client.iter_messages(entity, limit=min(200, max(search_limit, 80))):
                if not _is_pdf_document(msg):
                    continue
                if not _matches_terms(_haystack(msg), terms):
                    continue
                by_id[msg.id] = msg

        try:
            await asyncio.wait_for(_recent(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.warning("telegram recent scan timed out")
        except Exception as e:
            logger.warning("telegram recent scan failed: %s", e)
    return sorted(by_id.values(), key=lambda m: m.id, reverse=True)


async def _sync_company_async(
    company: str,
    *,
    search_limit: int = 80,
    max_files: int = 25,
    ingest: bool = True,
) -> dict[str, Any]:
    """Find company PDFs in the Telegram group → local brokerage_reports → Drive → library ingest."""
    from iis.integrations.gdrive import drive_configured, upload_local_file
    from iis.library.detect import company_key_from_info
    from iis.library.ingest import ingest_file_to_library
    from iis.library.layout import ensure_company_layout
    from iis.library.store import get_company_library_store
    from iis.models.artifacts import CompanyInfo
    from iis.models.session import ResearchSession

    from iis.integrations.telegram_report_index import find_reports_for_company, load_index

    result: dict[str, Any] = {
        "company": company,
        "terms": [],
        "company_key": None,
        "matched": 0,
        "downloaded": 0,
        "uploaded": 0,
        "ingested": 0,
        "skipped_seen": 0,
        "errors": [],
        "files": [],
        "index_hits": 0,
        "source": "telegram_search",
    }
    terms = company_search_terms(company)
    result["terms"] = terms
    if not terms:
        result["errors"].append("Empty company name")
        return result
    if not settings.telegram_enabled:
        result["errors"].append("telegram_enabled is false")
        return result
    if not settings.telegram_group:
        result["errors"].append("IIS_TELEGRAM_GROUP not set")
        return result
    if not telegram_session_exists():
        result["errors"].append("Not authorized — run: iis telegram auth")
        return result

    # Resolve company library key / folder
    store = get_company_library_store()
    session = ResearchSession()
    session.company = CompanyInfo(name=company.strip())
    session.company_key = company_key_from_info(None, company)
    existing = store.find_library_for_session(session)
    if existing:
        session.company_key = existing.company_key
        if existing.company and existing.company.name:
            session.company = existing.company
    key = session.company_key or company_key_from_info(session.company, company)
    if not key:
        result["errors"].append("Could not resolve company_key")
        return result
    session.company_key = key
    result["company_key"] = key

    root = ensure_company_layout(settings.data_dir / "companies", key)
    dest_dir = root / "brokerage_reports"
    dest_dir.mkdir(parents=True, exist_ok=True)

    seen = _load_seen()
    seen_ids = set(str(x) for x in seen.get("ids") or [])
    seen_fids = set(str(x) for x in seen.get("file_unique_ids") or [])

    # Prefer daily cross-ref index (fast); fall back to live Telegram search
    index_hits = find_reports_for_company(company, index=load_index())
    result["index_hits"] = len(index_hits)
    message_ids_from_index = [
        int(h["message_id"])
        for h in index_hits
        if h.get("message_id") is not None
    ][:max_files]

    client = _build_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            result["errors"].append("Not authorized — run: iis telegram auth")
            return result
        entity = await client.get_entity(_resolve_group_entity(settings.telegram_group))

        messages: list[Any] = []
        if message_ids_from_index:
            result["source"] = "report_index"
            print(f"  Index matched {len(index_hits)} report(s) — fetching by message id…", flush=True)
            for mid in message_ids_from_index:
                try:
                    msg = await client.get_messages(entity, ids=mid)
                    if msg and _is_pdf_document(msg):
                        messages.append(msg)
                except Exception as e:
                    result["errors"].append(f"get message {mid}: {e}")
        if not messages:
            result["source"] = "telegram_search"
            messages = await _collect_company_pdf_messages(
                client, entity, terms, search_limit=search_limit
            )
        result["matched"] = len(messages)

        for msg in messages[:max_files]:
            msg_key = f"co:{key}:{getattr(msg, 'chat_id', '')}:{msg.id}"
            fid = _file_unique_id(msg)
            raw_name = _doc_file_name(msg) or f"telegram_{msg.id}.pdf"
            safe = _safe_filename(raw_name, f"telegram_{msg.id}.pdf")
            remote_name = f"tg_{msg.id}_{safe}"
            dest = dest_dir / remote_name

            if dest.exists() or msg_key in seen_ids or (fid and f"co:{key}:{fid}" in seen_fids):
                result["skipped_seen"] += 1
                if dest.exists() and ingest:
                    try:
                        _, status = ingest_file_to_library(
                            session, dest, store, forced_kind="brokerage_report"
                        )
                        if status == "ingested":
                            result["ingested"] += 1
                    except Exception:
                        pass
                continue

            try:
                await client.download_media(msg, file=str(dest))
                result["downloaded"] += 1
            except Exception as e:
                result["errors"].append(f"download msg {msg.id}: {e}")
                continue

            drive_id = None
            if drive_configured() and dest.exists():
                try:
                    drive_id = upload_local_file(
                        dest,
                        session.company.name if session.company else company,
                        remote_name=remote_name,
                        skip_if_exists=True,
                        doc_kind="brokerage_report",
                    )
                    if drive_id:
                        result["uploaded"] += 1
                except Exception as e:
                    result["errors"].append(f"Drive upload {remote_name}: {e}")

            if ingest and dest.exists():
                try:
                    _, status = ingest_file_to_library(
                        session, dest, store, forced_kind="brokerage_report"
                    )
                    if status == "ingested":
                        result["ingested"] += 1
                except Exception as e:
                    result["errors"].append(f"ingest {remote_name}: {e}")

            seen_ids.add(msg_key)
            if fid:
                seen_fids.add(f"co:{key}:{fid}")

            # Keep cross-ref index warm with anything we actually pull
            meta = next((h for h in index_hits if h.get("message_id") == msg.id), None)
            try:
                from iis.integrations.telegram_report_index import (
                    load_index,
                    parse_report_metadata,
                    save_index,
                    upsert_entry,
                )

                caption = str(getattr(msg, "message", None) or "") or None
                msg_date = None
                try:
                    if getattr(msg, "date", None):
                        msg_date = msg.date.astimezone(timezone.utc).strftime("%Y-%m-%d")
                except Exception:
                    pass
                entry = parse_report_metadata(
                    raw_name,
                    caption=caption,
                    message_id=msg.id,
                    message_date=msg_date,
                )
                # Ensure research company is searchable even if filename parse missed it
                display = (session.company.name if session.company else company) or company
                if display and not entry.get("company"):
                    entry["company"] = display.strip()
                for t in terms:
                    tl = t.lower()
                    kws = list(entry.get("company_keywords") or [])
                    if tl not in kws:
                        kws.append(tl)
                    entry["company_keywords"] = kws
                    kws2 = list(entry.get("keywords") or [])
                    if tl not in kws2:
                        kws2.append(tl)
                    entry["keywords"] = kws2
                idx = load_index()
                upsert_entry(idx, entry)
                save_index(idx)
                meta = entry
            except Exception as e:
                logger.debug("index upsert skipped: %s", e)

            result["files"].append(
                {
                    "message_id": msg.id,
                    "local": str(dest),
                    "drive_id": drive_id,
                    "name": remote_name,
                    "broker": (meta or {}).get("broker"),
                    "report_type": (meta or {}).get("report_type"),
                    "rating": (meta or {}).get("rating"),
                    "report_date": (meta or {}).get("report_date"),
                }
            )

        seen["ids"] = list(seen_ids)
        seen["file_unique_ids"] = list(seen_fids)
        _save_seen(seen)
        try:
            lib = store.load(key)
            store.save(lib)
        except Exception:
            pass
    except Exception as e:
        logger.exception("telegram company sync failed")
        result["errors"].append(str(e))
    finally:
        await client.disconnect()
    return result


def sync_pdfs_for_company(
    company: str,
    *,
    search_limit: int = 80,
    max_files: int = 25,
    ingest: bool = True,
) -> dict[str, Any]:
    """Pull company-related PDFs from Telegram into the company library for analysis."""
    return asyncio.run(
        _sync_company_async(
            company,
            search_limit=search_limit,
            max_files=max_files,
            ingest=ingest,
        )
    )
