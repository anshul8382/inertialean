"""Cross-reference index of Telegram research PDFs.

Daily-updated catalog at ~/.iis/telegram/report_index.json with:
  file name, broker, company, report type, report date, rating (buy/hold/sell), keywords.

Company research queries this index first, then downloads only relevant missing PDFs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iis.config import settings

logger = logging.getLogger(__name__)

INDEX_VERSION = 1

BROKERS: list[tuple[str, re.Pattern[str]]] = [
    ("Motilal Oswal", re.compile(r"motilal[\s_]*oswal", re.I)),
    ("ICICI Securities", re.compile(r"icici[\s_]*securities", re.I)),
    ("Kotak", re.compile(r"\bkotak\b", re.I)),
    ("Antique", re.compile(r"\bantique\b", re.I)),
    ("Nuvama", re.compile(r"\bnuvama\b", re.I)),
    ("Systematix", re.compile(r"\bsystematix\b", re.I)),
    ("Mirae Asset Sharekhan", re.compile(r"mirae|sharekhan", re.I)),
    ("PL Capital", re.compile(r"\bpl[\s_]*capital\b", re.I)),
    ("Axis Securities", re.compile(r"axis[\s_]*securities", re.I)),
    ("Phillip Capital", re.compile(r"phillip[\s_]*capital", re.I)),
    ("Emkay", re.compile(r"\bemkay\b", re.I)),
    ("Nirmal Bang", re.compile(r"nirmal[\s_]*bang", re.I)),
    ("UBS", re.compile(r"\bubs\b", re.I)),
    ("JM Financial", re.compile(r"jm[\s_]*financial", re.I)),
    ("Edelweiss", re.compile(r"\bedelweiss\b", re.I)),
    ("Nomura", re.compile(r"\bnomura\b", re.I)),
    ("Jefferies", re.compile(r"\bjefferies\b", re.I)),
    ("Goldman Sachs", re.compile(r"goldman", re.I)),
    ("Morgan Stanley", re.compile(r"morgan[\s_]*stanley", re.I)),
    ("CLSA", re.compile(r"\bclsa\b", re.I)),
    ("Macquarie", re.compile(r"\bmacquarie\b", re.I)),
    ("HSBC", re.compile(r"\bhsbc\b", re.I)),
    ("Citi", re.compile(r"\bciti(?:group)?\b", re.I)),
    ("BNP Paribas", re.compile(r"bnp", re.I)),
    ("Dam Capital", re.compile(r"dam[\s_]*capital", re.I)),
    ("Ambit", re.compile(r"\bambit\b", re.I)),
]

REPORT_TYPES: list[tuple[str, re.Pattern[str]]] = [
    ("initiating_coverage", re.compile(r"initiat(?:ing|ion)\s*(?:coverage)?|initiating coverage", re.I)),
    ("ipo_note", re.compile(r"\bipo\b", re.I)),
    ("results_preview", re.compile(r"results?\s*preview|preview\s*on", re.I)),
    ("quarterly_update", re.compile(r"\bQ[1-4]\b|quarterly|results?\s*update|earnings\s*update|1[Hh]FY|FY\d{2}", re.I)),
    ("annual_report_analysis", re.compile(r"annual\s*report|year[\s_-]*end\s*analysis", re.I)),
    ("sector_note", re.compile(r"sector\s*(?:note|update)|automobiles?|banking\s*sector", re.I)),
    ("daily_note", re.compile(r"\bdaily\b", re.I)),
    ("rating_update", re.compile(r"sees\s+\d+|UPSIDE|DOWNSIDE|target\s*price", re.I)),
    ("company_update", re.compile(r"\bupdate\s+on\b|financial\s+update", re.I)),
]

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def index_path() -> Path:
    return Path(settings.telegram_report_index_path)


def empty_index() -> dict[str, Any]:
    return {
        "version": INDEX_VERSION,
        "updated_at": None,
        "group": settings.telegram_group,
        "last_message_id": 0,
        "entry_count": 0,
        "entries": [],
    }


def load_index() -> dict[str, Any]:
    path = index_path()
    if not path.exists():
        return empty_index()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return empty_index()
        data.setdefault("entries", [])
        data.setdefault("last_message_id", 0)
        return data
    except Exception:
        return empty_index()


def save_index(data: dict[str, Any]) -> Path:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["entry_count"] = len(data.get("entries") or [])
    data["version"] = INDEX_VERSION
    data["group"] = settings.telegram_group
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _detect_broker(text: str) -> str | None:
    for name, pat in BROKERS:
        if pat.search(text):
            return name
    return None


def _detect_report_type(text: str) -> str:
    for name, pat in REPORT_TYPES:
        if pat.search(text):
            return name
    return "other"


def _detect_rating(text: str) -> tuple[str | None, str | None]:
    """Return (rating bucket, detail string)."""
    t = text.upper()
    m = re.search(r"(\d+)\s*[_%]?\s*(UPSIDE|DOWNSIDE)", t)
    detail = None
    if m:
        detail = f"{m.group(1)}% {m.group(2).title()}"
        return ("buy" if m.group(2) == "UPSIDE" else "sell"), detail
    if re.search(r"\bBUY\b|\bACCUMULATE\b|\bOVERWEIGHT\b|\bOUTPERFORM\b", t):
        return "buy", "BUY"
    if re.search(r"\bHOLD\b|\bNEUTRAL\b|\bEQUAL[\s_-]*WEIGHT\b|\bMARKET[\s_-]*PERFORM\b", t):
        return "hold", "HOLD"
    if re.search(r"\bSELL\b|\bREDUCE\b|\bUNDERWEIGHT\b|\bUNDERPERFORM\b", t):
        return "sell", "SELL"
    return None, None


def _detect_date(text: str) -> str | None:
    # 21-May-24 / 21-May-2024 / 12-Aug-24
    m = re.search(
        r"\b(\d{1,2})[-_\s](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-_\s](\d{2,4})\b",
        text,
        re.I,
    )
    if m:
        d, mon, y = int(m.group(1)), m.group(2)[:3].lower(), m.group(3)
        year = int(y)
        if year < 100:
            year += 2000
        month = _MONTHS.get(mon, 1)
        try:
            return f"{year:04d}-{month:02d}-{d:02d}"
        except Exception:
            return None
    # 21/05/2024
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _extract_company(text: str, broker: str | None) -> tuple[str | None, list[str]]:
    """Heuristic company name from 'sees X% in COMPANY' or 'on COMPANY' patterns."""
    # Strip extension / telegram prefixes
    name = Path(text).stem
    name = re.sub(r"^tg_\d+_", "", name)
    name = re.sub(r"^\d+_", "", name)
    name = name.replace("_", " ")

    company = None
    patterns = [
        r"sees\s+\d+\s*%?\s*(?:UPSIDE|DOWNSIDE)\s+in\s+(.+?)(?:\s*[-–.].*|\s*$)",
        r"(?:Initiating Coverage|IPO Note|Update|Preview)\s+on\s+(.+?)(?:\s+with\b|\s*[-–.].*|\s*$)",
        r"\bin\s+([A-Z][A-Za-z0-9 &.]+?(?:Ltd|Limited|Bank|Industries|Finance|Cement|Systems|Realty|Zinc)?)\b",
    ]
    for pat in patterns:
        m = re.search(pat, name, re.I)
        if m:
            company = m.group(1).strip(" -_.")
            break

    if company:
        # Drop trailing noise
        company = re.sub(r"\s+(Strong|Healthy|Robust|Decent|Resilient|Not|to be).*$", "", company, flags=re.I)
        company = re.sub(r"\s+", " ", company).strip(" -_")

    # Remove broker words from company if leaked
    if company and broker:
        company = re.sub(re.escape(broker), "", company, flags=re.I).strip(" -_")

    keywords: list[str] = []
    if company:
        cleaned = re.sub(
            r"\b(limited|ltd\.?|pvt\.?|private|corporation)\b",
            "",
            company,
            flags=re.I,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        keywords.append(cleaned.lower())
        for tok in re.split(r"[^A-Za-z0-9]+", cleaned):
            if len(tok) >= 4:
                keywords.append(tok.lower())
    # Dedupe
    seen: set[str] = set()
    kw: list[str] = []
    for k in keywords:
        if k and k not in seen:
            seen.add(k)
            kw.append(k)
    return company, kw


def parse_report_metadata(
    file_name: str,
    *,
    caption: str | None = None,
    message_id: int | None = None,
    message_date: str | None = None,
) -> dict[str, Any]:
    blob = f"{file_name} {caption or ''}"
    broker = _detect_broker(blob)
    report_type = _detect_report_type(blob)
    rating, rating_detail = _detect_rating(blob)
    report_date = _detect_date(blob) or message_date
    company, keywords = _extract_company(file_name, broker)
    if caption and not company:
        company, keywords = _extract_company(caption, broker)

    entry_id = f"tg:{message_id}" if message_id is not None else f"file:{file_name}"
    return {
        "id": entry_id,
        "message_id": message_id,
        "file_name": file_name,
        "broker": broker,
        "company": company,
        "company_keywords": keywords,
        "report_type": report_type,
        "report_date": report_date,
        "rating": rating,
        "rating_detail": rating_detail,
        "caption": (caption or "")[:300] or None,
        "keywords": _build_keyword_list(broker, company, keywords, report_type, rating),
    }


def _build_keyword_list(
    broker: str | None,
    company: str | None,
    company_kw: list[str],
    report_type: str,
    rating: str | None,
) -> list[str]:
    out: list[str] = []
    for x in ([broker, company, report_type, rating] + list(company_kw)):
        if not x:
            continue
        s = str(x).strip().lower()
        if s and s not in out:
            out.append(s)
    return out


def upsert_entry(index: dict[str, Any], entry: dict[str, Any]) -> None:
    entries: list[dict[str, Any]] = index.setdefault("entries", [])
    eid = entry.get("id")
    for i, existing in enumerate(entries):
        if existing.get("id") == eid or (
            entry.get("message_id")
            and existing.get("message_id") == entry.get("message_id")
        ):
            entries[i] = {**existing, **entry}
            break
    else:
        entries.append(entry)
    mid = entry.get("message_id")
    if isinstance(mid, int) and mid > int(index.get("last_message_id") or 0):
        index["last_message_id"] = mid


def find_reports_for_company(company: str, *, index: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return index entries relevant to a company name (keyword / company field match)."""
    from iis.integrations.telegram_inbox import company_search_terms

    index = index or load_index()
    terms = [t.lower() for t in company_search_terms(company)]
    if not terms:
        return []
    hits: list[dict[str, Any]] = []
    for e in index.get("entries") or []:
        hay = " ".join(
            [
                str(e.get("company") or ""),
                " ".join(e.get("company_keywords") or []),
                str(e.get("file_name") or ""),
                " ".join(e.get("keywords") or []),
            ]
        ).lower()
        score = 0
        for t in terms:
            if len(t) >= 4 and t in hay:
                score += 2 if t in (e.get("company") or "").lower() else 1
            elif len(t) < 4 and re.search(rf"\b{re.escape(t)}\b", hay):
                score += 1
        if score:
            row = dict(e)
            row["_score"] = score
            hits.append(row)
    # Higher score first, then newer report_date
    hits.sort(
        key=lambda r: (int(r.get("_score") or 0), str(r.get("report_date") or "")),
        reverse=True,
    )
    return hits


async def _index_messages_async(*, limit: int | None = None, full: bool = False) -> dict[str, Any]:
    """Scan Telegram group PDFs and update the cross-ref index."""
    from iis.integrations.telegram_inbox import (
        _build_client,
        _doc_file_name,
        _is_pdf_document,
        _resolve_group_entity,
        telegram_session_exists,
    )

    summary: dict[str, Any] = {
        "ok": False,
        "scanned": 0,
        "pdfs": 0,
        "upserted": 0,
        "path": str(index_path()),
        "errors": [],
    }
    if not settings.telegram_group:
        summary["errors"].append("IIS_TELEGRAM_GROUP not set")
        return summary
    if not telegram_session_exists():
        summary["errors"].append("Not authorized — run: iis telegram auth")
        return summary

    index = load_index()
    last_id = 0 if full else int(index.get("last_message_id") or 0)
    client = _build_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            summary["errors"].append("Session expired — run: iis telegram auth")
            return summary
        entity = await client.get_entity(_resolve_group_entity(settings.telegram_group))
        # Newest first; stop when we pass last_id (incremental)
        max_scan = limit if limit is not None else (5000 if full else 400)
        async for msg in client.iter_messages(entity, limit=max_scan):
            summary["scanned"] += 1
            if not full and last_id and msg.id <= last_id:
                break
            if not _is_pdf_document(msg):
                continue
            summary["pdfs"] += 1
            fname = _doc_file_name(msg) or f"telegram_{msg.id}.pdf"
            caption = str(getattr(msg, "message", None) or "") or None
            msg_date = None
            try:
                if getattr(msg, "date", None):
                    msg_date = msg.date.astimezone(timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                pass
            entry = parse_report_metadata(
                fname,
                caption=caption,
                message_id=msg.id,
                message_date=msg_date,
            )
            upsert_entry(index, entry)
            summary["upserted"] += 1

        # Also index local inbox / known downloaded names without message ids
        inbox = settings.telegram_local_inbox
        if inbox.exists():
            for p in inbox.glob("*.pdf"):
                # Prefer message id prefix "12345_name.pdf"
                m = re.match(r"^(\d+)_(.+)$", p.name)
                mid = int(m.group(1)) if m else None
                entry = parse_report_metadata(p.name, message_id=mid)
                if mid is None:
                    entry["id"] = f"local:{p.name}"
                upsert_entry(index, entry)

        path = save_index(index)
        summary["ok"] = True
        summary["path"] = str(path)
        summary["entry_count"] = len(index.get("entries") or [])
        summary["last_message_id"] = index.get("last_message_id")
    except Exception as e:
        logger.exception("report index update failed")
        summary["errors"].append(str(e))
    finally:
        await client.disconnect()
    return summary


def update_report_index(*, limit: int | None = None, full: bool = False) -> dict[str, Any]:
    """Rebuild or incrementally update the Telegram report cross-ref index."""
    return asyncio.run(_index_messages_async(limit=limit, full=full))


def index_stats() -> dict[str, Any]:
    idx = load_index()
    return {
        "path": str(index_path()),
        "updated_at": idx.get("updated_at"),
        "entry_count": len(idx.get("entries") or []),
        "last_message_id": idx.get("last_message_id"),
        "group": idx.get("group"),
    }
