"""Detect trade-statement format and parse bytes → TradeParseResult."""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

from services.reconciliation.trade_parsers.base import TradeParseResult
from services.reconciliation.trade_parsers.order_book_screenshot import (
    parse_order_book_screenshot,
)
from services.reconciliation.trade_parsers.zerodha_contract_note import (
    looks_like_zerodha_contract,
    parse_zerodha_contract_text,
)
from services.reconciliation.trade_parsers.zerodha_tradebook_xlsx import (
    looks_like_zerodha_tradebook_xlsx,
    parse_zerodha_tradebook_xlsx,
)

_DATE_IN_NAME = __import__("re").compile(
    r"(?P<d>\d{1,2})[-_/](?P<m>\d{1,2})[-_/](?P<y>20\d{2})"
)


def _guess_date_from_name(name: str) -> Optional[date]:
    m = _DATE_IN_NAME.search(name or "")
    if not m:
        return None
    try:
        return date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    except Exception:
        return None


def _extract_pdf_text(
    data: bytes, passwords: List[str]
) -> Tuple[str, Optional[str], Optional[str]]:
    """Return (text, password_used, fail_reason).

    ``fail_reason`` is ``password_required`` when the PDF is encrypted and none
    of the supplied passwords unlocked it; ``None`` on success / non-encrypted.
    """
    try:
        from io import BytesIO

        from PyPDF2 import PdfReader
    except ImportError:
        return "", None, None
    bio = BytesIO(data)
    try:
        reader = PdfReader(bio)
    except Exception:
        return "", None, None
    used = None
    encrypted = bool(getattr(reader, "is_encrypted", False))
    if encrypted:
        unlocked = False
        # Try empty password first (some brokers use blank user-pwd), then supplied
        candidates = [""] + [str(p) for p in (passwords or []) if str(p)]
        seen_pw = set()
        for pw in candidates:
            if pw in seen_pw:
                continue
            seen_pw.add(pw)
            try:
                bio2 = BytesIO(data)
                reader = PdfReader(bio2)
                if reader.decrypt(str(pw)) == 0:
                    continue
                used = str(pw) if pw else ""
                unlocked = True
                break
            except Exception:
                continue
        if not unlocked:
            return "", None, "password_required"
    try:
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        text = ""
    # Encrypted but decrypt "succeeded" with empty extract — usually wrong password
    if encrypted and not (text or "").strip():
        return "", used, "password_required"
    return text, used, None


def detect_and_parse_trade(
    data: bytes,
    *,
    filename: str = "",
    passwords: Optional[List[str]] = None,
) -> TradeParseResult:
    """
    Route bytes to a format-specific parser.

    Supported today (verified on Subhodeep Trade details Drive samples):
      - zerodha_contract_note (PDF)
      - zerodha_tradebook_xlsx
      - order_book_screenshot (Angel / ICICI) — OCR optional
    """
    name = filename or "trade.bin"
    low = name.lower()
    passwords = list(passwords or [])

    # Images → screenshot path
    if low.endswith((".jpg", ".jpeg", ".png", ".webp")) or (
        data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
    ):
        return parse_order_book_screenshot(data, source_name=name)

    # XLSX
    if low.endswith(".xlsx") or data[:2] == b"PK":
        # confirm sheet
        try:
            from io import BytesIO
            import openpyxl

            wb = openpyxl.load_workbook(BytesIO(data), data_only=True)
            rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
            if looks_like_zerodha_tradebook_xlsx(rows):
                return parse_zerodha_tradebook_xlsx(data, source_name=name)
        except Exception:
            pass
        # still try tradebook parser
        if low.endswith(".xlsx"):
            return parse_zerodha_tradebook_xlsx(data, source_name=name)

    # PDF / unnamed PDF
    is_pdf = data[:5] == b"%PDF-" or low.endswith(".pdf")
    if is_pdf or data[:5] == b"%PDF-":
        text, used_pw, fail = _extract_pdf_text(data, passwords)
        if fail == "password_required":
            return TradeParseResult(
                format_id="pdf_password_required",
                warnings=[
                    "PDF is password-protected. Saved identity did not unlock it — enter the password."
                ],
                meta={"password_required": True, "tried_password_count": len(passwords)},
            )
        if not text.strip():
            return TradeParseResult(
                format_id="pdf_unreadable",
                warnings=[
                    "PDF has no extractable text (scanned/image-only or bad password)"
                ],
                meta={"password_used": bool(used_pw)},
            )
        # SOA (holdings positions) — not a trade fill source
        flat_u = "".join((text or "").upper().split())
        if "STATEMENTOFACCOUNTSOFSECURITIES" in flat_u:
            return TradeParseResult(
                format_id="zerodha_holdings_soa",
                fills=[],
                warnings=[
                    "Zerodha holdings SOA detected — not a trade contract note; skipped for fills"
                ],
                meta={"text_len": len(text)},
            )
        if looks_like_zerodha_contract(text, name):
            return parse_zerodha_contract_text(
                text,
                source_name=name,
                trade_date=_guess_date_from_name(name),
            )
        # fallback: still try Zerodha order-line parser
        return parse_zerodha_contract_text(
            text, source_name=name, trade_date=_guess_date_from_name(name)
        )

    return TradeParseResult(
        format_id="unknown",
        warnings=[f"Unsupported trade file type: {name}"],
    )
