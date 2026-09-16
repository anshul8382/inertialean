"""
Scan historical_price for traded securities: large consecutive-row moves, zeros, tx dates missing EOD rows.
Stores latest run + findings for the hub; spike acknowledgements persist across runs.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    HistoricalPrice,
    PriceAccuracyFinding,
    PriceAccuracyRun,
    PriceAccuracySpikeAck,
    Security,
    Transaction,
)

logger = logging.getLogger(__name__)


def _f(x: Any) -> float:
    if x is None:
        return float("nan")
    return float(x)


def _normalize_cal_date(value: Any) -> Optional[date]:
    """MySQL/driver may use date vs midnight datetime or YYYY-MM-DD strings."""
    from services.price_accuracy_holiday_service import to_cal_date

    return to_cal_date(value)


# Placeholder ledger / bogus prices use this calendar day — exclude from price-accuracy report
IGNORED_DUMMY_PRICE_REPORT_DATE = date(2000, 1, 1)


def _is_nse_weekend(val: Any) -> bool:
    """NSE/BSE are closed Sat/Sun — a trade on those calendar days has no EOD close to store."""
    from services.price_accuracy_holiday_service import is_weekend

    return is_weekend(val)


def _is_excluded_dummy_report_date(val: Any) -> bool:
    d = _normalize_cal_date(val)
    return bool(d and d == IGNORED_DUMMY_PRICE_REPORT_DATE)


def _finding_involves_excluded_dummy_date(f: PriceAccuracyFinding) -> bool:
    if _is_excluded_dummy_report_date(f.missing_date):
        return True
    if (f.kind or "").upper() in ("MISSING", "ZERO") and _is_nse_weekend(f.missing_date):
        return True
    if _is_excluded_dummy_report_date(f.date_prev) or _is_excluded_dummy_report_date(f.date_next):
        return True
    return False


def load_spike_ack_keys() -> Set[Tuple[int, date, date]]:
    rows = db.session.query(
        PriceAccuracySpikeAck.security_id,
        PriceAccuracySpikeAck.date_prev,
        PriceAccuracySpikeAck.date_next,
    ).all()
    out: Set[Tuple[int, date, date]] = set()
    for r in rows:
        if r[0] is None:
            continue
        d1 = _normalize_cal_date(r[1])
        d2 = _normalize_cal_date(r[2])
        if not d1 or not d2:
            continue
        out.add((int(r[0]), d1, d2))
    return out


def _purge_spike_findings_matching_ack(security_id: int, date_prev: date, date_next: date) -> int:
    """Remove stored report rows so verified spikes disappear immediately (run totals may lag until next scan)."""
    date_prev = _normalize_cal_date(date_prev)
    date_next = _normalize_cal_date(date_next)
    if not date_prev or not date_next:
        return 0
    n = PriceAccuracyFinding.query.filter(
        PriceAccuracyFinding.kind == "SPIKE",
        PriceAccuracyFinding.security_id == int(security_id),
        func.date(PriceAccuracyFinding.date_prev) == date_prev,
        func.date(PriceAccuracyFinding.date_next) == date_next,
    ).delete(synchronize_session=False)
    return int(n) if n is not None else 0


def txn_date_ranges_by_security() -> List[Tuple[int, date, date]]:
    """Distinct security ids that have trades, with calendar min/max trade dates."""
    q = (
        db.session.query(
            Transaction.security_id,
            func.min(func.date(Transaction.transaction_date)),
            func.max(func.date(Transaction.transaction_date)),
        )
        .group_by(Transaction.security_id)
        .all()
    )
    out: List[Tuple[int, date, date]] = []
    for sid, mn, mx in q:
        if sid is None or mn is None or mx is None:
            continue
        out.append((int(sid), mn, mx))
    return out


def run_price_accuracy_scan(threshold_pct: float = 10.0) -> Dict[str, Any]:
    """
    Replace previous run/results with a fresh snapshot.
    Threshold applies to consecutive historical rows (pct change vs prior row close).
    """
    threshold_pct = float(threshold_pct)
    if threshold_pct <= 0:
        raise ValueError("threshold_pct must be positive")

    started = datetime.utcnow()
    try:
        PriceAccuracyFinding.query.delete()
        PriceAccuracyRun.query.delete()
        db.session.flush()

        ack = load_spike_ack_keys()
        sym_by_id = {s.id: s.symbol for s in Security.query.with_entities(Security.id, Security.symbol).all()}

        tx_ranges = txn_date_ranges_by_security()
        if not tx_ranges:
            run = PriceAccuracyRun(
                started_at=started,
                finished_at=datetime.utcnow(),
                threshold_pct=Decimal(str(round(threshold_pct, 4))),
                securities_scanned=0,
                spike_count=0,
                missing_count=0,
                zero_count=0,
            )
            db.session.add(run)
            db.session.commit()
            return {"run_id": run.id, "securities_scanned": 0, "spikes": 0, "missing": 0, "zeros": 0, "skipped_closed": 0}

        tx_dates_by_sid: Dict[int, Set[date]] = {}
        date_rows = (
            db.session.query(Transaction.security_id, func.date(Transaction.transaction_date))
            .filter(Transaction.security_id.in_([x[0] for x in tx_ranges]))
            .distinct()
            .all()
        )
        for sid, d in date_rows:
            if sid is None or d is None:
                continue
            nd = _normalize_cal_date(d)
            if not nd or _is_excluded_dummy_report_date(nd):
                continue
            tx_dates_by_sid.setdefault(int(sid), set()).add(nd)

        from services.price_accuracy_holiday_service import (
            detect_sparse_weekdays,
            is_closed_calendar_day,
            load_holiday_dates,
            long_price_gaps,
            save_sparse_candidates,
            to_cal_date,
        )

        holiday_dates = load_holiday_dates()
        from services.corporate_action_price_restatement import (
            load_dilutive_cas_grouped,
            spike_explained_by_corporate_actions,
        )

        cas_by_sid = load_dilutive_cas_grouped([x[0] for x in tx_ranges])

        spikes: List[Dict[str, Any]] = []
        missing: List[Dict[str, Any]] = []
        zeros: List[Dict[str, Any]] = []
        coverage: List[Dict[str, Any]] = []

        th = threshold_pct / 100.0

        for sid, mn, mx in tx_ranges:
            symbol = sym_by_id.get(sid, f"id:{sid}")
            mn_d = _normalize_cal_date(mn)
            mx_d = _normalize_cal_date(mx)
            if not mn_d or not mx_d:
                continue

            hp_rows = (
                HistoricalPrice.query.filter(
                    HistoricalPrice.security_id == sid,
                    HistoricalPrice.date >= mn,
                    HistoricalPrice.date <= mx,
                )
                .order_by(HistoricalPrice.date.asc())
                .with_entities(HistoricalPrice.date, HistoricalPrice.close_price)
                .all()
            )

            have = {d for d in (_normalize_cal_date(r.date) for r in hp_rows) if d}
            coverage.append(
                {
                    "sid": sid,
                    "symbol": symbol,
                    "mn": mn_d,
                    "mx": mx_d,
                    "have": have,
                    "hp_rows": hp_rows,
                    "trades": tx_dates_by_sid.get(int(sid)) or set(),
                }
            )

        sparse_rows = detect_sparse_weekdays(coverage, holiday_dates=holiday_dates)
        save_sparse_candidates(sparse_rows)
        sparse_dates = {to_cal_date(r.get("date")) for r in sparse_rows}
        sparse_dates.discard(None)
        closed_for_gaps = set(holiday_dates) | sparse_dates

        for row in coverage:
            sid = row["sid"]
            symbol = row["symbol"]
            mn_d = row["mn"]
            mx_d = row["mx"]
            have = row["have"]
            hp_rows = row["hp_rows"]

            for gap in long_price_gaps(
                have,
                range_start=mn_d,
                range_end=mx_d,
                closed=closed_for_gaps,
                trade_dates=row.get("trades") or set(),
            ):
                missing.append(
                    {
                        "security_id": sid,
                        "symbol": symbol,
                        "missing_date": gap["first_missing"],
                        "date_prev": gap.get("date_prev"),
                        "date_next": gap.get("date_next"),
                    }
                )

            for r in hp_rows:
                rd = _normalize_cal_date(r.date)
                if not rd or _is_excluded_dummy_report_date(rd):
                    continue
                if is_closed_calendar_day(rd, holiday_dates=holiday_dates):
                    continue
                cp = float(r.close_price) if r.close_price is not None else 0.0
                if cp <= 0:
                    zeros.append(
                        {"security_id": sid, "symbol": symbol, "missing_date": rd},
                    )

            for i in range(len(hp_rows) - 1):
                d_prev = _normalize_cal_date(hp_rows[i].date)
                d_next = _normalize_cal_date(hp_rows[i + 1].date)
                if not d_prev or not d_next:
                    continue
                if _is_excluded_dummy_report_date(d_prev) or _is_excluded_dummy_report_date(d_next):
                    continue
                p_prev = _f(hp_rows[i].close_price)
                p_next = _f(hp_rows[i + 1].close_price)
                if p_prev <= 0 or p_next <= 0:
                    continue
                if (sid, d_prev, d_next) in ack:
                    continue
                change = abs(p_next - p_prev) / p_prev if p_prev else 0
                if change >= th:
                    if spike_explained_by_corporate_actions(
                        date_prev=d_prev,
                        date_next=d_next,
                        close_prev=p_prev,
                        close_next=p_next,
                        actions=cas_by_sid.get(int(sid)) or [],
                        residual_threshold=th,
                    ):
                        continue
                    pct = round((p_next - p_prev) / p_prev * 100.0, 6)
                    spikes.append(
                        {
                            "security_id": sid,
                            "symbol": symbol,
                            "date_prev": d_prev,
                            "date_next": d_next,
                            "close_prev": Decimal(str(p_prev)),
                            "close_next": Decimal(str(p_next)),
                            "pct_change": Decimal(str(pct)),
                        },
                    )

        run = PriceAccuracyRun(
            started_at=started,
            finished_at=datetime.utcnow(),
            threshold_pct=Decimal(str(round(threshold_pct, 4))),
            securities_scanned=len(tx_ranges),
            spike_count=len(spikes),
            missing_count=len(missing),
            zero_count=len(zeros),
        )
        db.session.add(run)
        db.session.flush()

        for s in spikes:
            db.session.add(
                PriceAccuracyFinding(
                    run_id=run.id,
                    kind="SPIKE",
                    security_id=s["security_id"],
                    symbol=s["symbol"],
                    date_prev=s["date_prev"],
                    date_next=s["date_next"],
                    close_prev=s["close_prev"],
                    close_next=s["close_next"],
                    pct_change=s["pct_change"],
                )
            )

        for m in missing:
            db.session.add(
                PriceAccuracyFinding(
                    run_id=run.id,
                    kind="MISSING",
                    security_id=m["security_id"],
                    symbol=m["symbol"],
                    missing_date=m["missing_date"],
                    date_prev=m.get("date_prev"),
                    date_next=m.get("date_next"),
                )
            )

        # ZERO uses missing_date column to carry the offending row date for display
        for z in zeros:
            db.session.add(
                PriceAccuracyFinding(
                    run_id=run.id,
                    kind="ZERO",
                    security_id=z["security_id"],
                    symbol=z["symbol"],
                    missing_date=z["missing_date"],
                )
            )

        db.session.commit()
        return {
            "run_id": run.id,
            "securities_scanned": len(tx_ranges),
            "spikes": len(spikes),
            "missing": len(missing),
            "zeros": len(zeros),
            "skipped_closed": len(sparse_rows),
        }
    except Exception as exc:
        logger.exception("price accuracy scan failed: %s", exc)
        db.session.rollback()
        raise


def _sheets_match_tolerance_ratio() -> float:
    try:
        from flask import has_app_context, current_app

        if has_app_context() and current_app:
            v = current_app.config.get("PRICE_ACCURACY_SHEETS_MATCH_TOLERANCE_PCT")
            if v is not None:
                return float(v) / 100.0
    except Exception:
        pass
    return float(os.environ.get("PRICE_ACCURACY_SHEETS_MATCH_TOLERANCE_PCT", "1") or "1") / 100.0


def _db_close_matches_sheet(db_close: float, sheet_close: float, rel_tol: float) -> bool:
    from services.price_accuracy_sheet_suppress_service import _within_tol

    return _within_tol(db_close, sheet_close, rel_tol)


def _spike_matches_sheet(dbp: float, dbn: float, sp: float, sn: float, rel_tol: float) -> bool:
    from services.price_accuracy_sheet_suppress_service import spike_matches_sheet_closes

    return spike_matches_sheet_closes(dbp, dbn, sp, sn, rel_tol)


def acknowledge_spikes_bulk(
    finding_ids: List[int],
    *,
    user_id: Optional[int] = None,
    notes: Optional[str] = None,
    source: str = "ui_bulk",
) -> Dict[str, int]:
    """Mark many SPIKE findings as verified (persist spike acks). Already-acked pairs are skipped."""
    n_new = 0
    n_existing = 0
    for fid in finding_ids:
        try:
            fid_int = int(fid)
        except (TypeError, ValueError):
            continue
        f = PriceAccuracyFinding.query.filter_by(id=fid_int, kind="SPIKE").first()
        if not f or not f.date_prev or not f.date_next:
            continue
        dp = _normalize_cal_date(f.date_prev)
        dn = _normalize_cal_date(f.date_next)
        if not dp or not dn:
            continue
        existed = PriceAccuracySpikeAck.query.filter_by(
            security_id=f.security_id,
            date_prev=dp,
            date_next=dn,
        ).first()
        if existed:
            if _purge_spike_findings_matching_ack(f.security_id, dp, dn):
                db.session.commit()
            n_existing += 1
            continue
        got = acknowledge_spike(
            security_id=f.security_id,
            date_prev=f.date_prev,
            date_next=f.date_next,
            source=(source or "ui_bulk")[:30],
            user_id=user_id,
            notes=(notes or "")[:500] if notes else None,
        )
        if got:
            n_new += 1
    return {"new_ack": n_new, "already_acked": n_existing, "requested": len(finding_ids)}


def verify_spikes_from_google_sheets(
    *,
    finding_ids: Optional[List[int]] = None,
    all_spikes_for_latest_run: bool = False,
    user_id: Optional[int] = None,
    rel_tol: Optional[float] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    For SPIKE findings, fetch closes via GOOGLEFINANCE on the HistoricalPrices sheet (batch).
    If both dates match stored DB closes within tolerance, insert spike ack automatically.

    dry_run: compare only — no spike ack, no suppress JSON, no finding delete.
    """
    from services.google_sheets_historical_price import GoogleSheetsHistoricalPrice, _sanitize_symbol

    repo_sa = Path(__file__).resolve().parent.parent / "service_account.json"
    if not repo_sa.is_file():
        raise FileNotFoundError(f"Missing Google service account JSON: {repo_sa}")

    run = PriceAccuracyRun.query.order_by(PriceAccuracyRun.id.desc()).first()
    if not run:
        return {"error": "no_price_accuracy_run", "verified": 0, "no_sheet_data": 0, "mismatch": 0}

    q = PriceAccuracyFinding.query.filter_by(run_id=run.id, kind="SPIKE")
    if not all_spikes_for_latest_run and finding_ids:
        ids_int = []
        for x in finding_ids:
            try:
                ids_int.append(int(x))
            except (TypeError, ValueError):
                pass
        if not ids_int:
            return {"error": "no_finding_ids", "verified": 0, "no_sheet_data": 0, "mismatch": 0}
        q = q.filter(PriceAccuracyFinding.id.in_(ids_int))

    spikes = q.all()
    if not spikes:
        return {"verified": 0, "no_sheet_data": 0, "mismatch": 0, "skipped": 0, "message": "no spikes to check"}

    ack_existing = load_spike_ack_keys()
    spikes_open = []
    for f in spikes:
        dp = _normalize_cal_date(f.date_prev)
        dn = _normalize_cal_date(f.date_next)
        if not dp or not dn:
            continue
        if (int(f.security_id), dp, dn) not in ack_existing:
            spikes_open.append(f)
    n_skipped_acked = len(spikes) - len(spikes_open)
    spikes = spikes_open
    if not spikes:
        return {
            "verified": 0,
            "no_sheet_data": 0,
            "mismatch": 0,
            "skipped_already_acked": n_skipped_acked,
            "message": "all selected spikes already verified",
        }

    tol = rel_tol if rel_tol is not None else _sheets_match_tolerance_ratio()

    pairs_ordered: List[Tuple[str, date]] = []
    seen_pairs: Set[Tuple[str, str]] = set()

    for f in spikes:
        if not f.date_prev or not f.date_next:
            continue
        for dt_raw in (f.date_prev, f.date_next):
            dt = _normalize_cal_date(dt_raw)
            if not dt:
                continue
            sym = _sanitize_symbol(f.symbol)
            if sym == "INVALID":
                continue
            key = (sym, dt.strftime("%Y-%m-%d"))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            pairs_ordered.append((f.symbol, dt))

    batch_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if pairs_ordered:
        wait = min(120.0, max(6.0, 4.0 + len(pairs_ordered) * 0.12))
        batch_map = GoogleSheetsHistoricalPrice.get_historical_price_batch(pairs_ordered, wait_seconds=wait)

    single_cache: Dict[Tuple[str, str], Optional[float]] = {}

    def sheet_close(symbol: str, dt: date) -> Optional[float]:
        dt = _normalize_cal_date(dt) or dt
        sym = _sanitize_symbol(symbol)
        if sym == "INVALID":
            return None
        ds = dt.strftime("%Y-%m-%d")
        hit = batch_map.get((sym, ds))
        if hit and hit.get("price") is not None:
            return float(hit["price"])
        ck = (sym, ds)
        if ck in single_cache:
            return single_cache[ck]
        r = GoogleSheetsHistoricalPrice.get_historical_price(
            symbol,
            dt,
            try_nearby_days=False,
            max_days_back=0,
        )
        if r and float(r["price"]) > 0 and r.get("days_offset", 99) == 0:
            single_cache[ck] = float(r["price"])
            return single_cache[ck]
        single_cache[ck] = None
        return None

    verified = 0
    verified_price_level = 0
    verified_move_only = 0
    no_sheet = 0
    mismatch = 0
    mismatch_samples: List[Dict[str, Any]] = []
    would_close_samples: List[Dict[str, Any]] = []

    notes = (
        f"Auto verify: GOOGLEFINANCE (sheet) vs DB within {tol * 100:.2g}% "
        f"(price level or same % move)"
    )[:500]

    from services.price_accuracy_sheet_suppress_service import _within_tol

    for f in spikes:
        if not f.date_prev or not f.date_next or f.close_prev is None or f.close_next is None:
            continue
        sp = sheet_close(f.symbol, f.date_prev)
        sn = sheet_close(f.symbol, f.date_next)
        if sp is None or sn is None:
            no_sheet += 1
            continue
        dbp = float(f.close_prev)
        dbn = float(f.close_next)
        price_ok = _within_tol(dbp, sp, tol) and _within_tol(dbn, sn, tol)
        move_ok = _spike_matches_sheet(dbp, dbn, sp, sn, tol)
        if move_ok:
            if price_ok:
                verified_price_level += 1
                how = "price_level"
            else:
                verified_move_only += 1
                how = "same_pct_move"
            db_pct = ((dbn - dbp) / dbp * 100.0) if dbp else None
            sh_pct = ((sn - sp) / sp * 100.0) if sp else None
            sample = {
                "symbol": f.symbol,
                "how": how,
                "date_prev": str(f.date_prev)[:10],
                "date_next": str(f.date_next)[:10],
                "db_pct": round(db_pct, 2) if db_pct is not None else None,
                "sheet_pct": round(sh_pct, 2) if sh_pct is not None else None,
            }
            sid = int(f.security_id)
            d_prev, d_next = f.date_prev, f.date_next
            if not dry_run:
                _write_finding_suppress(f, reason="sheet_matches_db")
                acknowledge_spike(
                    security_id=sid,
                    date_prev=d_prev,
                    date_next=d_next,
                    source="google_sheets_match",
                    user_id=user_id,
                    notes=notes,
                )
            verified += 1
            if len(would_close_samples) < 8:
                would_close_samples.append(sample)
        else:
            mismatch += 1
            if len(mismatch_samples) < 5:
                db_pct = ((dbn - dbp) / dbp * 100.0) if dbp else None
                sh_pct = ((sn - sp) / sp * 100.0) if sp else None
                mismatch_samples.append(
                    {
                        "symbol": f.symbol,
                        "date_prev": str(f.date_prev)[:10],
                        "date_next": str(f.date_next)[:10],
                        "db_prev": round(dbp, 4),
                        "db_next": round(dbn, 4),
                        "sheet_prev": round(sp, 4),
                        "sheet_next": round(sn, 4),
                        "db_pct": round(db_pct, 2) if db_pct is not None else None,
                        "sheet_pct": round(sh_pct, 2) if sh_pct is not None else None,
                    }
                )

    return {
        "ok": True,
        "dry_run": dry_run,
        "verified": verified,
        "verified_price_level": verified_price_level,
        "verified_move_only": verified_move_only,
        "no_sheet_data": no_sheet,
        "mismatch": mismatch,
        "mismatch_samples": mismatch_samples,
        "would_close_samples": would_close_samples,
        "tolerance_pct": tol * 100.0,
        "spikes_checked": len(spikes),
        "skipped_already_acked": n_skipped_acked,
        "wrote_acks": (not dry_run) and verified > 0,
    }


def _write_finding_suppress(finding: Any, *, reason: str) -> None:
    """Record firm-wide suppress JSON so Hub/client hide this finding even if DB purge lags."""
    try:
        from services.price_accuracy_sheet_suppress_service import add_suppression_entry

        kind = str(getattr(finding, "kind", "") or "").upper()
        md = getattr(finding, "missing_date", None)
        dp = getattr(finding, "date_prev", None)
        dn = getattr(finding, "date_next", None)
        add_suppression_entry(
            kind=kind,
            symbol=str(getattr(finding, "symbol", "") or ""),
            security_id=int(finding.security_id) if getattr(finding, "security_id", None) is not None else None,
            missing_date=str(md)[:10] if md else None,
            date_prev=str(dp)[:10] if dp else None,
            date_next=str(dn)[:10] if dn else None,
            reason=reason,
            persist=True,
        )
    except Exception as exc:
        logger.warning("price-accuracy suppress JSON write failed: %s", exc)


def close_finding_verified_from_sheet(
    finding_id: int,
    *,
    source: str = "google_sheets_webhook",
    user_id: Optional[int] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Close a Hub finding after spreadsheet verification (any kind).

    SPIKE → persist spike ack and delete finding.
    MISSING/ZERO → suppress JSON + delete finding (holiday / accepted gap).
    """
    finding = PriceAccuracyFinding.query.get(int(finding_id))
    if not finding:
        return {"ok": False, "error": "finding_not_found"}
    kind = (finding.kind or "").upper()
    if kind == "SPIKE":
        if not finding.date_prev or not finding.date_next:
            return {"ok": False, "error": "SPIKE finding missing dates"}
        _write_finding_suppress(finding, reason="sheet_verified")
        ack = acknowledge_spike(
            security_id=finding.security_id,
            date_prev=finding.date_prev,
            date_next=finding.date_next,
            source=(source or "google_sheets_webhook")[:30],
            user_id=user_id,
            notes=(notes or "")[:500] if notes else None,
        )
        return {"ok": True, "kind": "SPIKE", "ack_id": ack.id if ack else None}
    if kind in ("MISSING", "ZERO"):
        _write_finding_suppress(finding, reason="sheet_verified")
        db.session.delete(finding)
        db.session.commit()
        return {"ok": True, "kind": kind, "ack_id": None}
    return {"ok": False, "error": f"unsupported kind {kind}"}


def acknowledge_spike(
    *,
    security_id: int,
    date_prev: date,
    date_next: date,
    source: str = "ui",
    user_id: Optional[int] = None,
    notes: Optional[str] = None,
) -> Optional[PriceAccuracySpikeAck]:
    date_prev = _normalize_cal_date(date_prev)
    date_next = _normalize_cal_date(date_next)
    if not date_prev or not date_next:
        raise ValueError("acknowledge_spike requires date_prev and date_next")

    new_ack = PriceAccuracySpikeAck(
        security_id=security_id,
        date_prev=date_prev,
        date_next=date_next,
        source=(source or "ui")[:30],
        user_id=user_id,
        notes=(notes or "")[:500] if notes else None,
    )
    row: Optional[PriceAccuracySpikeAck] = None
    try:
        db.session.add(new_ack)
        db.session.flush()
        row = new_ack
    except IntegrityError:
        db.session.rollback()
        row = PriceAccuracySpikeAck.query.filter_by(
            security_id=security_id, date_prev=date_prev, date_next=date_next
        ).first()
        if row is None:
            logger.warning(
                "acknowledge_spike: duplicate key but no row for sid=%s %s/%s",
                security_id,
                date_prev,
                date_next,
            )
            return None

    _purge_spike_findings_matching_ack(security_id, date_prev, date_next)
    db.session.commit()
    return row


def _close_gap_findings_for_filled_price(security_id: int, d: date) -> int:
    """Drop MISSING/ZERO alerts for a date once a real close exists in historical_price."""
    d = _normalize_cal_date(d)
    if not d:
        return 0
    n = (
        PriceAccuracyFinding.query.filter(
            PriceAccuracyFinding.security_id == int(security_id),
            PriceAccuracyFinding.kind.in_(["MISSING", "ZERO"]),
            func.date(PriceAccuracyFinding.missing_date) == d,
        ).delete(synchronize_session=False)
    )
    return int(n or 0)


def mark_market_holiday_and_close_findings(
    holiday_date: Any,
    *,
    note: str = "",
    source: str = "ui",
) -> Dict[str, Any]:
    """Persist a market holiday and drop MISSING/ZERO findings on that calendar day."""
    from services.price_accuracy_holiday_service import (
        add_market_holiday,
        drop_sparse_candidate,
        to_cal_date,
    )

    d = to_cal_date(holiday_date)
    if not d:
        return {"ok": False, "error": "invalid_date", "closed": 0}
    add_market_holiday(d, note=note, source=source)
    drop_sparse_candidate(d)
    n = (
        PriceAccuracyFinding.query.filter(
            PriceAccuracyFinding.kind.in_(["MISSING", "ZERO"]),
            func.date(PriceAccuracyFinding.missing_date) == d,
        ).delete(synchronize_session=False)
    )
    # Long-gap rows store the hole between date_prev and date_next.
    spanned = PriceAccuracyFinding.query.filter(
        PriceAccuracyFinding.kind == "MISSING",
        PriceAccuracyFinding.date_prev.isnot(None),
        PriceAccuracyFinding.date_next.isnot(None),
        func.date(PriceAccuracyFinding.date_prev) < d,
        func.date(PriceAccuracyFinding.date_next) > d,
    ).delete(synchronize_session=False)
    closed = int(n or 0) + int(spanned or 0)
    db.session.commit()
    return {"ok": True, "date": d.isoformat(), "closed": closed}


def _neighbor_closes_excluding_sheet_fills(security_id: int, target: date) -> List[float]:
    """Nearest as-traded neighbors (skip prior google_sheets_missing_fill contamination)."""
    prev = (
        HistoricalPrice.query.filter(
            HistoricalPrice.security_id == int(security_id),
            HistoricalPrice.date < target,
            HistoricalPrice.source != "google_sheets_missing_fill",
            HistoricalPrice.close_price > 0,
        )
        .order_by(HistoricalPrice.date.desc())
        .first()
    )
    nxt = (
        HistoricalPrice.query.filter(
            HistoricalPrice.security_id == int(security_id),
            HistoricalPrice.date > target,
            HistoricalPrice.source != "google_sheets_missing_fill",
            HistoricalPrice.close_price > 0,
        )
        .order_by(HistoricalPrice.date.asc())
        .first()
    )
    out: List[float] = []
    if prev and prev.close_price is not None:
        out.append(float(prev.close_price))
    if nxt and nxt.close_price is not None:
        out.append(float(nxt.close_price))
    return out


def assess_incoming_close_for_ca_adjusted(
    *,
    security_id: int,
    target: date,
    close_price: float,
    cas_by_sid: Optional[Dict[int, List[Any]]] = None,
) -> Dict[str, Any]:
    """
    Refuse GOOGLEFINANCE / sheet closes that look corporate-action-adjusted vs as-traded history.
    """
    from services.corporate_action_price_restatement import (
        ca_action_date,
        load_dilutive_cas_grouped,
        sheet_close_looks_ca_adjusted,
    )

    sid = int(security_id)
    if cas_by_sid is None:
        cas_by_sid = load_dilutive_cas_grouped([sid])
    actions_after = []
    for a in cas_by_sid.get(sid, []) or []:
        ad = ca_action_date(getattr(a, "action_date", None) if not isinstance(a, dict) else a.get("action_date"))
        if ad and ad > target:
            actions_after.append(a)
    return sheet_close_looks_ca_adjusted(
        sheet_close=float(close_price),
        neighbor_closes=_neighbor_closes_excluding_sheet_fills(sid, target),
        actions_after_target=actions_after,
    )


def apply_historical_price_fixes(
    fixes: List[Dict[str, Any]],
    *,
    source: str = "manual",
    reject_ca_adjusted: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Upsert historical_price rows. Each fix: security_id (int), date (YYYY-MM-DD), close_price (number).
    Closes matching MISSING/ZERO findings so the Hub alert disappears without a re-scan.

    Sheet / GOOGLEFINANCE sources reject closes that look CA-adjusted vs as-traded neighbors
    (see UNADJUSTED_NSE_REQUIRED_MSG). Pass reject_ca_adjusted=False only for trusted manual NSE unadjusted uploads.
    """
    from services.corporate_action_price_restatement import (
        UNADJUSTED_NSE_REQUIRED_MSG,
        load_dilutive_cas_grouped,
    )

    src = (source or "manual")[:50]
    if reject_ca_adjusted is None:
        reject_ca_adjusted = src.startswith("google_sheets") or src in (
            "google_sheets_webhook",
            "google_sheets_missing_fill",
            "google_sheets_match",
        )

    updated = 0
    created = 0
    findings_closed = 0
    rejected_adjusted = 0
    errors: List[str] = []
    rejected_samples: List[Dict[str, Any]] = []

    cas_by_sid: Dict[int, List[Any]] = {}
    if reject_ca_adjusted:
        sids = []
        for raw in fixes:
            try:
                sids.append(int(raw["security_id"]))
            except Exception:
                continue
        cas_by_sid = load_dilutive_cas_grouped(sorted(set(sids)))

    for raw in fixes:
        try:
            sid = int(raw["security_id"])
            d_str = str(raw["date"])[:10]
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
            price = Decimal(str(raw["close_price"]))
            if price <= 0:
                errors.append(f"non-positive price for security {sid} {d_str}")
                continue
            if reject_ca_adjusted:
                assess = assess_incoming_close_for_ca_adjusted(
                    security_id=sid,
                    target=d,
                    close_price=float(price),
                    cas_by_sid=cas_by_sid,
                )
                if assess.get("rejected"):
                    rejected_adjusted += 1
                    msg = assess.get("message") or UNADJUSTED_NSE_REQUIRED_MSG
                    errors.append(f"rejected_ca_adjusted security {sid} {d_str}: {msg}")
                    if len(rejected_samples) < 15:
                        rejected_samples.append(
                            {
                                "security_id": sid,
                                "date": d_str,
                                "close_price": float(price),
                                "reason": assess.get("reason"),
                                "message": msg,
                                "ca": assess.get("ca_summaries") or [],
                            }
                        )
                    continue
            row = HistoricalPrice.query.filter_by(security_id=sid, date=d).first()
            if row:
                row.close_price = price
                row.source = src
                row.updated_at = datetime.utcnow()
                updated += 1
            else:
                db.session.add(
                    HistoricalPrice(
                        security_id=sid,
                        date=d,
                        close_price=price,
                        source=src,
                    )
                )
                created += 1
            findings_closed += _close_gap_findings_for_filled_price(sid, d)
        except Exception as ex:
            errors.append(str(ex))

    db.session.commit()
    return {
        "updated": updated,
        "created": created,
        "findings_closed": findings_closed,
        "rejected_adjusted": rejected_adjusted,
        "rejected_samples": rejected_samples,
        "user_message": UNADJUSTED_NSE_REQUIRED_MSG if rejected_adjusted else "",
        "errors": errors,
    }


def fill_missing_from_google_sheets(
    *,
    dry_run: bool = True,
    max_unique_lookups: int = 400,
    after_finding_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Same-date GOOGLEFINANCE closes for MISSING findings (HistoricalPrices sheet).

    Does not use nearby days. Skips Sat/Sun. Writes historical_price only when dry_run is False.
    Rejects closes that look corporate-action-adjusted vs as-traded neighbors (do not import).
    """
    from services.google_sheets_historical_price import GoogleSheetsHistoricalPrice, _sanitize_symbol
    from services.price_accuracy_sheet_suppress_service import (
        add_suppression_entry,
        is_finding_suppressed,
        load_suppressions,
        save_suppressions,
    )

    repo_sa = Path(__file__).resolve().parent.parent / "service_account.json"
    if not repo_sa.is_file():
        return {"ok": False, "error": "missing_service_account", "would_fill": 0}

    run = PriceAccuracyRun.query.order_by(PriceAccuracyRun.id.desc()).first()
    if not run:
        return {"ok": False, "error": "no_price_accuracy_run", "would_fill": 0}

    q = PriceAccuracyFinding.query.filter_by(run_id=run.id, kind="MISSING").order_by(
        PriceAccuracyFinding.id.asc()
    )
    findings = q.all()
    if after_finding_id is not None:
        findings = [f for f in findings if int(f.id) > int(after_finding_id)]

    pack = load_suppressions()
    to_fetch: List[Any] = []
    skipped_weekend = 0
    skipped_suppressed = 0
    for f in findings:
        md = _normalize_cal_date(f.missing_date)
        if not md:
            continue
        if _is_nse_weekend(md):
            skipped_weekend += 1
            continue
        if is_finding_suppressed(f, pack=pack):
            skipped_suppressed += 1
            continue
        to_fetch.append(f)

    pairs: List[Tuple[str, date]] = []
    seen: Set[Tuple[str, str]] = set()
    capped = 0
    for f in to_fetch:
        md = _normalize_cal_date(f.missing_date)
        if not md:
            continue
        sym = _sanitize_symbol(f.symbol)
        if sym == "INVALID":
            continue
        key = (sym, md.strftime("%Y-%m-%d"))
        if key in seen:
            continue
        if len(seen) >= int(max_unique_lookups):
            capped += 1
            continue
        seen.add(key)
        pairs.append((f.symbol, md))

    batch_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if pairs:
        wait = min(180.0, max(8.0, 4.0 + len(pairs) * 0.12))
        try:
            batch_map = GoogleSheetsHistoricalPrice.get_historical_price_batch(
                pairs, wait_seconds=wait
            )
        except Exception as exc:
            logger.warning("missing fill batch failed: %s", exc)
            return {"ok": False, "error": f"sheet_batch_failed:{exc}", "would_fill": 0}

    def sheet_px(symbol: str, dt: date) -> Optional[float]:
        sym = _sanitize_symbol(symbol)
        ds = dt.strftime("%Y-%m-%d")
        hit = batch_map.get((sym, ds))
        if not hit or hit.get("price") is None:
            return None
        try:
            p = float(hit["price"])
        except Exception:
            return None
        return p if p > 0 else None

    would_fill = 0
    holiday_or_empty = 0
    invalid_symbol = 0
    rejected_adjusted = 0
    fill_samples: List[Dict[str, Any]] = []
    empty_samples: List[Dict[str, Any]] = []
    rejected_samples: List[Dict[str, Any]] = []
    fixes: List[Dict[str, Any]] = []
    checked_ids: List[int] = []
    max_id = int(after_finding_id or 0)
    seen_fill: Set[Tuple[int, str]] = set()

    from services.corporate_action_price_restatement import (
        load_dilutive_cas_grouped,
        UNADJUSTED_NSE_REQUIRED_MSG,
    )

    cas_by_sid = load_dilutive_cas_grouped(
        sorted({int(f.security_id) for f in to_fetch if f.security_id is not None})
    )

    for f in to_fetch:
        md = _normalize_cal_date(f.missing_date)
        if not md:
            continue
        sym = _sanitize_symbol(f.symbol)
        if sym == "INVALID":
            invalid_symbol += 1
            continue
        ds = md.strftime("%Y-%m-%d")
        if (sym, ds) not in seen:
            continue
        checked_ids.append(int(f.id))
        max_id = max(max_id, int(f.id))
        sp = sheet_px(f.symbol, md)
        if sp is None:
            holiday_or_empty += 1
            if len(empty_samples) < 8:
                empty_samples.append({"symbol": f.symbol, "date": ds})
            if not dry_run:
                add_suppression_entry(
                    kind="MISSING",
                    symbol=f.symbol,
                    security_id=int(f.security_id),
                    missing_date=ds,
                    reason="sheet_also_empty",
                    pack=pack,
                )
            continue

        assess = assess_incoming_close_for_ca_adjusted(
            security_id=int(f.security_id),
            target=md,
            close_price=sp,
            cas_by_sid=cas_by_sid,
        )
        if assess.get("rejected"):
            rejected_adjusted += 1
            if len(rejected_samples) < 10:
                rejected_samples.append(
                    {
                        "symbol": f.symbol,
                        "date": ds,
                        "sheet_close": round(sp, 4),
                        "reason": assess.get("reason"),
                        "message": assess.get("message") or UNADJUSTED_NSE_REQUIRED_MSG,
                        "ca": assess.get("ca_summaries") or [],
                    }
                )
            continue

        pair_key = (int(f.security_id), ds)
        if pair_key in seen_fill:
            continue
        seen_fill.add(pair_key)
        would_fill += 1
        if len(fill_samples) < 8:
            fill_samples.append({"symbol": f.symbol, "date": ds, "sheet_close": round(sp, 4)})
        fixes.append(
            {
                "security_id": int(f.security_id),
                "date": ds,
                "close_price": sp,
            }
        )

    wrote = {"updated": 0, "created": 0, "findings_closed": 0, "errors": []}
    if not dry_run:
        if fixes:
            # Second-pass guard inside apply (same check); neighbors unchanged for new inserts.
            wrote = apply_historical_price_fixes(
                fixes,
                source="google_sheets_missing_fill",
                reject_ca_adjusted=True,
            )
        save_suppressions(pack)

    return {
        "ok": True,
        "dry_run": dry_run,
        "run_id": run.id,
        "missing_in_run": len(findings) if after_finding_id is None else len(to_fetch) + skipped_weekend + skipped_suppressed,
        "sheet_lookups": len(pairs),
        "would_fill": would_fill,
        "holiday_or_empty": holiday_or_empty,
        "rejected_adjusted": rejected_adjusted,
        "user_message": (
            UNADJUSTED_NSE_REQUIRED_MSG
            if rejected_adjusted
            else ""
        ),
        "invalid_symbol": invalid_symbol,
        "skipped_weekend": skipped_weekend,
        "skipped_suppressed": skipped_suppressed,
        "capped_skipped": capped,
        "fill_samples": fill_samples,
        "empty_samples": empty_samples,
        "rejected_samples": rejected_samples,
        "wrote": wrote if not dry_run else None,
        "last_finding_id": max_id,
        "batch_complete": capped == 0,
    }


def _delete_spike_findings_matching_any_existing_ack() -> None:
    """
    Removes SPIKE findings that already have an acknowledgement row.
    Repairs old deployments where ACK existed but findings were not deleted.
    """
    changed = False
    for sid, d1, d2 in load_spike_ack_keys():
        if _purge_spike_findings_matching_ack(sid, d1, d2):
            changed = True
    if changed:
        db.session.commit()


def latest_run_snapshot() -> Optional[Dict[str, Any]]:
    _delete_spike_findings_matching_any_existing_ack()
    run = PriceAccuracyRun.query.order_by(PriceAccuracyRun.id.desc()).first()
    if not run:
        return None
    findings = (
        PriceAccuracyFinding.query.filter_by(run_id=run.id).order_by(PriceAccuracyFinding.kind, PriceAccuracyFinding.symbol).all()
    )
    ack_keys = load_spike_ack_keys()
    suppress_pack = None
    try:
        from services.price_accuracy_sheet_suppress_service import (
            is_price_alert_closed,
            load_suppressions,
        )

        suppress_pack = load_suppressions()
    except Exception:
        is_price_alert_closed = None  # type: ignore
        suppress_pack = None
    from services.price_accuracy_holiday_service import (
        is_closed_calendar_day,
        load_holiday_dates,
        missing_finding_is_noise,
    )

    holiday_dates = load_holiday_dates()
    from services.corporate_action_price_restatement import (
        finding_is_ca_explained_spike,
        load_dilutive_cas_grouped,
    )

    cas_by_sid = load_dilutive_cas_grouped(
        [int(f.security_id) for f in findings if f.security_id]
    )
    residual_th = 0.10
    try:
        if run.threshold_pct is not None:
            residual_th = float(run.threshold_pct) / 100.0
    except Exception:
        residual_th = 0.10
    visible: List[PriceAccuracyFinding] = []
    for f in findings:
        if _finding_involves_excluded_dummy_date(f):
            continue
        kind = (f.kind or "").upper()
        if kind == "MISSING" and missing_finding_is_noise(f, holiday_dates=holiday_dates):
            continue
        if kind == "ZERO" and is_closed_calendar_day(f.missing_date, holiday_dates=holiday_dates):
            continue
        if finding_is_ca_explained_spike(f, cas_by_sid, residual_threshold=residual_th):
            continue
        closed = False
        if is_price_alert_closed is not None:
            closed = is_price_alert_closed(f, ack_keys=ack_keys, pack=suppress_pack)
        elif f.kind == "SPIKE" and f.date_prev is not None and f.date_next is not None:
            dp = _normalize_cal_date(f.date_prev)
            dn = _normalize_cal_date(f.date_next)
            if dp and dn and (int(f.security_id), dp, dn) in ack_keys:
                closed = True
        if closed:
            continue
        visible.append(f)

    payload = [
        {
            "id": f.id,
            "kind": f.kind,
            "security_id": f.security_id,
            "symbol": f.symbol,
            "date_prev": f.date_prev.isoformat() if f.date_prev else None,
            "date_next": f.date_next.isoformat() if f.date_next else None,
            "close_prev": float(f.close_prev) if f.close_prev is not None else None,
            "close_next": float(f.close_next) if f.close_next is not None else None,
            "pct_change": float(f.pct_change) if f.pct_change is not None else None,
            "missing_date": f.missing_date.isoformat() if f.missing_date else None,
        }
        for f in visible
    ]
    by_kind = Counter(x["kind"] for x in payload)
    return {
        "run": {
            "id": run.id,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "threshold_pct": float(run.threshold_pct) if run.threshold_pct is not None else 10,
            "securities_scanned": run.securities_scanned,
            "spike_count": by_kind.get("SPIKE", 0),
            "missing_count": by_kind.get("MISSING", 0),
            "zero_count": by_kind.get("ZERO", 0),
            "error_message": run.error_message,
        },
        "findings": payload,
    }


def firm_trade_calendar_dates(among: Iterable[Any]) -> Set[date]:
    """Subset of calendar dates that have at least one firm transaction."""
    from services.price_accuracy_holiday_service import to_cal_date

    wanted = {to_cal_date(x) for x in among}
    wanted.discard(None)
    if not wanted:
        return set()
    rows = (
        db.session.query(func.date(Transaction.transaction_date))
        .filter(func.date(Transaction.transaction_date).in_(list(wanted)))
        .distinct()
        .all()
    )
    out: Set[date] = set()
    for (d,) in rows:
        nd = to_cal_date(d)
        if nd:
            out.add(nd)
    return out


def visible_possible_holidays(
    rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Holiday list for Hub: only trade days where no traded name has a close."""
    from services.price_accuracy_holiday_service import (
        filter_holiday_candidates,
        load_sparse_candidates,
        to_cal_date,
    )

    pack = list(rows) if rows is not None else load_sparse_candidates()
    dates = [to_cal_date(r.get("date")) for r in pack if isinstance(r, dict)]
    traded = firm_trade_calendar_dates([d for d in dates if d])
    return filter_holiday_candidates(pack, traded_dates=traded)


def run_accuracy_scan_standalone(threshold_pct: float = 10.0) -> Dict[str, Any]:
    """For Airflow/CLI inside app context."""
    return run_price_accuracy_scan(threshold_pct=threshold_pct)


GROUP_BY_SYMBOL_COLLAPSE_THRESHOLD = 5


def group_findings_by_security_for_hub(
    rows: List[Dict[str, Any]],
    *,
    collapse_threshold: int = GROUP_BY_SYMBOL_COLLAPSE_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Stable order: symbol then id. ``collapse`` True when that symbol has more than ``collapse_threshold`` rows."""
    from collections import OrderedDict

    by_sid: "OrderedDict[int, Dict[str, Any]]" = OrderedDict()
    for row in sorted(rows, key=lambda x: ((x.get("symbol") or "").upper(), x.get("id", 0))):
        sid = int(row.get("security_id", 0))
        if sid not in by_sid:
            by_sid[sid] = {
                "security_id": sid,
                "symbol": row.get("symbol", ""),
                "rows": [],
            }
        by_sid[sid]["rows"].append(row)
    out: List[Dict[str, Any]] = []
    for bundle in by_sid.values():
        n = len(bundle["rows"])
        bundle["count"] = n
        bundle["collapse"] = n > collapse_threshold
        out.append(bundle)
    return out


def spike_finding_ids_latest_run_for_security(security_id: int) -> List[int]:
    run = PriceAccuracyRun.query.order_by(PriceAccuracyRun.id.desc()).first()
    if not run:
        return []
    rows = PriceAccuracyFinding.query.filter_by(
        run_id=run.id,
        kind="SPIKE",
        security_id=int(security_id),
    ).all()
    return [int(r.id) for r in rows]


def findings_dicts_filtered_for_push(
    all_findings: List[Dict[str, Any]],
    *,
    finding_ids: Optional[List[int]] = None,
    security_id: Optional[int] = None,
    kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Subset snapshot ``findings`` for Google Sheets queue (rewrites the queue tab to this subset)."""
    if finding_ids:
        id_set = {int(x) for x in finding_ids}
        return [f for f in all_findings if f.get("id") in id_set]
    if security_id is None and not kind:
        return list(all_findings)
    sid = int(security_id) if security_id is not None else None
    out: List[Dict[str, Any]] = []
    for f in all_findings:
        if sid is not None and int(f.get("security_id", -1)) != sid:
            continue
        if kind and f.get("kind") != kind:
            continue
        out.append(f)
    return out
