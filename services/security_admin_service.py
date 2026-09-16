"""Securities maintenance UI: list, CRUD, hot stocks, daily update marker."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from extensions import db
from models import Security, SecurityDailyUpdate


def build_securities_list_context() -> dict[str, Any]:
    securities = (
        Security.query.options(joinedload(Security.asset_class))
        .order_by(func.lower(Security.symbol))
        .all()
    )
    daily_update = SecurityDailyUpdate.query.filter_by(update_date=date.today()).first()
    return {"securities": securities, "daily_update": daily_update, "today": datetime.now()}


def create_security_from_add_form(form: Any, user_id: int) -> tuple[bool, str | None]:
    symbol = (form.get("symbol") or "").strip().upper()
    name = (form.get("name") or "").strip()
    if not symbol or not name:
        return False, "Symbol and name are required."
    try:
        asset_class_id = int(form.get("asset_class_id") or 0)
    except (TypeError, ValueError):
        asset_class_id = 0
    if not asset_class_id:
        return False, "Asset class is required."
    sec_type = (form.get("security_type") or "STOCK").strip().upper()
    try:
        refresh_interval = int(form.get("refresh_interval") or 300)
    except (TypeError, ValueError):
        refresh_interval = 300
    cp_raw = form.get("current_price")
    current_price = None
    if cp_raw not in (None, ""):
        try:
            current_price = float(cp_raw)
        except (TypeError, ValueError):
            return False, "Invalid current price."

    meta_raw = (form.get("meta_data") or "").strip()
    meta_data = None
    if meta_raw:
        try:
            json.loads(meta_raw)
            meta_data = meta_raw
        except json.JSONDecodeError:
            return False, "Additional information must be valid JSON."

    if Security.query.filter(func.upper(Security.symbol) == symbol).first():
        return False, f"A security with symbol {symbol} already exists."

    sec = Security(
        symbol=symbol,
        name=name,
        asset_class_id=asset_class_id,
        security_type=sec_type,
        current_price=current_price,
        refresh_interval=refresh_interval,
        meta_data=meta_data,
        created_by=user_id,
    )
    db.session.add(sec)
    try:
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        return False, str(e) or "Could not save security (duplicate or constraint)."
    return True, None


def update_security_from_validated_form(security: Security, form: Any, raw_form: Any) -> tuple[bool, str | None]:
    """Persist edits; ``form`` is a validated ``SecurityForm``; ``raw_form`` is request.form (refresh_interval)."""
    security.symbol = (form.symbol.data or "").strip().upper()
    security.name = (form.name.data or "").strip()
    security.security_type = (form.type.data or "STOCK").strip().upper()
    security.asset_class_id = int(form.asset_class_id.data)
    if form.current_price.data is not None:
        security.current_price = form.current_price.data
    desc = (form.description.data or "").strip()
    security.meta_data = desc or None

    try:
        mins = int(raw_form.get("refresh_interval") or 5)
        mins = max(1, min(60, mins))
        security.refresh_interval = mins * 60
    except (TypeError, ValueError):
        pass

    security.updated_at = datetime.utcnow()
    try:
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        return False, str(e) or "Could not update security."
    return True, None


def touch_security_refresh(security_id: int) -> tuple[bool, str | None]:
    sec = Security.query.get(security_id)
    if not sec:
        return False, "Security not found."
    sec.last_updated = datetime.utcnow()
    db.session.commit()
    return True, None


def record_market_close_update(user_id: int) -> tuple[bool, str | None]:
    today = date.today()
    existing = SecurityDailyUpdate.query.filter_by(update_date=today).first()
    count = Security.query.count()
    if existing:
        existing.update_type = "market_close"
        existing.updated_securities_count = count
        existing.created_at = datetime.utcnow()
        existing.created_by = user_id
    else:
        db.session.add(
            SecurityDailyUpdate(
                update_date=today,
                update_type="market_close",
                updated_securities_count=count,
                created_by=user_id,
            )
        )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False, "Could not record daily update."
    return True, None


def try_delete_security(security_id: int) -> tuple[bool, str | None]:
    sec = Security.query.get(security_id)
    if not sec:
        return False, "Security not found."
    try:
        db.session.delete(sec)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False, "Cannot delete: this security is referenced by transactions or other records."
    except Exception as e:
        db.session.rollback()
        return False, str(e) or "Delete failed."
    return True, None


def _is_hot(s: Security) -> bool:
    return bool(getattr(s, "is_hot_stock", False)) or int(getattr(s, "hot_stock_rating", 0) or 0) > 0


def build_hot_stocks_context() -> dict[str, Any]:
    all_secs = (
        Security.query.options(joinedload(Security.asset_class))
        .order_by(func.lower(Security.symbol))
        .all()
    )
    hot = [s for s in all_secs if _is_hot(s)]
    hot_ids = {s.id for s in hot}
    rest = [s for s in all_secs if s.id not in hot_ids]
    return {"hot_stocks": hot, "all_securities": rest}


def mark_hot(security_id: int, hot: bool) -> tuple[bool, str | None]:
    sec = Security.query.get(security_id)
    if not sec:
        return False, "Security not found."
    if hot:
        sec.is_hot_stock = True
        if not int(getattr(sec, "hot_stock_rating", 0) or 0):
            sec.hot_stock_rating = 1
    else:
        sec.is_hot_stock = False
        sec.hot_stock_rating = 0
    sec.updated_at = datetime.utcnow()
    db.session.commit()
    return True, None


def bulk_mark_hot(security_ids: list[int]) -> tuple[int, str | None]:
    n = 0
    for sid in security_ids:
        ok, _ = mark_hot(int(sid), True)
        if ok:
            n += 1
    return n, None
