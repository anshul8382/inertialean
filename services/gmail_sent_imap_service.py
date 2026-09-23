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
                if not symbol and not company:
                    continue
                amount = _parse_money(value_s)
                if amount == 0:
                    amount = _parse_money(qty_s) * _parse_money(price_s)
                lines_out.append(
                    {
                        "action": action,
                        "symbol": symbol,
                        "name": company,
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
                date_hdr = msg.get("Date")
                advice_date: Optional[date] = None
                try:
                    if date_hdr:
                        advice_date = parsedate_to_datetime(date_hdr).date()
                except Exception:
                    advice_date = None
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
