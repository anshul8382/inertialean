"""
Read Gmail Sent via IMAP (App Password) for Portfolio Investment Recos emails.

Uses stdlib imaplib only — same MAIL_USERNAME / MAIL_PASSWORD as SMTP.
"""
from __future__ import annotations

import email
import imaplib
import logging
import os
import re
from datetime import date, datetime
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

RECO_SUBJECT_MARKER = "Portfolio Investment Recos"


def _env(key: str, default: str = "") -> str:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            val = current_app.config.get(key)
            if val:
                return str(val)
    except Exception:
        pass
    return os.environ.get(key) or os.environ.get(key.replace("MAIL_", "SMTP_"), default) or default


def _decode_header_value(raw: Optional[str]) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _extract_email_address(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", raw)
    return m.group(0).lower() if m else None


def _message_body(msg: email.message.Message) -> Tuple[str, str]:
    """Return (html, text)."""
    html_parts: List[str] = []
    text_parts: List[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ctype == "text/html":
                html_parts.append(text)
            elif ctype == "text/plain":
                text_parts.append(text)
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace") if payload else ""
        except Exception:
            text = str(msg.get_payload() or "")
        if (msg.get_content_type() or "").lower() == "text/html":
            html_parts.append(text)
        else:
            text_parts.append(text)
    return "\n".join(html_parts), "\n".join(text_parts)


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n", text)
    text = re.sub(r"(?is)</tr>", "\n", text)
    text = re.sub(r"(?is)</td>", "\t", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _parse_money(token: str) -> float:
    t = re.sub(r"[₹,\s]", "", token or "")
    t = t.replace("−", "-")
    try:
        return float(t)
    except Exception:
        return 0.0


def parse_reco_email_body(body: str) -> Dict[str, Any]:
    """
    Parse Buy/Sell tables from recommendation email HTML or plain text.
    Returns nature, products, symbols, lines.
    """
    if not body:
        return {"nature": "", "products": "", "symbols": [], "lines": []}

    plain = body if "<" not in body[:200] else _strip_html(body)
    lines_out: List[Dict[str, Any]] = []
    symbols: List[str] = []

    # Prefer structured HTML table parse
    if "<table" in body.lower():
        # Split into Buy / Sell sections via section cell text
        sections = re.split(r"(?is)>\s*(Buy|Sell)\s*<", body)
        # sections like [preamble, 'Buy', buy_chunk, 'Sell', sell_chunk, ...]
        i = 1
        while i + 1 < len(sections):
            action = sections[i].strip().lower()
            chunk = sections[i + 1]
            i += 2
            if action not in ("buy", "sell"):
                continue
            # Rows: Company | Symbol | Qty | Price | Value
            for row_m in re.finditer(r"(?is)<tr[^>]*>(.*?)</tr>", chunk):
                cells = re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", row_m.group(1))
                cells_txt = [_strip_html(c).strip() for c in cells]
                if len(cells_txt) < 5:
                    continue
                if cells_txt[0].lower() in ("company", "buy", "sell") or "total" in cells_txt[0].lower():
                    continue
                if "total" in " ".join(cells_txt).lower():
                    continue
                company, symbol, qty_s, price_s, value_s = cells_txt[:5]
                symbol = symbol.strip()
                # Tables often show "NSE:AFFLE" — keep ticker only
                if ":" in symbol:
                    symbol = symbol.split(":")[-1].strip()
                symbol = symbol.upper()
                if not symbol and not company:
                    continue
                qty = _parse_money(qty_s)
                amount = _parse_money(value_s)
                if amount == 0:
                    amount = qty * _parse_money(price_s)
                lines_out.append(
                    {
                        "action": action,
                        "symbol": symbol,
                        "name": company,
                        "quantity": qty if qty else None,
                        "amount": amount,
                        "asset_class": "Equity",  # email tables are equity trades; debt rare in HTML
                    }
                )
                if symbol:
                    symbols.append(symbol)

    if not lines_out:
        # Plain-text fallback: look for BUY:/SELL: style or symbol lines
        current = None
        for line in plain.splitlines():
            low = line.strip().lower()
            if low == "buy" or low.startswith("buy "):
                current = "buy"
                continue
            if low == "sell" or low.startswith("sell "):
                current = "sell"
                continue
            if current and "\t" in line:
                parts = [p.strip() for p in line.split("\t") if p.strip()]
                if len(parts) >= 2:
                    company = parts[0]
                    symbol = parts[1] if len(parts) > 1 else ""
                    value = _parse_money(parts[-1]) if parts else 0
                    lines_out.append(
                        {
                            "action": current,
                            "symbol": symbol,
                            "name": company,
                            "amount": value,
                            "asset_class": "Equity",
                        }
                    )
                    if symbol:
                        symbols.append(symbol)

    from services.advisory_register_service import format_nature_and_products

    nature, products = format_nature_and_products(lines_out)
    return {
        "nature": nature,
        "products": products,
        "symbols": symbols,
        "lines": lines_out,
    }


def _imap_login() -> imaplib.IMAP4_SSL:
    user = (_env("MAIL_USERNAME") or "").strip()
    password = (_env("MAIL_PASSWORD") or _env("SMTP_PASSWORD") or "").strip()
    if not user or not password:
        raise RuntimeError(
            "MAIL_USERNAME and MAIL_PASSWORD required for Gmail IMAP Sent import"
        )
    conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    conn.login(user, password)
    return conn


def _select_sent(conn: imaplib.IMAP4_SSL) -> str:
    for name in ('"[Gmail]/Sent Mail"', '"[Gmail]/Sent"', "Sent", '"Sent Mail"'):
        typ, _ = conn.select(name, readonly=True)
        if typ == "OK":
            return name
    # List and find Sent
    typ, boxes = conn.list()
    if typ == "OK" and boxes:
        for raw in boxes:
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            if "sent" in line.lower():
                m = re.search(r'"([^"]+)"\s*$', line)
                if m:
                    folder = f'"{m.group(1)}"'
                    typ2, _ = conn.select(folder, readonly=True)
                    if typ2 == "OK":
                        return folder
    raise RuntimeError("Could not select Gmail Sent folder — enable IMAP in Gmail settings")


def _all_recipient_addresses(msg: email.message.Message) -> List[str]:
    """Lowercased emails from To / Cc / Bcc headers."""
    found: List[str] = []
    seen = set()
    for hdr in ("To", "Cc", "Bcc"):
        raw = _decode_header_value(msg.get(hdr))
        if not raw:
            continue
        for m in re.finditer(r"[\w.+-]+@[\w.-]+\.\w+", raw):
            addr = m.group(0).lower()
            if addr not in seen:
                seen.add(addr)
                found.append(addr)
    return found


def message_matches_addresses(
    msg: email.message.Message, addresses: List[str]
) -> bool:
    """True if any To/Cc/Bcc address is in the target set."""
    targets = {a.strip().lower() for a in addresses if a and a.strip()}
    if not targets:
        return False
    return bool(targets.intersection(_all_recipient_addresses(msg)))


def _parse_msg_date(msg: email.message.Message) -> Optional[date]:
    date_hdr = msg.get("Date")
    try:
        if date_hdr:
            return parsedate_to_datetime(date_hdr).date()
    except Exception:
        return None
    return None


def _imap_recipient_or_clause(addresses: List[str]) -> str:
    """
    Build IMAP OR chain for TO/CC matching any address.
    Example: (OR (OR TO "a@x.com" CC "a@x.com") (OR TO "b@y.com" CC "b@y.com"))
    """
    parts: List[str] = []
    for addr in addresses:
        a = addr.strip().lower()
        if not a or "@" not in a:
            continue
        # Quote for IMAP string
        q = a.replace('\\', '\\\\').replace('"', '\\"')
        parts.append(f'(OR TO "{q}" CC "{q}")')
    if not parts:
        return ""
    clause = parts[0]
    for p in parts[1:]:
        clause = f"(OR {clause} {p})"
    return clause


def _fetch_rfc822(conn: imaplib.IMAP4_SSL, num: bytes) -> Optional[bytes]:
    typ, msg_data = conn.fetch(num, "(RFC822)")
    if typ != "OK" or not msg_data or not msg_data[0]:
        return None
    raw = msg_data[0][1]
    if not isinstance(raw, (bytes, bytearray)):
        return None
    return bytes(raw)


def _row_from_raw(
    raw: bytes,
    *,
    num: bytes,
    since: date,
    before: date,
    targets: List[str],
) -> Optional[Dict[str, Any]]:
    msg = email.message_from_bytes(raw)
    if targets and not message_matches_addresses(msg, targets):
        return None
    advice_date = _parse_msg_date(msg)
    if advice_date is None:
        return None
    if advice_date < since or advice_date >= before:
        return None
    subject = _decode_header_value(msg.get("Subject"))
    html, text = _message_body(msg)
    mid = (msg.get("Message-ID") or "").strip() or (
        f"imap-{num.decode() if isinstance(num, bytes) else num}"
    )
    to_raw = _decode_header_value(msg.get("To"))
    cc_raw = _decode_header_value(msg.get("Cc"))
    return {
        "gmail_message_id": mid,
        "subject": subject,
        "sent_at": advice_date.isoformat(),
        "advice_date": advice_date,
        "to_email": _extract_email_address(to_raw),
        "to_raw": to_raw,
        "cc_raw": cc_raw,
        "recipient_emails": _all_recipient_addresses(msg),
        "html": html,
        "text": text,
        "raw_rfc822": raw,
        "source": "imap_sent",
    }


def fetch_sent_to_addresses(
    *,
    addresses: List[str],
    since: date,
    before: date,
    max_messages: int = 150,
) -> List[Dict[str, Any]]:
    """
    Fetch Sent messages whose To/Cc includes any of ``addresses``
    between since (inclusive) and before (exclusive).

    Uses IMAP TO/CC search (not full-mailbox download). Caps full-body fetches
    at ``max_messages`` so the request cannot hang for hours.
    """
    targets = sorted({a.strip().lower() for a in addresses if a and "@" in a})
    if not targets:
        return []

    conn = _imap_login()
    # Avoid indefinite hang on a dead IMAP socket
    try:
        conn.sock.settimeout(90)
    except Exception:
        pass
    results: List[Dict[str, Any]] = []
    try:
        _select_sent(conn)
        since_s = since.strftime("%d-%b-%Y")
        before_s = before.strftime("%d-%b-%Y")
        recip = _imap_recipient_or_clause(targets)
        ids: List[bytes] = []
        if recip:
            criteria = f'(SINCE {since_s} BEFORE {before_s} {recip})'
            typ, data = conn.search(None, criteria)
            if typ == "OK" and data and data[0]:
                ids = data[0].split()
                logger.info(
                    "IMAP Sent TO/CC search: %s hit(s) for %s (%s → %s)",
                    len(ids),
                    targets,
                    since_s,
                    before_s,
                )
        if not ids:
            # Fallback: date window only, but HEADER peek first (never bulk RFC822)
            typ, data = conn.search(None, f"(SINCE {since_s} BEFORE {before_s})")
            if typ != "OK" or not data or not data[0]:
                return []
            candidate_ids = data[0].split()
            logger.warning(
                "IMAP TO/CC search empty; header-scanning %s Sent msg(s) (cap %s)",
                len(candidate_ids),
                max_messages * 20,
            )
            # Newest first
            candidate_ids = list(reversed(candidate_ids))
            matched: List[bytes] = []
            scan_cap = min(len(candidate_ids), max(max_messages * 20, 400))
            for i, num in enumerate(candidate_ids[:scan_cap]):
                try:
                    typ, msg_data = conn.fetch(
                        num, "(BODY.PEEK[HEADER.FIELDS (TO CC BCC SUBJECT DATE MESSAGE-ID)])"
                    )
                    if typ != "OK" or not msg_data:
                        continue
                    # msg_data is list of tuples; header bytes in [0][1]
                    hdr = b""
                    for part in msg_data:
                        if isinstance(part, tuple) and len(part) >= 2 and part[1]:
                            hdr = part[1]
                            break
                    if not hdr:
                        continue
                    msg = email.message_from_bytes(hdr)
                    if message_matches_addresses(msg, targets):
                        matched.append(num)
                        if len(matched) >= max_messages:
                            break
                except Exception as exc:
                    logger.warning("Skip IMAP header %s: %s", num, exc)
                if i and i % 100 == 0:
                    logger.info("IMAP header scan progress %s/%s", i, scan_cap)
            ids = matched

        # Prefer newest
        ids = list(reversed(ids))[: int(max_messages)]
        for i, num in enumerate(ids):
            try:
                raw = _fetch_rfc822(conn, num)
                if not raw:
                    continue
                row = _row_from_raw(
                    raw, num=num, since=since, before=before, targets=targets
                )
                if row:
                    results.append(row)
            except Exception as exc:
                logger.warning("Skip IMAP message %s: %s", num, exc)
            if i and i % 25 == 0:
                logger.info("IMAP body fetch progress %s/%s", i, len(ids))
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    logger.info(
        "IMAP Sent fetch done: %s message(s) for %s", len(results), targets
    )
    return results


def fetch_portfolio_reco_sent(
    *,
    since: date,
    before: date,
) -> List[Dict[str, Any]]:
    """
    Fetch Sent messages with subject containing Portfolio Investment Recos
    between since (inclusive) and before (exclusive).
    """
    conn = _imap_login()
    results: List[Dict[str, Any]] = []
    try:
        _select_sent(conn)
        # IMAP BEFORE is exclusive; SINCE inclusive
        since_s = since.strftime("%d-%b-%Y")
        before_s = before.strftime("%d-%b-%Y")
        criteria = f'(SINCE {since_s} BEFORE {before_s} SUBJECT "{RECO_SUBJECT_MARKER}")'
        typ, data = conn.search(None, criteria)
        if typ != "OK" or not data or not data[0]:
            # Fallback: date-only search then filter subject locally (SUBJECT can be flaky)
            typ, data = conn.search(None, f"(SINCE {since_s} BEFORE {before_s})")
            if typ != "OK" or not data or not data[0]:
                return []
            ids = data[0].split()
        else:
            ids = data[0].split()

        for num in ids:
            try:
                typ, msg_data = conn.fetch(num, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                subject = _decode_header_value(msg.get("Subject"))
                if RECO_SUBJECT_MARKER.lower() not in subject.lower():
                    continue
                advice_date = _parse_msg_date(msg)
                if advice_date is None:
                    continue
                if advice_date < since or advice_date >= before:
                    continue

                html, text = _message_body(msg)
                mid = (msg.get("Message-ID") or "").strip() or f"imap-{num.decode() if isinstance(num, bytes) else num}"
                to_raw = _decode_header_value(msg.get("To"))
                results.append(
                    {
                        "gmail_message_id": mid,
                        "subject": subject,
                        "advice_date": advice_date,
                        "to_email": _extract_email_address(to_raw),
                        "to_raw": to_raw,
                        "html": html,
                        "text": text,
                    }
                )
            except Exception as exc:
                logger.warning("Skip IMAP message %s: %s", num, exc)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return results
