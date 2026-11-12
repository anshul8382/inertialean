"""Read prices + listed-company universe from the lean server's Google Sheet.

Sheet layout (same one `enhanced_price_update_cron.py` reads):
  Prices tab: Symbol | Price          (GOOGLEFINANCE-fed, NSE symbols)
  Stocks tab: Symbol | Company Name | Sector | Exchange
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from iis.config import settings

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_BAD_CELLS = {
    "#N/A", "#N/A N/A", "#NA", "#DIV/0!", "#REF!", "#NAME?", "#NULL!", "#VALUE!",
    "#NUM!", "N/A", "", "-", "LOADING...",
}
_SUFFIX_RE = re.compile(
    r"\b(limited|ltd|pvt|private|corporation|corp|company|co|inc|plc|india)\b\.?", re.I
)

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "prices": {}, "universe": []}


@dataclass
class ListedCompany:
    symbol: str
    name: str
    sector: str
    exchange: str

    @property
    def base_name(self) -> str:
        return base_company_name(self.name)


def base_company_name(name: str) -> str:
    """'Apollo Tyres Limited' → 'apollo tyres' (for name matching)."""
    text = _SUFFIX_RE.sub(" ", (name or "").lower())
    text = re.sub(r"[^a-z0-9&]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    path = settings.price_sheet_service_account_path
    if not path or not path.exists():
        raise FileNotFoundError(f"price sheet service account missing: {path}")
    creds = Credentials.from_service_account_file(str(path), scopes=_SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _parse_price(raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("₹", "")
    if text.upper() in _BAD_CELLS:
        return None
    try:
        val = float(text)
    except ValueError:
        return None
    if val <= 0 or val > 1_000_000:
        return None
    return val


def _read_tab(svc, tab: str) -> list[list[str]]:
    resp = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=settings.price_sheet_id, range=f"'{tab}'!A:F")
        .execute()
    )
    return resp.get("values", []) or []


def _header_index(header: list[str], *names: str) -> int | None:
    low = [h.strip().lower() for h in header]
    for n in names:
        if n in low:
            return low.index(n)
    return None


def _refresh(force: bool = False) -> None:
    now = time.time()
    if not force and _cache["prices"] and now - _cache["at"] < settings.price_sheet_cache_sec:
        return
    svc = _service()
    prices: dict[str, float] = {}
    rows = _read_tab(svc, settings.price_sheet_prices_tab)
    if rows:
        si = _header_index(rows[0], "symbol")
        pi = _header_index(rows[0], "price")
        if si is not None and pi is not None:
            for r in rows[1:]:
                if len(r) <= max(si, pi):
                    continue
                sym = r[si].strip().upper()
                price = _parse_price(r[pi])
                if sym and price:
                    prices[sym] = price

    universe: list[ListedCompany] = []
    try:
        urows = _read_tab(svc, settings.price_sheet_universe_tab)
    except Exception as exc:
        logger.info("price sheet universe tab unreadable: %s", exc)
        urows = []
    if urows:
        si = _header_index(urows[0], "symbol")
        ni = _header_index(urows[0], "company name", "name", "name of company")
        sec = _header_index(urows[0], "sector")
        ex = _header_index(urows[0], "exchange")
        if si is not None and ni is not None:
            for r in urows[1:]:
                if len(r) <= max(si, ni):
                    continue
                universe.append(
                    ListedCompany(
                        symbol=r[si].strip().upper(),
                        name=r[ni].strip(),
                        sector=(r[sec].strip() if sec is not None and len(r) > sec else ""),
                        exchange=(r[ex].strip() if ex is not None and len(r) > ex else ""),
                    )
                )
    _cache.update({"at": now, "prices": prices, "universe": universe})


def _ensure(force: bool = False) -> bool:
    if not settings.price_sheet_enabled:
        return False
    with _lock:
        try:
            _refresh(force=force)
            return True
        except Exception as exc:
            logger.warning("price sheet read failed: %s", exc)
            return bool(_cache["prices"])


def sheet_price(symbol: str | None, *, force: bool = False) -> float | None:
    if not symbol or not _ensure(force=force):
        return None
    return _cache["prices"].get(symbol.strip().upper())


def listed_universe() -> list[ListedCompany]:
    if not _ensure():
        return []
    return list(_cache["universe"])


def find_listed(name_or_symbol: str | None) -> ListedCompany | None:
    """Exact symbol, then exact base-name match. No fuzzy guessing."""
    if not name_or_symbol:
        return None
    q = name_or_symbol.strip()
    uni = listed_universe()
    sym = q.upper()
    for c in uni:
        if c.symbol == sym:
            return c
    base = base_company_name(q)
    if not base:
        return None
    for c in uni:
        if c.base_name == base:
            return c
    return None


def sheet_status() -> dict[str, Any]:
    ok = _ensure()
    return {
        "enabled": settings.price_sheet_enabled,
        "ok": ok,
        "symbols": len(_cache["prices"]),
        "universe": len(_cache["universe"]),
        "cached_at": _cache["at"] or None,
    }
