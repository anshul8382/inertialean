"""
India mutual fund NAV ingestion from AMFI (NAVAll.txt).

Link a ``Security`` via JSON ``meta_data``:
  ``amfi_scheme_code`` (string, required for sync)
  ``amfi_isin`` (optional)

Optional history backfill uses mfapi.in (third-party); verify suitability for production.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

AMFI_NAV_ALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
MFAPI_SCHEME_HISTORY_URL = "https://api.mfapi.in/mf/{scheme_code}/all"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_RAW_TEXT_CACHE: Dict[str, Any] = {"text": None, "fetched_at": 0.0}


@dataclass(frozen=True)
class AmfiNavRow:
    scheme_code: str
    scheme_name: str
    isin_div_growth: str
    isin_div_reinvestment: str
    nav: Decimal
    nav_date: date
    amc_name: str


def _strip_bom(text: str) -> str:
    return text.lstrip("\ufeff").strip()


def _parse_amfi_nav_date(raw: str) -> Optional[date]:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_mfapi_date(raw: str) -> Optional[date]:
    raw = (raw or "").strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_nav_decimal(raw: str) -> Optional[Decimal]:
    s = (raw or "").strip().upper()
    if not s or s in ("N.A.", "NA", "-", "--"):
        return None
    s = s.replace(",", "")
    try:
        return Decimal(s)
    except Exception:
        return None


def fetch_nav_all_text(
    *,
    url: str = AMFI_NAV_ALL_URL,
    timeout: int = 120,
    user_agent: str = DEFAULT_USER_AGENT,
) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def fetch_nav_all_text_cached(
    *,
    ttl_seconds: float = 600.0,
    url: str = AMFI_NAV_ALL_URL,
    timeout: int = 120,
    user_agent: str = DEFAULT_USER_AGENT,
) -> str:
    now = time.time()
    cached = _RAW_TEXT_CACHE.get("text")
    ts = float(_RAW_TEXT_CACHE.get("fetched_at") or 0.0)
    if cached and (now - ts) < ttl_seconds:
        return str(cached)
    text = fetch_nav_all_text(url=url, timeout=timeout, user_agent=user_agent)
    _RAW_TEXT_CACHE["text"] = text
    _RAW_TEXT_CACHE["fetched_at"] = now
    return text


def clear_nav_all_cache() -> None:
    _RAW_TEXT_CACHE["text"] = None
    _RAW_TEXT_CACHE["fetched_at"] = 0.0


def parse_nav_all_text(content: str) -> List[AmfiNavRow]:
    text = _strip_bom(content)
    rows: List[AmfiNavRow] = []
    current_amc = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        ll = line.lower()
        if ll.startswith("open ended") or ll.startswith("close ended"):
            continue
        parts = [p.strip() for p in line.split(";")]
        if not parts:
            continue
        if parts[0] == "Scheme Code":
            continue
        if len(parts) == 2 and parts[0].isdigit():
            current_amc = parts[1]
            continue
        if len(parts) >= 6 and parts[0].isdigit():
            scheme_code = parts[0]
            scheme_name = parts[1]
            isin_g = parts[2] if len(parts) > 2 else ""
            isin_r = parts[3] if len(parts) > 3 else ""
            nav_raw = parts[4] if len(parts) > 4 else ""
            nav = _parse_nav_decimal(nav_raw)
            date_raw = parts[-1] if len(parts) > 5 else ""
            nav_date = _parse_amfi_nav_date(date_raw)
            if nav is None or nav_date is None:
                continue
            rows.append(
                AmfiNavRow(
                    scheme_code=scheme_code,
                    scheme_name=scheme_name,
                    isin_div_growth=isin_g,
                    isin_div_reinvestment=isin_r,
                    nav=nav,
                    nav_date=nav_date,
                    amc_name=current_amc,
                )
            )
    return rows


def index_nav_rows_by_scheme_code(rows: Iterable[AmfiNavRow]) -> Dict[str, AmfiNavRow]:
    out: Dict[str, AmfiNavRow] = {}
    for r in rows:
        out[r.scheme_code] = r
    return out


def _security_meta_dict(meta_data: Optional[str]) -> Dict[str, Any]:
    if not meta_data:
        return {}
    try:
        data = json.loads(meta_data)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _dump_meta(meta: Dict[str, Any]) -> str:
    return json.dumps(meta, separators=(",", ":"), ensure_ascii=False)


def get_amfi_scheme_code(security: Any) -> Optional[str]:
    meta = _security_meta_dict(getattr(security, "meta_data", None))
    code = meta.get("amfi_scheme_code")
    if code is None:
        return None
    s = str(code).strip()
    return s or None


def search_schemes(rows: List[AmfiNavRow], query: str, limit: int = 50) -> List[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        hay = f"{r.scheme_name} {r.scheme_code} {r.amc_name}".lower()
        if q in hay:
            out.append(
                {
                    "scheme_code": r.scheme_code,
                    "scheme_name": r.scheme_name,
                    "amc_name": r.amc_name,
                    "isin_div_growth": r.isin_div_growth,
                    "nav": str(r.nav),
                    "nav_date": r.nav_date.isoformat(),
                }
            )
            if len(out) >= limit:
                break
    return out


def sync_daily_nav_for_linked_securities(
    *,
    db_session,
    Security: Any,
    HistoricalPrice: Any,
    rows: Optional[List[AmfiNavRow]] = None,
    fetch_fresh: bool = True,
) -> Dict[str, Any]:
    if rows is None:
        text = fetch_nav_all_text() if fetch_fresh else fetch_nav_all_text_cached()
        rows = parse_nav_all_text(text)
    by_code = index_nav_rows_by_scheme_code(rows)

    securities = (
        db_session.query(Security)
        .filter(Security.security_type.ilike("%mutual%fund%"))
        .filter(Security.meta_data.isnot(None))
        .all()
    )

    updated = 0
    skipped_no_code = 0
    skipped_no_nav = 0
    missing_codes: List[str] = []

    for sec in securities:
        code = get_amfi_scheme_code(sec)
        if not code:
            skipped_no_code += 1
            continue
        row = by_code.get(code)
        if not row:
            missing_codes.append(f"{sec.symbol}:{code}")
            skipped_no_nav += 1
            continue

        close_price = row.nav
        hp = (
            db_session.query(HistoricalPrice)
            .filter_by(security_id=sec.id, date=row.nav_date)
            .first()
        )
        if hp:
            hp.close_price = close_price
            hp.source = "amfi"
            hp.confidence = Decimal("1.00")
        else:
            db_session.add(
                HistoricalPrice(
                    security_id=sec.id,
                    date=row.nav_date,
                    close_price=close_price,
                    source="amfi",
                    confidence=Decimal("1.00"),
                )
            )
        sec.current_price = close_price
        sec.last_updated = datetime.utcnow()
        updated += 1

    return {
        "parsed_nav_rows": len(rows),
        "distinct_scheme_codes": len(by_code),
        "securities_scanned": len(securities),
        "historical_prices_updated": updated,
        "skipped_no_amfi_code": skipped_no_code,
        "skipped_scheme_not_in_file": skipped_no_nav,
        "missing_scheme_codes_sample": missing_codes[:25],
    }


def link_security_to_amfi_scheme(
    *,
    security: Any,
    amfi_scheme_code: str,
    amfi_isin: Optional[str] = None,
) -> Dict[str, Any]:
    code = str(amfi_scheme_code or "").strip()
    if not code:
        raise ValueError("amfi_scheme_code is required")
    meta = _security_meta_dict(getattr(security, "meta_data", None))
    meta["amfi_scheme_code"] = code
    if amfi_isin:
        meta["amfi_isin"] = str(amfi_isin).strip()
    security.meta_data = _dump_meta(meta)
    return {"amfi_scheme_code": code, "amfi_isin": meta.get("amfi_isin")}


def fetch_mfapi_nav_history(
    scheme_code: str,
    *,
    timeout: int = 120,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Tuple[List[Tuple[date, Decimal]], Optional[Dict[str, Any]]]:
    safe = urllib.parse.quote(str(scheme_code), safe="")
    url = MFAPI_SCHEME_HISTORY_URL.format(scheme_code=safe)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    meta = payload.get("meta") if isinstance(payload, dict) else None
    out: List[Tuple[date, Decimal]] = []
    for pair in (payload.get("data") if isinstance(payload, dict) else None) or []:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        d = _parse_mfapi_date(str(pair[0]))
        nav = _parse_nav_decimal(str(pair[1]))
        if d is not None and nav is not None:
            out.append((d, nav))
    return out, meta if isinstance(meta, dict) else None


def backfill_historical_from_mfapi(
    *,
    db_session,
    security: Any,
    HistoricalPrice: Any,
    replace_existing: bool = False,
) -> Dict[str, Any]:
    code = get_amfi_scheme_code(security)
    if not code:
        raise ValueError("Security has no meta_data.amfi_scheme_code")

    pairs, meta = fetch_mfapi_nav_history(code)
    inserted = 0
    updated = 0
    skipped = 0
    for d, nav in pairs:
        hp = db_session.query(HistoricalPrice).filter_by(security_id=security.id, date=d).first()
        if hp:
            if replace_existing:
                hp.close_price = nav
                hp.source = "mfapi.in"
                hp.confidence = Decimal("0.95")
                updated += 1
            else:
                skipped += 1
            continue
        db_session.add(
            HistoricalPrice(
                security_id=security.id,
                date=d,
                close_price=nav,
                source="mfapi.in",
                confidence=Decimal("0.95"),
            )
        )
        inserted += 1

    nav_by_date: Dict[date, Decimal] = {}
    for d, nav in pairs:
        nav_by_date[d] = nav
    if nav_by_date:
        latest_date = max(nav_by_date)
        security.current_price = nav_by_date[latest_date]
        security.last_updated = datetime.utcnow()

    return {
        "scheme_code": code,
        "rows_from_api": len(pairs),
        "historical_inserted": inserted,
        "historical_updated": updated,
        "historical_skipped_existing": skipped,
        "mfapi_meta_scheme_name": (meta or {}).get("scheme_name"),
    }


def find_row_by_scheme_code(rows: List[AmfiNavRow], scheme_code: str) -> Optional[AmfiNavRow]:
    code = str(scheme_code).strip()
    for r in rows:
        if r.scheme_code == code:
            return r
    return None


def ensure_mutual_fund_asset_class(db_session, AssetClass: Any, user_id: int) -> Any:
    ac = db_session.query(AssetClass).filter(AssetClass.name == "Mutual Fund").first()
    if ac:
        return ac
    ac = AssetClass(
        name="Mutual Fund",
        description="Mutual Fund securities",
        created_by=user_id,
    )
    db_session.add(ac)
    db_session.flush()
    return ac


def create_mutual_fund_security_from_amfi(
    *,
    db_session,
    Security: Any,
    AssetClass: Any,
    HistoricalPrice: Any,
    scheme_code: str,
    user_id: int,
    symbol: Optional[str] = None,
) -> Tuple[Any, AmfiNavRow]:
    """
    Create a ``Security`` (Mutual Fund) from AMFI ``scheme_code`` using the latest NAVAll.txt row.
    Also inserts the corresponding ``HistoricalPrice`` for that NAV date.
    """
    text = fetch_nav_all_text()
    rows = parse_nav_all_text(text)
    row = find_row_by_scheme_code(rows, scheme_code)
    if not row:
        raise ValueError(f"Scheme code {scheme_code} not found in latest AMFI NAV file")

    sym = (symbol or f"MF{scheme_code}").strip().upper()[:20]
    if db_session.query(Security).filter(Security.symbol == sym).first():
        raise ValueError(f"A security with symbol {sym} already exists")

    ac = ensure_mutual_fund_asset_class(db_session, AssetClass, user_id)
    name = row.scheme_name[:100]
    meta: Dict[str, Any] = {"amfi_scheme_code": row.scheme_code}
    if row.isin_div_growth:
        meta["amfi_isin"] = row.isin_div_growth.strip()

    sec = Security(
        symbol=sym,
        name=name,
        security_type="Mutual Fund",
        asset_class_id=ac.id,
        created_by=user_id,
        meta_data=_dump_meta(meta),
        current_price=row.nav,
        last_updated=datetime.utcnow(),
    )
    db_session.add(sec)
    db_session.flush()
    db_session.add(
        HistoricalPrice(
            security_id=sec.id,
            date=row.nav_date,
            close_price=row.nav,
            source="amfi",
            confidence=Decimal("1.00"),
        )
    )
    return sec, row
