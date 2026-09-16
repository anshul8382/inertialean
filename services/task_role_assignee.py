"""
Resolve a concrete user_id for policy roles used in task / alert assignment.

Role keys (string, not tied to display names in DB):
- admin: first active user with is_admin=True
- ops_manager: ops/manager role via Role table or legacy User.role, then AlertService fallback

Used by TaskAssignmentService fallbacks and by migrations that seed TaskAssignmentRule rows.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func

logger = logging.getLogger(__name__)


def resolve_user_id_for_assignment_role(role_key: str) -> Optional[int]:
    """
    Map a policy role to a user id. Returns None if no candidate exists.

    role_key:
      - admin
      - ops_manager
    """
    rk = (role_key or "").strip().lower()
    if rk not in ("admin", "ops_manager"):
        logger.warning("Unknown assignment role_key: %s", role_key)
        return None

    from models import Role, User

    if rk == "admin":
        u = (
            User.query.filter_by(is_admin=True, is_active=True)
            .order_by(User.id.asc())
            .first()
        )
        return u.id if u else None

    # ops_manager, then sales_ops_manager (same ops assignee pool for rules / fallbacks)
    for role_name in ("ops_manager", "sales_ops_manager"):
        r = (
            Role.query.filter(
                func.lower(Role.name) == role_name,
                Role.is_active.is_(True),
            )
            .order_by(Role.id.asc())
            .first()
        )
        if r:
            u = (
                User.query.filter_by(role_id=r.id, is_active=True)
                .order_by(User.id.asc())
                .first()
            )
            if u:
                return u.id

    u = (
        User.query.filter(User.role == "ops_manager", User.is_active.is_(True))
        .order_by(User.id.asc())
        .first()
    )
    if u:
        return u.id

    r = (
        Role.query.filter(
            func.lower(Role.name) == "manager",
            Role.is_active.is_(True),
        )
        .order_by(Role.id.asc())
        .first()
    )
    if r:
        u = (
            User.query.filter_by(role_id=r.id, is_active=True)
            .order_by(User.id.asc())
            .first()
        )
        if u:
            return u.id

    u = (
        User.query.filter(User.role == "manager", User.is_active.is_(True))
        .order_by(User.id.asc())
        .first()
    )
    if u:
        return u.id

    try:
        from alert_service import AlertService

        oid = AlertService._get_ops_manager_user_id()
        if oid:
            return oid
    except Exception as e:
        logger.warning("ops_manager fallback via AlertService failed: %s", e)

    return None
