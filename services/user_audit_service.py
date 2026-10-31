"""Per-user audit history helpers (login days + event list)."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set

from services.db_cutover import audit_log_table_exists


def record_login_success(user, *, channel: str, commit: bool = True) -> None:
    """Set last_login and write login_success audit row."""
    from datetime import datetime as dt

    from services.audit_service import log_audit_event

    if user is not None:
        user.last_login = dt.utcnow()
    log_audit_event(
        "login_success",
        user_id=getattr(user, "id", None),
        details={"channel": channel},
        commit=commit,
    )


def record_login_failure(
    *,
    email: Optional[str] = None,
    user_id: Optional[int] = None,
    channel: str = "web_session",
    reason: str = "invalid_credentials",
) -> None:
    from services.audit_service import log_audit_event

    details: Dict[str, Any] = {"channel": channel, "reason": reason}
    if email:
        details["email"] = (email or "").strip().lower()[:120]
    log_audit_event("login_failure", user_id=user_id, details=details)


def record_logout(user_id: Optional[int], *, channel: str = "web_session") -> None:
    from services.audit_service import log_audit_event

    log_audit_event("logout", user_id=user_id, details={"channel": channel})


def list_user_audit_events(
    user_id: int,
    *,
    limit: int = 100,
    action: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> List[Any]:
    if not audit_log_table_exists():
        return []
    from models.audit_log import AuditLog

    q = AuditLog.query.filter(AuditLog.user_id == int(user_id))
    if action:
        q = q.filter(AuditLog.action == action)
    if since is not None:
        q = q.filter(AuditLog.created_at >= since)
    if until is not None:
        q = q.filter(AuditLog.created_at < until)
    return q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(max(1, min(limit, 500))).all()


def login_days_in_month(user_id: int, year: int, month: int) -> Set[date]:
    """Calendar dates in YYYY-MM where the user had at least one login_success."""
    if not audit_log_table_exists():
        return set()
    from models.audit_log import AuditLog

    start = datetime(year, month, 1)
    last_day = monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59)
    rows = (
        AuditLog.query.filter(
            AuditLog.user_id == int(user_id),
            AuditLog.action == "login_success",
            AuditLog.created_at >= start,
            AuditLog.created_at <= end,
        )
        .with_entities(AuditLog.created_at)
        .all()
    )
    out: Set[date] = set()
    for (ts,) in rows:
        if ts is None:
            continue
        out.add(ts.date() if isinstance(ts, datetime) else ts)
    return out


def month_activity_summary(user_id: int, year: int, month: int) -> Dict[str, Any]:
    days = sorted(login_days_in_month(user_id, year, month))
    return {
        "year": year,
        "month": month,
        "login_days": days,
        "login_day_count": len(days),
        "days_in_month": monthrange(year, month)[1],
    }
