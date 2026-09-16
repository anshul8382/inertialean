"""
Firm-wide price-finding suppressions via Google Sheets verification.

Detect stays DB-only (price accuracy scan).
This module: Python fetches spreadsheet closes, compares to DB within tolerance,
writes a shared JSON so the same symbol/date is not re-surfaced for every client.

LLM never fetches prices — it may only read the resulting open findings / JSON later.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

DEFAULT_TOLERANCE_PCT = 0.5  # user policy: 0.5%


def _dir() -> Path:
    root = Path(__file__).resolve().parent.parent / "var" / "price_accuracy"
    root.mkdir(parents=True, exist_ok=True)
    return root


def suppressions_path() -> Path:
    return _dir() / "sheet_verified_suppressions.json"


def _tolerance_ratio(rel_tol: Optional[float] = None) -> float:
    if rel_tol is not None:
        return float(rel_tol)
    try:
        from flask import has_app_context, current_app

        if has_app_context() and current_app:
            v = current_app.config.get("PRICE_ACCURACY_SHEETS_MATCH_TOLERANCE_PCT")
            if v is not None:
                return float(v) / 100.0
    except Exception:
        pass
    env = os.environ.get("PRICE_ACCURACY_SHEETS_MATCH_TOLERANCE_PCT")
    if env:
        return float(env) / 100.0
    return DEFAULT_TOLERANCE_PCT / 100.0


def load_suppressions() -> Dict[str, Any]:
    path = suppressions_path()
    if not path.is_file():
        return {
            "schema_version": 1,
            "tolerance_pct": DEFAULT_TOLERANCE_PCT,
            "updated_at": None,
            "entries": {},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        data.setdefault("entries", {})
        data.setdefault("schema_version", 1)
        return data
    except Exception as exc:
        logger.warning("Failed reading sheet suppressions JSON: %s", exc)
        return {
            "schema_version": 1,
            "tolerance_pct": DEFAULT_TOLERANCE_PCT,
            "updated_at": None,
            "entries": {},
        }


def save_suppressions(data: Dict[str, Any]) -> Path:
    data = dict(data)
    data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    path = suppressions_path()
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return path


def _to_date(value: Any) -> Optional[date]:
    """Normalize ORM date/datetime/str to calendar date for ack matching."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _finding_attr(finding: Any, name: str) -> Any:
    if isinstance(finding, dict):
        return finding.get(name)
    return getattr(finding, name, None)


def suppression_key(
    *,
    kind: str,
    symbol: str,
    missing_date: Optional[str] = None,
    date_prev: Optional[str] = None,
    date_next: Optional[str] = None,
) -> str:
    kind_u = (kind or "").upper().strip()
    sym = (symbol or "").strip().upper()
    if kind_u == "SPIKE":
        return f"SPIKE|{sym}|{date_prev or ''}|{date_next or ''}"
    return f"{kind_u}|{sym}|{missing_date or ''}"


def is_finding_suppressed(finding: Any, *, pack: Optional[Dict[str, Any]] = None) -> bool:
    """True if this finding (ORM or dict-like) is in the firm-wide suppress JSON."""
    pack = pack or load_suppressions()
    entries = pack.get("entries") or {}
    kind = getattr(finding, "kind", None) or (finding.get("kind") if isinstance(finding, dict) else None)
    symbol = getattr(finding, "symbol", None) or (finding.get("symbol") if isinstance(finding, dict) else None)
    if not kind or not symbol:
        return False
    kind_u = str(kind).upper()
    if kind_u == "SPIKE":
        dp = getattr(finding, "date_prev", None) or (finding.get("date_prev") if isinstance(finding, dict) else None)
        dn = getattr(finding, "date_next", None) or (finding.get("date_next") if isinstance(finding, dict) else None)
        key = suppression_key(
            kind="SPIKE",
            symbol=str(symbol),
            date_prev=str(dp)[:10] if dp else None,
            date_next=str(dn)[:10] if dn else None,
        )
    else:
        md = getattr(finding, "missing_date", None) or (
            finding.get("missing_date") if isinstance(finding, dict) else None
        )
        key = suppression_key(
            kind=kind_u,
            symbol=str(symbol),
            missing_date=str(md)[:10] if md else None,
        )
    return key in entries


def is_price_alert_closed(
    finding: Any,
    *,
    ack_keys: Optional[Set[Tuple[int, date, date]]] = None,
    pack: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    True when this finding must not be shown as an open alert.

    Closed if: SPIKE pair is in DB ack keys (date-normalized), or firm sheet-suppress JSON.
    """
    kind = str(_finding_attr(finding, "kind") or "").upper()
    if kind in ("MISSING", "ZERO"):
        md = _to_date(_finding_attr(finding, "missing_date"))
        if md is not None:
            try:
                from services.price_accuracy_holiday_service import is_closed_calendar_day

                if is_closed_calendar_day(md):
                    return True
            except Exception:
                if md.weekday() >= 5:
                    return True
    if kind == "SPIKE" and ack_keys:
        sid = _finding_attr(finding, "security_id")
        d1 = _to_date(_finding_attr(finding, "date_prev"))
        d2 = _to_date(_finding_attr(finding, "date_next"))
        if sid is not None and d1 and d2 and (int(sid), d1, d2) in ack_keys:
            return True
    return is_finding_suppressed(finding, pack=pack)


def add_suppression_entry(
    *,
    kind: str,
    symbol: str,
    security_id: Optional[int] = None,
    missing_date: Optional[str] = None,
    date_prev: Optional[str] = None,
    date_next: Optional[str] = None,
    reason: str,
    sheet_price: Optional[float] = None,
    sheet_price_prev: Optional[float] = None,
    sheet_price_next: Optional[float] = None,
    db_price: Optional[float] = None,
    db_price_prev: Optional[float] = None,
    db_price_next: Optional[float] = None,
    gap_pct: Optional[float] = None,
    pack: Optional[Dict[str, Any]] = None,
    persist: bool = False,
) -> str:
    pack = pack if pack is not None else load_suppressions()
    key = suppression_key(
        kind=kind,
        symbol=symbol,
        missing_date=missing_date,
        date_prev=date_prev,
        date_next=date_next,
    )
    pack.setdefault("entries", {})[key] = {
        "kind": (kind or "").upper(),
        "symbol": (symbol or "").strip().upper(),
        "security_id": security_id,
        "missing_date": missing_date,
        "date_prev": date_prev,
        "date_next": date_next,
        "reason": reason,
        "sheet_price": sheet_price,
        "sheet_price_prev": sheet_price_prev,
        "sheet_price_next": sheet_price_next,
        "db_price": db_price,
        "db_price_prev": db_price_prev,
        "db_price_next": db_price_next,
        "gap_pct": gap_pct,
        "verified_at": datetime.utcnow().isoformat() + "Z",
    }
    if persist:
        save_suppressions(pack)
    return key


def _within_tol(db_close: Optional[float], sheet_close: Optional[float], rel_tol: float) -> bool:
    if db_close is None or sheet_close is None:
        return False
    db_close = float(db_close)
    sheet_close = float(sheet_close)
    if db_close <= 0 or sheet_close <= 0:
        return False
    return abs(db_close - sheet_close) / db_close <= rel_tol


def _return_ratio(prev: Optional[float], nxt: Optional[float]) -> Optional[float]:
    if prev is None or nxt is None:
        return None
    prev_f = float(prev)
    nxt_f = float(nxt)
    if prev_f <= 0 or nxt_f <= 0:
        return None
    return (nxt_f - prev_f) / prev_f


def spike_matches_sheet_closes(
    db_prev: float,
    db_next: float,
    sheet_prev: float,
    sheet_next: float,
    rel_tol: float,
) -> bool:
    """
    A spike is a real market move (auto-close) when:
      - both DB closes match GOOGLEFINANCE within rel_tol, or
      - the two-day % move matches even if rupee levels differ (split-adjusted GF vs as-traded DB).

    Keep open when GF is flat (or a different move) while DB jumped — that is a data error.
    """
    if _within_tol(db_prev, sheet_prev, rel_tol) and _within_tol(db_next, sheet_next, rel_tol):
        return True
    db_ret = _return_ratio(db_prev, db_next)
    sh_ret = _return_ratio(sheet_prev, sheet_next)
    if db_ret is None or sh_ret is None:
        return False
    band = max(float(rel_tol), abs(db_ret) * 0.10)
    return abs(db_ret - sh_ret) <= band


def _gap_pct(db_close: float, sheet_close: float) -> float:
    if not db_close:
        return 0.0
    return abs(float(sheet_close) - float(db_close)) / float(db_close) * 100.0


def verify_and_suppress_from_sheets(
    *,
    kinds: Optional[List[str]] = None,
    rel_tol: Optional[float] = None,
    max_unique_lookups: int = 250,
    also_ack_spikes_in_db: bool = True,
    user_id: Optional[int] = None,
    after_finding_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    For latest price-accuracy run: verify open findings vs Google Sheets.

    Suppress (JSON) when:
      - SPIKE: both DB closes match sheet within tolerance
      - MISSING: sheet has no close (holiday) OR same-date DB close matches sheet within tolerance
      - ZERO: sheet has no usable close (noise); if sheet has a real price, keep open

    Does not invent prices. Does not use LLM. Nearby-day MISSING match is NOT used (too loose).
    """
    from models import HistoricalPrice, PriceAccuracyFinding, PriceAccuracyRun
    from services.google_sheets_historical_price import (
        GoogleSheetsHistoricalPrice,
        _sanitize_symbol,
    )
    from services.price_accuracy_service import acknowledge_spike, load_spike_ack_keys

    repo_sa = Path(__file__).resolve().parent.parent / "service_account.json"
    if not repo_sa.is_file():
        return {"ok": False, "error": "missing_service_account", "suppressed": 0}

    run = PriceAccuracyRun.query.order_by(PriceAccuracyRun.id.desc()).first()
    if not run:
        return {"ok": False, "error": "no_price_accuracy_run", "suppressed": 0}

    want = {(k or "").upper() for k in (kinds or ["SPIKE", "MISSING", "ZERO"])}
    findings = (
        PriceAccuracyFinding.query.filter(
            PriceAccuracyFinding.run_id == run.id,
            PriceAccuracyFinding.kind.in_(list(want)),
        )
        .order_by(PriceAccuracyFinding.id.asc())
        .all()
    )
    if after_finding_id is not None:
        findings = [f for f in findings if int(f.id) > int(after_finding_id)]

    pack = load_suppressions()
    tol = _tolerance_ratio(rel_tol)
    pack["tolerance_pct"] = tol * 100.0

    to_check: List[Any] = []
    skipped_already = 0
    spike_acks = load_spike_ack_keys() if "SPIKE" in want else set()

    for f in findings:
        if is_finding_suppressed(f, pack=pack):
            skipped_already += 1
            continue
        if (f.kind or "").upper() == "SPIKE" and f.date_prev and f.date_next:
            dp = _to_date(f.date_prev)
            dn = _to_date(f.date_next)
            if dp and dn and (int(f.security_id), dp, dn) in spike_acks:
                add_suppression_entry(
                    kind="SPIKE",
                    symbol=f.symbol,
                    security_id=int(f.security_id),
                    date_prev=str(f.date_prev)[:10],
                    date_next=str(f.date_next)[:10],
                    reason="already_acked_in_db",
                    pack=pack,
                )
                skipped_already += 1
                continue
        to_check.append(f)

    pairs: List[Tuple[str, date]] = []
    seen: Set[Tuple[str, str]] = set()

    def add_pair(symbol: str, dt: Optional[date]) -> None:
        if not dt:
            return
        sym = _sanitize_symbol(symbol)
        if sym == "INVALID":
            return
        key = (sym, dt.strftime("%Y-%m-%d"))
        if key in seen:
            return
        if len(seen) >= max_unique_lookups:
            return
        seen.add(key)
        pairs.append((symbol, dt))

    for f in to_check:
        kind = (f.kind or "").upper()
        if kind == "SPIKE":
            add_pair(f.symbol, f.date_prev)
            add_pair(f.symbol, f.date_next)
        else:
            add_pair(f.symbol, f.missing_date)

    batch_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if pairs:
        wait = min(180.0, max(6.0, 4.0 + len(pairs) * 0.12))
        try:
            batch_map = GoogleSheetsHistoricalPrice.get_historical_price_batch(
                pairs, wait_seconds=wait
            )
        except Exception as exc:
            logger.warning("Sheet batch price fetch failed: %s", exc)
            return {
                "ok": False,
                "error": f"sheet_batch_failed:{exc}",
                "suppressed": 0,
                "run_id": run.id,
            }

    def sheet_price(symbol: str, dt: Optional[date]) -> Optional[float]:
        if not dt:
            return None
        sym = _sanitize_symbol(symbol)
        if sym == "INVALID":
            return None
        ds = dt.strftime("%Y-%m-%d")
        hit = batch_map.get((sym, ds))
        if hit and hit.get("price") is not None:
            try:
                p = float(hit["price"])
                return p if p > 0 else None
            except Exception:
                return None
        return None

    suppressed = 0
    kept_open = 0
    no_sheet = 0
    mismatch = 0
    capped_skipped = 0
    checked_keys: Set[str] = set()
    max_id_seen = int(after_finding_id or 0)
    batch_finding_ids: List[int] = []

    for f in to_check:
        try:
            fid = int(f.id)
        except Exception:
            continue
        kind = (f.kind or "").upper()
        if kind == "SPIKE":
            key = suppression_key(
                kind="SPIKE",
                symbol=f.symbol,
                date_prev=str(f.date_prev)[:10] if f.date_prev else None,
                date_next=str(f.date_next)[:10] if f.date_next else None,
            )
        else:
            key = suppression_key(
                kind=kind,
                symbol=f.symbol,
                missing_date=str(f.missing_date)[:10] if f.missing_date else None,
            )
        if key in checked_keys:
            continue

        if kind == "SPIKE":
            need = []
            if f.date_prev:
                need.append((_sanitize_symbol(f.symbol), f.date_prev.strftime("%Y-%m-%d")))
            if f.date_next:
                need.append((_sanitize_symbol(f.symbol), f.date_next.strftime("%Y-%m-%d")))
            if any(n not in seen for n in need):
                capped_skipped += 1
                continue
        else:
            if f.missing_date:
                n = (_sanitize_symbol(f.symbol), f.missing_date.strftime("%Y-%m-%d"))
                if n not in seen:
                    capped_skipped += 1
                    continue
        checked_keys.add(key)
        batch_finding_ids.append(fid)
        max_id_seen = max(max_id_seen, fid)

        if kind == "SPIKE":
            if f.close_prev is None or f.close_next is None or not f.date_prev or not f.date_next:
                kept_open += 1
                continue
            sp = sheet_price(f.symbol, f.date_prev)
            sn = sheet_price(f.symbol, f.date_next)
            if sp is None or sn is None:
                no_sheet += 1
                kept_open += 1
                continue
            dbp, dbn = float(f.close_prev), float(f.close_next)
            if spike_matches_sheet_closes(dbp, dbn, sp, sn, tol):
                gap = max(_gap_pct(dbp, sp), _gap_pct(dbn, sn))
                add_suppression_entry(
                    kind="SPIKE",
                    symbol=f.symbol,
                    security_id=int(f.security_id),
                    date_prev=str(f.date_prev)[:10],
                    date_next=str(f.date_next)[:10],
                    reason="sheet_matches_db",
                    sheet_price_prev=sp,
                    sheet_price_next=sn,
                    db_price_prev=dbp,
                    db_price_next=dbn,
                    gap_pct=round(gap, 4),
                    pack=pack,
                )
                if also_ack_spikes_in_db:
                    try:
                        acknowledge_spike(
                            security_id=int(f.security_id),
                            date_prev=f.date_prev,
                            date_next=f.date_next,
                            source="google_sheets_match",
                            user_id=user_id,
                            notes=f"Sheet vs DB within {tol * 100:.2g}% (auto)",
                        )
                    except Exception as exc:
                        logger.warning("spike ack failed sid=%s: %s", f.security_id, exc)
                suppressed += 1
            else:
                mismatch += 1
                kept_open += 1
            continue

        if kind == "MISSING":
            md = f.missing_date
            if not md:
                kept_open += 1
                continue
            sp = sheet_price(f.symbol, md)
            if sp is None:
                add_suppression_entry(
                    kind="MISSING",
                    symbol=f.symbol,
                    security_id=int(f.security_id),
                    missing_date=str(md)[:10],
                    reason="sheet_also_empty",
                    pack=pack,
                )
                suppressed += 1
                no_sheet += 1
                continue
            row = HistoricalPrice.query.filter_by(
                security_id=int(f.security_id), date=md
            ).first()
            dbp = float(row.close_price) if row and row.close_price is not None else None
            if dbp is not None and _within_tol(dbp, sp, tol):
                add_suppression_entry(
                    kind="MISSING",
                    symbol=f.symbol,
                    security_id=int(f.security_id),
                    missing_date=str(md)[:10],
                    reason="sheet_matches_db",
                    sheet_price=sp,
                    db_price=dbp,
                    gap_pct=round(_gap_pct(dbp, sp), 4),
                    pack=pack,
                )
                suppressed += 1
            else:
                # Real feed gap — keep open for symbol-level leftovers queue
                mismatch += 1
                kept_open += 1
            continue

        if kind == "ZERO":
            md = f.missing_date
            sp = sheet_price(f.symbol, md) if md else None
            if sp is None:
                add_suppression_entry(
                    kind="ZERO",
                    symbol=f.symbol,
                    security_id=int(f.security_id),
                    missing_date=str(md)[:10] if md else None,
                    reason="sheet_also_empty",
                    pack=pack,
                )
                suppressed += 1
                no_sheet += 1
            else:
                kept_open += 1
                mismatch += 1
            continue

        kept_open += 1

    path = save_suppressions(pack)
    if batch_finding_ids:
        max_id_seen = max(max_id_seen, max(batch_finding_ids))

    return {
        "ok": True,
        "run_id": run.id,
        "tolerance_pct": tol * 100.0,
        "findings_in_run": len(findings),
        "already_suppressed": skipped_already,
        "sheet_lookups": len(pairs),
        "suppressed": suppressed,
        "kept_open": kept_open,
        "no_sheet_data": no_sheet,
        "mismatch": mismatch,
        "capped_skipped": capped_skipped,
        "entries_total": len(pack.get("entries") or {}),
        "json_path": str(path),
        "after_finding_id": after_finding_id,
        "last_finding_id": max_id_seen,
        "batch_complete": capped_skipped == 0 and len(pairs) < max_unique_lookups,
    }


def cursor_path() -> Path:
    return _dir() / "sheet_suppress_cursor.json"


def load_cursor() -> Dict[str, Any]:
    path = cursor_path()
    if not path.is_file():
        return {"run_id": None, "last_finding_id": 0, "cycles": 0, "done": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"run_id": None, "last_finding_id": 0, "cycles": 0}
    except Exception:
        return {"run_id": None, "last_finding_id": 0, "cycles": 0, "done": False}


def save_cursor(data: Dict[str, Any]) -> Path:
    path = cursor_path()
    data = dict(data)
    data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return path


def leftovers_queue_path() -> Path:
    return _dir() / "symbol_leftovers_queue.json"


def run_sheet_suppress_cycle(
    *,
    batch_lookups: int = 80,
    rel_tol: Optional[float] = None,
    kinds: Optional[List[str]] = None,
    rebuild_leftovers_if_done: bool = True,
) -> Dict[str, Any]:
    """
    One overnight cycle: verify next batch of findings vs sheets, advance cursor.

    Schedule many cycles with a few minutes gap (Airflow). Safe to re-run.
    """
    from models import PriceAccuracyRun

    run = PriceAccuracyRun.query.order_by(PriceAccuracyRun.id.desc()).first()
    if not run:
        return {"ok": False, "error": "no_price_accuracy_run", "done": True}

    cursor = load_cursor()
    if cursor.get("run_id") != run.id:
        cursor = {
            "run_id": run.id,
            "last_finding_id": 0,
            "cycles": 0,
            "done": False,
        }

    if cursor.get("done"):
        leftovers = load_leftovers_queue() if rebuild_leftovers_if_done else {}
        return {
            "ok": True,
            "done": True,
            "message": "cursor already complete for this run",
            "run_id": run.id,
            "cycles": cursor.get("cycles"),
            "leftovers_count": len((leftovers or {}).get("symbols") or []),
        }

    # Priority order: process SPIKE/ZERO before MISSING by kinds per cycle
    kind_cycle = kinds or ["SPIKE", "ZERO", "MISSING"]
    result = verify_and_suppress_from_sheets(
        kinds=kind_cycle,
        rel_tol=rel_tol if rel_tol is not None else 0.005,
        max_unique_lookups=int(batch_lookups),
        after_finding_id=int(cursor.get("last_finding_id") or 0),
    )
    if not result.get("ok"):
        return result

    last_id = int(result.get("last_finding_id") or cursor.get("last_finding_id") or 0)
    lookups = int(result.get("sheet_lookups") or 0)
    capped = int(result.get("capped_skipped") or 0)
    # Done when this cycle found nothing left to fetch (all remaining already suppressed / none)
    done = lookups == 0 and capped == 0

    cursor["last_finding_id"] = max(last_id, int(cursor.get("last_finding_id") or 0))
    cursor["cycles"] = int(cursor.get("cycles") or 0) + 1
    cursor["done"] = done
    cursor["last_cycle"] = {
        "sheet_lookups": lookups,
        "suppressed": result.get("suppressed"),
        "kept_open": result.get("kept_open"),
        "capped_skipped": capped,
        "entries_total": result.get("entries_total"),
    }
    save_cursor(cursor)

    leftovers_summary = None
    if done and rebuild_leftovers_if_done:
        leftovers_summary = rebuild_symbol_leftovers_queue()

    return {
        "ok": True,
        "done": done,
        "run_id": run.id,
        "cycle": cursor["cycles"],
        "last_finding_id": last_id,
        "batch": {
            "sheet_lookups": result.get("sheet_lookups"),
            "suppressed": result.get("suppressed"),
            "kept_open": result.get("kept_open"),
            "mismatch": result.get("mismatch"),
            "capped_skipped": result.get("capped_skipped"),
            "entries_total": result.get("entries_total"),
        },
        "leftovers": leftovers_summary,
    }


def rebuild_symbol_leftovers_queue() -> Dict[str, Any]:
    """
    Symbol-level ops queue: securities that still have open (non-suppressed) findings.

    One row per symbol — not repeated per client. Advisors fix feed once.
    """
    from models import PriceAccuracyFinding, PriceAccuracyRun

    run = PriceAccuracyRun.query.order_by(PriceAccuracyRun.id.desc()).first()
    if not run:
        empty = {
            "schema_version": 1,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "run_id": None,
            "symbols": [],
        }
        leftovers_queue_path().write_text(json.dumps(empty, indent=2), encoding="utf-8")
        return empty

    pack = load_suppressions()
    findings = PriceAccuracyFinding.query.filter_by(run_id=run.id).all()
    by_sym: Dict[str, Dict[str, Any]] = {}
    for f in findings:
        if is_finding_suppressed(f, pack=pack):
            continue
        if (f.kind or "").upper() == "SPIKE":
            # DB ack already handled in verify; skip if somehow still here with ack-only
            pass
        sym = (f.symbol or "").strip().upper() or f"SID:{f.security_id}"
        row = by_sym.get(sym)
        if not row:
            row = {
                "symbol": sym,
                "security_id": int(f.security_id),
                "spike": 0,
                "missing": 0,
                "zero": 0,
                "sample_dates": [],
                "hub_url": "/hub/system/price-accuracy",
            }
            by_sym[sym] = row
        kind = (f.kind or "").upper()
        if kind == "SPIKE":
            row["spike"] += 1
        elif kind == "ZERO":
            row["zero"] += 1
        else:
            row["missing"] += 1
        d = f.missing_date or f.date_next or f.date_prev
        if d and len(row["sample_dates"]) < 3:
            ds = str(d)[:10]
            if ds not in row["sample_dates"]:
                row["sample_dates"].append(ds)

    symbols = sorted(
        by_sym.values(),
        key=lambda r: (-(r["spike"] + r["zero"]), -r["missing"], r["symbol"]),
    )
    out = {
        "schema_version": 1,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "run_id": run.id,
        "symbols": symbols,
        "total_symbols": len(symbols),
        "note": (
            "Fix price feed / enter closes per symbol once; "
            "Client DI pages hide sheet-verified noise via suppress JSON."
        ),
    }
    leftovers_queue_path().write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


def load_leftovers_queue() -> Dict[str, Any]:
    path = leftovers_queue_path()
    if not path.is_file():
        return {"symbols": [], "total_symbols": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"symbols": []}
    except Exception:
        return {"symbols": [], "total_symbols": 0}


def is_item_suppressed(item: Dict[str, Any], *, pack: Optional[Dict[str, Any]] = None) -> bool:
    """Filter nightly snapshot price items (dict rows) via suppress JSON."""
    pack = pack or load_suppressions()
    kind = (item.get("kind") or "").upper()
    symbol = item.get("symbol") or ""
    fact_id = item.get("fact_id") or ""
    # fact_id shape: price:MISSING:RELIANCE
    if fact_id.startswith("price:") and fact_id.count(":") >= 2:
        parts = fact_id.split(":")
        kind = parts[1].upper() or kind
        symbol = parts[2] or symbol
    if kind == "SPIKE":
        # Snapshot items usually don't carry date_prev/next; suppress by symbol+kind prefix
        entries = pack.get("entries") or {}
        prefix = f"SPIKE|{(symbol or '').upper()}|"
        return any(k.startswith(prefix) for k in entries)
    # For grouped MISSING/ZERO items, suppress only if ALL sample dates suppressed —
    # if fact_id is price:KIND:SYM without date, check any entry for that symbol+kind
    entries = pack.get("entries") or {}
    prefix = f"{kind}|{(symbol or '').upper()}|"
    # Grouped row: if every known sample date is suppressed, or no dates and any suppress exists
    samples = item.get("sample_dates") or []
    if samples:
        return all(
            suppression_key(kind=kind, symbol=symbol, missing_date=str(d)[:10]) in entries
            for d in samples
        )
    # Prefer keep if we can't prove full suppress for a group without dates
    return False
