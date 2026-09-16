"""
Merge two Security rows that are the same listing (same symbol, duplicate names).

Keep one id, repoint every FK, then delete the duplicate. Caller commits.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    ClientBehaviorPattern,
    CorporateAction,
    DataIntegrityException,
    DataIntegrityIssue,
    HistoricalPrice,
    Holding,
    HoldingSnapshot,
    MProfitSymbolMap,
    PriceAccuracyFinding,
    PriceAccuracySpikeAck,
    Recommendation,
    Security,
    Transaction,
)

logger = logging.getLogger(__name__)

# Tables handled via ORM (skip these in the inspector leftover pass).
_ORM_TABLES = {
    "security",
    "transaction",
    "holding",
    "holding_snapshot",
    "recommendation",
    "security_allocation",
    "model_allocation",
    "mprofit_symbol_map",
    "historical_price",
    "price_accuracy_finding",
    "price_accuracy_spike_ack",
    "corporate_actions",
    "data_integrity_issue",
    "data_integrity_exception",
    "client_behavior_pattern",
}


def combined_holding_qty_avg(
    qty_a: float, avg_a: float, qty_b: float, avg_b: float
) -> Tuple[float, float]:
    """Quantity-weighted average price when folding two lots into one holding."""
    qa = max(0.0, float(qty_a or 0))
    qb = max(0.0, float(qty_b or 0))
    total = qa + qb
    if total <= 0:
        return 0.0, 0.0
    cost = qa * float(avg_a or 0) + qb * float(avg_b or 0)
    return total, cost / total


def choose_survivor_by_latest_history(
    rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Prefer the security whose historical_price series is newest.
    Tie-break: has current_price > 0, then longer name, then lower id.
    """
    if not rows:
        raise ValueError("No securities to choose from")

    def _key(r: Dict[str, Any]) -> Tuple:
        latest = r.get("latest_hist_date") or date.min
        has_px = 1 if float(r.get("current_price") or 0) > 0 else 0
        name_len = len((r.get("name") or "").strip())
        sid = int(r["id"])
        return (latest, has_px, name_len, -sid)

    return max(rows, key=_key)


def find_securities_by_symbol(symbol: str) -> List[Security]:
    from sqlalchemy import func

    sym = (symbol or "").upper().strip()
    return (
        Security.query.filter(func.upper(func.trim(Security.symbol)) == sym)
        .order_by(Security.id)
        .all()
    )


def preview_duplicate_symbol(symbol: str) -> Dict[str, Any]:
    secs = find_securities_by_symbol(symbol)
    previews = []
    for s in secs:
        latest = (
            db.session.query(db.func.max(HistoricalPrice.date))
            .filter(HistoricalPrice.security_id == s.id)
            .scalar()
        )
        hist_count = HistoricalPrice.query.filter_by(security_id=s.id).count()
        txn_count = Transaction.query.filter_by(security_id=s.id).count()
        hold_count = Holding.query.filter_by(security_id=s.id).count()
        previews.append(
            {
                "id": s.id,
                "symbol": s.symbol,
                "name": s.name,
                "current_price": float(s.current_price) if s.current_price is not None else None,
                "last_updated": s.last_updated.isoformat() if s.last_updated else None,
                "latest_hist_date": latest,
                "historical_price_rows": hist_count,
                "transaction_rows": txn_count,
                "holding_rows": hold_count,
            }
        )
    survivor = choose_survivor_by_latest_history(previews) if len(previews) >= 1 else None
    return {
        "symbol": (symbol or "").upper().strip(),
        "count": len(previews),
        "rows": previews,
        "suggested_keep_id": survivor["id"] if survivor else None,
    }


def merge_securities(keep_id: int, drop_id: int) -> Dict[str, Any]:
    """
    Repoint FKs from drop_id → keep_id and delete drop_id.
    Does not commit. Raises ValueError on invalid ids.
    """
    keep_id = int(keep_id)
    drop_id = int(drop_id)
    if keep_id == drop_id:
        raise ValueError("keep_id and drop_id must differ")

    keep = Security.query.get(keep_id)
    drop = Security.query.get(drop_id)
    if not keep or not drop:
        raise ValueError("Both securities must exist")

    report: Dict[str, Any] = {
        "keep_id": keep_id,
        "drop_id": drop_id,
        "keep_name": keep.name,
        "drop_name": drop.name,
        "symbol": keep.symbol,
        "moved": {},
        "deleted_conflicts": {},
    }

    _merge_holdings(keep_id, drop_id, report)
    _merge_holding_snapshots(keep_id, drop_id, report)
    _merge_historical_prices(keep_id, drop_id, report)
    _merge_corporate_actions(keep_id, drop_id, report)
    _merge_spike_acks(keep_id, drop_id, report)
    _merge_behavior_patterns(keep_id, drop_id, report)
    from services.holding_advisory_scope_service import merge_security_exclusions

    merge_security_exclusions(keep_id, drop_id, report)
    _merge_allocations(keep_id, drop_id, report)

    report["moved"]["transaction"] = _repoint_simple(Transaction, keep_id, drop_id)
    report["moved"]["recommendation"] = _repoint_simple(Recommendation, keep_id, drop_id)
    report["moved"]["mprofit_symbol_map"] = _repoint_simple(MProfitSymbolMap, keep_id, drop_id)
    report["moved"]["price_accuracy_finding"] = _repoint_simple(
        PriceAccuracyFinding, keep_id, drop_id
    )
    report["moved"]["data_integrity_issue"] = _repoint_simple(
        DataIntegrityIssue, keep_id, drop_id
    )
    report["moved"]["data_integrity_exception"] = _repoint_simple(
        DataIntegrityException, keep_id, drop_id
    )

    leftover = _repoint_leftover_columns(keep_id, drop_id)
    if leftover:
        report["moved"]["leftover_sql"] = leftover

    if (keep.current_price is None or float(keep.current_price or 0) <= 0) and drop.current_price:
        keep.current_price = drop.current_price
        keep.last_updated = drop.last_updated or keep.last_updated
    elif drop.last_updated and (not keep.last_updated or drop.last_updated > keep.last_updated):
        if drop.current_price and float(drop.current_price) > 0:
            keep.current_price = drop.current_price
            keep.last_updated = drop.last_updated

    keep.updated_at = datetime.utcnow()
    db.session.flush()
    db.session.expunge(drop)
    db.session.execute(text("DELETE FROM security WHERE id = :id"), {"id": drop_id})
    db.session.flush()

    try:
        from services.audit_service import log_audit_event

        log_audit_event(
            "security.merge_duplicate",
            resource_type="security",
            resource_id=str(keep_id),
            details=report,
            commit=False,
        )
    except Exception:
        logger.debug("audit log skipped for security merge", exc_info=True)

    logger.info(
        "Merged security drop_id=%s (%s) into keep_id=%s (%s)",
        drop_id,
        report["drop_name"],
        keep_id,
        report["keep_name"],
    )
    return report


def _repoint_simple(model, keep_id: int, drop_id: int) -> int:
    rows = model.query.filter_by(security_id=drop_id).all()
    n = 0
    for row in rows:
        row.security_id = keep_id
        n += 1
    return n


def _merge_holdings(keep_id: int, drop_id: int, report: Dict[str, Any]) -> None:
    drop_rows = Holding.query.filter_by(security_id=drop_id).all()
    moved, folded = 0, 0
    for dh in drop_rows:
        kh = Holding.query.filter_by(client_id=dh.client_id, security_id=keep_id).first()
        if kh is None:
            dh.security_id = keep_id
            moved += 1
            continue
        qty, avg = combined_holding_qty_avg(
            float(kh.quantity or 0),
            float(kh.average_price or 0),
            float(dh.quantity or 0),
            float(dh.average_price or 0),
        )
        kh.quantity = qty
        kh.average_price = avg
        db.session.delete(dh)
        folded += 1
    report["moved"]["holding"] = moved
    report["deleted_conflicts"]["holding"] = folded


def _merge_holding_snapshots(keep_id: int, drop_id: int, report: Dict[str, Any]) -> None:
    drop_rows = HoldingSnapshot.query.filter_by(security_id=drop_id).all()
    moved, folded = 0, 0
    for ds in drop_rows:
        ks = HoldingSnapshot.query.filter_by(
            portfolio_snapshot_id=ds.portfolio_snapshot_id, security_id=keep_id
        ).first()
        if ks is None:
            ds.security_id = keep_id
            moved += 1
            continue
        qty, avg = combined_holding_qty_avg(
            float(ks.quantity or 0),
            float(ks.average_price or 0),
            float(ds.quantity or 0),
            float(ds.average_price or 0),
        )
        ks.quantity = qty
        ks.average_price = avg
        db.session.delete(ds)
        folded += 1
    report["moved"]["holding_snapshot"] = moved
    report["deleted_conflicts"]["holding_snapshot"] = folded


def _merge_historical_prices(keep_id: int, drop_id: int, report: Dict[str, Any]) -> None:
    keep_dates = {
        r.date for r in HistoricalPrice.query.filter_by(security_id=keep_id).all()
    }
    drop_rows = HistoricalPrice.query.filter_by(security_id=drop_id).all()
    moved, skipped = 0, 0
    for row in drop_rows:
        if row.date in keep_dates:
            db.session.delete(row)
            skipped += 1
        else:
            row.security_id = keep_id
            keep_dates.add(row.date)
            moved += 1
    report["moved"]["historical_price"] = moved
    report["deleted_conflicts"]["historical_price"] = skipped


def _merge_corporate_actions(keep_id: int, drop_id: int, report: Dict[str, Any]) -> None:
    drop_rows = CorporateAction.query.filter_by(security_id=drop_id).all()
    moved, skipped = 0, 0
    for row in drop_rows:
        clash = CorporateAction.query.filter_by(
            security_id=keep_id, action_date=row.action_date, action_type=row.action_type
        ).first()
        if clash:
            db.session.delete(row)
            skipped += 1
        else:
            row.security_id = keep_id
            moved += 1
    src_rows = CorporateAction.query.filter_by(source_security_id=drop_id).all()
    src_moved = 0
    for row in src_rows:
        row.source_security_id = keep_id
        src_moved += 1
    report["moved"]["corporate_actions"] = moved
    report["moved"]["corporate_actions_source"] = src_moved
    report["deleted_conflicts"]["corporate_actions"] = skipped


def _merge_spike_acks(keep_id: int, drop_id: int, report: Dict[str, Any]) -> None:
    drop_rows = PriceAccuracySpikeAck.query.filter_by(security_id=drop_id).all()
    moved, skipped = 0, 0
    for row in drop_rows:
        clash = PriceAccuracySpikeAck.query.filter_by(
            security_id=keep_id, date_prev=row.date_prev, date_next=row.date_next
        ).first()
        if clash:
            db.session.delete(row)
            skipped += 1
        else:
            row.security_id = keep_id
            moved += 1
    report["moved"]["price_accuracy_spike_ack"] = moved
    report["deleted_conflicts"]["price_accuracy_spike_ack"] = skipped


def _merge_behavior_patterns(keep_id: int, drop_id: int, report: Dict[str, Any]) -> None:
    drop_rows = ClientBehaviorPattern.query.filter_by(security_id=drop_id).all()
    moved, skipped = 0, 0
    for row in drop_rows:
        clash = ClientBehaviorPattern.query.filter_by(
            client_id=row.client_id,
            check_category=row.check_category,
            pattern_type=row.pattern_type,
            security_id=keep_id,
        ).first()
        if clash:
            db.session.delete(row)
            skipped += 1
        else:
            row.security_id = keep_id
            moved += 1
    report["moved"]["client_behavior_pattern"] = moved
    report["deleted_conflicts"]["client_behavior_pattern"] = skipped


def _merge_allocations(keep_id: int, drop_id: int, report: Dict[str, Any]) -> None:
    # Prod model_allocation may lack ORM columns (e.g. created_at); use SQL.
    sa_moved, sa_skipped = _repoint_or_delete_on_pair(
        "security_allocation",
        keep_id,
        drop_id,
        match_cols=("security_model_id",),
    )
    ma_moved, ma_skipped = _repoint_or_delete_on_pair(
        "model_allocation",
        keep_id,
        drop_id,
        match_cols=("customization_id", "security_model_id"),
    )
    report["moved"]["security_allocation"] = sa_moved
    report["deleted_conflicts"]["security_allocation"] = sa_skipped
    report["moved"]["model_allocation"] = ma_moved
    report["deleted_conflicts"]["model_allocation"] = ma_skipped


def _repoint_or_delete_on_pair(
    table: str,
    keep_id: int,
    drop_id: int,
    match_cols: Tuple[str, ...],
) -> Tuple[int, int]:
    """Repoint security_id; if a keep-row already matches, delete the drop-row."""
    bind = db.engine
    if bind is None:
        return 0, 0
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return 0, 0
    cols = {c["name"] for c in inspector.get_columns(table)}
    if "security_id" not in cols:
        return 0, 0
    usable_match = [c for c in match_cols if c in cols]
    if not usable_match:
        sql = text(f"UPDATE `{table}` SET security_id = :keep WHERE security_id = :drop")
        result = db.session.execute(sql, {"keep": keep_id, "drop": drop_id})
        return int(result.rowcount or 0), 0

    clash_sql = text(
        f"""
        DELETE d FROM `{table}` d
        INNER JOIN `{table}` k
          ON k.security_id = :keep
         AND d.security_id = :drop
         AND {' AND '.join(f'k.`{c}` = d.`{c}`' for c in usable_match)}
        """
    )
    skipped = db.session.execute(clash_sql, {"keep": keep_id, "drop": drop_id})
    skipped_n = int(skipped.rowcount or 0)
    moved = db.session.execute(
        text(f"UPDATE `{table}` SET security_id = :keep WHERE security_id = :drop"),
        {"keep": keep_id, "drop": drop_id},
    )
    return int(moved.rowcount or 0), skipped_n



def _repoint_leftover_columns(keep_id: int, drop_id: int) -> Dict[str, int]:
    """Best-effort UPDATE for any other table.column named security_id / source_security_id."""
    moved: Dict[str, int] = {}
    bind = db.engine
    if bind is None:
        return moved
    inspector = inspect(bind)
    for table in inspector.get_table_names():
        if table in _ORM_TABLES:
            continue
        cols = {c["name"] for c in inspector.get_columns(table)}
        for col in ("security_id", "source_security_id"):
            if col not in cols:
                continue
            sql = text(f"UPDATE `{table}` SET `{col}` = :keep WHERE `{col}` = :drop")
            try:
                with db.session.begin_nested():
                    result = db.session.execute(sql, {"keep": keep_id, "drop": drop_id})
                    n = result.rowcount or 0
                    if n:
                        moved[f"{table}.{col}"] = n
            except IntegrityError:
                del_sql = text(f"DELETE FROM `{table}` WHERE `{col}` = :drop")
                result = db.session.execute(del_sql, {"drop": drop_id})
                moved[f"{table}.{col}_deleted"] = result.rowcount or 0
    return moved
