"""
Assign clients and leads to users (client.advisor_id + lead.user_id).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, or_

from extensions import db
from models import Client, ClientAdvisorAssignment, Lead, User

logger = logging.getLogger(__name__)


def list_assignable_users() -> List[User]:
    """Active users who may be assigned as client advisor."""
    return (
        User.query.filter_by(is_active=True)
        .order_by(func.lower(User.username))
        .all()
    )


def _assignment_clients_query(
    *,
    search: Optional[str] = None,
    advisor_id: Optional[int] = None,
    unassigned_only: bool = False,
    active_only: bool = True,
):
    query = Client.query

    if active_only:
        query = query.filter(Client.is_active.is_(True))

    if search:
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(Client.name).like(term),
                func.lower(Client.email).like(term),
            )
        )

    if advisor_id:
        query = query.filter(Client.advisor_id == advisor_id)

    if unassigned_only:
        query = query.filter(Client.advisor_id.is_(None))

    return query.order_by(func.lower(Client.name))


def get_client_ids_for_assignment(
    *,
    search: Optional[str] = None,
    advisor_id: Optional[int] = None,
    unassigned_only: bool = False,
    active_only: bool = True,
    max_ids: int = 5000,
) -> Tuple[List[int], bool]:
    """
    All client IDs matching filters (for bulk assign).
    Returns (ids, truncated) if more than max_ids exist.
    """
    query = _assignment_clients_query(
        search=search,
        advisor_id=advisor_id,
        unassigned_only=unassigned_only,
        active_only=active_only,
    )
    rows = query.with_entities(Client.id).limit(max_ids + 1).all()
    ids = [row[0] for row in rows]
    if len(ids) > max_ids:
        return ids[:max_ids], True
    return ids, False


def get_clients_for_assignment(
    *,
    search: Optional[str] = None,
    advisor_id: Optional[int] = None,
    unassigned_only: bool = False,
    active_only: bool = True,
    page: int = 1,
    per_page: int = 50,
) -> Tuple[List[Dict[str, Any]], Any]:
    """
    Paginated client rows for the assignment UI.
    Returns (rows, pagination object).
    """
    query = _assignment_clients_query(
        search=search,
        advisor_id=advisor_id,
        unassigned_only=unassigned_only,
        active_only=active_only,
    )

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    advisor_ids = {c.advisor_id for c in pagination.items if c.advisor_id}
    advisors_by_id: Dict[int, User] = {}
    if advisor_ids:
        for u in User.query.filter(User.id.in_(advisor_ids)).all():
            advisors_by_id[u.id] = u

    rows: List[Dict[str, Any]] = []
    for client in pagination.items:
        advisor = advisors_by_id.get(client.advisor_id) if client.advisor_id else None
        rows.append(
            {
                "id": client.id,
                "name": client.name,
                "email": client.email,
                "is_active": bool(client.is_active),
                "advisor_id": client.advisor_id,
                "advisor_label": _user_label(advisor) if advisor else None,
            }
        )

    return rows, pagination


def assign_client_to_advisor(
    client_id: int,
    advisor_id: int,
    assigned_by_user_id: int,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Set client's advisor and record assignment history.
    Returns {"ok": True, "client": ..., "advisor": ...} or {"ok": False, "error": ...}.
    """
    client = Client.query.get(client_id)
    if not client:
        return {"ok": False, "error": "Client not found"}

    advisor = User.query.get(advisor_id)
    if not advisor or not advisor.is_active:
        return {"ok": False, "error": "Advisor user not found or inactive"}

    if client.advisor_id == advisor_id:
        return {
            "ok": True,
            "unchanged": True,
            "client": client,
            "advisor": advisor,
            "message": "Client is already assigned to this advisor.",
        }

    try:
        existing = ClientAdvisorAssignment.query.filter_by(
            client_id=client_id, is_active=True
        ).first()
        if existing:
            existing.is_active = False

        db.session.add(
            ClientAdvisorAssignment(
                client_id=client_id,
                advisor_id=advisor_id,
                assigned_by=assigned_by_user_id,
                is_active=True,
                notes=(notes or "").strip() or None,
                assigned_at=datetime.utcnow(),
            )
        )
        client.advisor_id = advisor_id
        db.session.commit()

        try:
            from services.audit_service import log_audit_event

            log_audit_event(
                "client_advisor_assigned",
                user_id=assigned_by_user_id,
                client_id=client_id,
                resource_type="client",
                resource_id=str(client_id),
                details={
                    "advisor_id": advisor_id,
                    "advisor_username": advisor.username,
                    "notes": notes,
                },
            )
        except Exception as audit_err:
            logger.warning("Advisor assignment audit log failed: %s", audit_err)

        return {
            "ok": True,
            "client": client,
            "advisor": advisor,
            "message": f'Assigned "{client.name}" to {advisor.username}.',
        }
    except Exception as e:
        db.session.rollback()
        logger.exception("assign_client_to_advisor failed client_id=%s", client_id)
        return {"ok": False, "error": str(e)}


def bulk_assign_clients_to_advisor(
    client_ids: List[int],
    advisor_id: int,
    assigned_by_user_id: int,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Assign many clients to one advisor. Returns counts and any errors."""
    if not client_ids:
        return {
            "ok": False,
            "error": "No clients selected.",
            "assigned": 0,
            "unchanged": 0,
            "failed": 0,
        }

    advisor = User.query.get(advisor_id)
    if not advisor or not advisor.is_active:
        return {
            "ok": False,
            "error": "Advisor user not found or inactive",
            "assigned": 0,
            "unchanged": 0,
            "failed": 0,
        }

    assigned = 0
    unchanged = 0
    failed = 0
    errors: List[str] = []

    for client_id in client_ids:
        result = assign_client_to_advisor(
            client_id,
            advisor_id,
            assigned_by_user_id,
            notes=notes,
        )
        if result.get("ok") and result.get("unchanged"):
            unchanged += 1
        elif result.get("ok"):
            assigned += 1
        else:
            failed += 1
            err = result.get("error") or "Unknown error"
            if len(errors) < 5:
                errors.append(f"Client #{client_id}: {err}")

    total = len(client_ids)
    ok = failed == 0
    if assigned:
        message = (
            f'Assigned {assigned} client(s) to {advisor.username}.'
            + (f" {unchanged} already had this advisor." if unchanged else "")
            + (f" {failed} failed." if failed else "")
        )
    elif unchanged == total:
        message = f"All {total} selected client(s) were already assigned to {advisor.username}."
        ok = True
    elif failed:
        message = f"Bulk assign failed for {failed} of {total} client(s)."
    else:
        message = "No assignments were made."

    return {
        "ok": ok,
        "assigned": assigned,
        "unchanged": unchanged,
        "failed": failed,
        "errors": errors,
        "message": message,
        "advisor": advisor,
    }


def _assignment_leads_query(
    *,
    search: Optional[str] = None,
    owner_id: Optional[int] = None,
    active_only: bool = True,
):
    query = Lead.query

    if active_only:
        query = query.filter(Lead.is_active.is_(True))

    if search:
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(Lead.name).like(term),
                func.lower(Lead.email).like(term),
                func.lower(Lead.phone).like(term),
            )
        )

    if owner_id:
        query = query.filter(Lead.user_id == owner_id)

    return query.order_by(Lead.created_at.desc())


def get_lead_ids_for_assignment(
    *,
    search: Optional[str] = None,
    owner_id: Optional[int] = None,
    active_only: bool = True,
    max_ids: int = 5000,
) -> Tuple[List[int], bool]:
    query = _assignment_leads_query(
        search=search,
        owner_id=owner_id,
        active_only=active_only,
    )
    rows = query.with_entities(Lead.id).limit(max_ids + 1).all()
    ids = [row[0] for row in rows]
    if len(ids) > max_ids:
        return ids[:max_ids], True
    return ids, False


def get_leads_for_assignment(
    *,
    search: Optional[str] = None,
    owner_id: Optional[int] = None,
    active_only: bool = True,
    page: int = 1,
    per_page: int = 50,
) -> Tuple[List[Dict[str, Any]], Any]:
    query = _assignment_leads_query(
        search=search,
        owner_id=owner_id,
        active_only=active_only,
    )
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    owner_ids = {lead.user_id for lead in pagination.items if lead.user_id}
    owners_by_id: Dict[int, User] = {}
    if owner_ids:
        for u in User.query.filter(User.id.in_(owner_ids)).all():
            owners_by_id[u.id] = u

    rows: List[Dict[str, Any]] = []
    for lead in pagination.items:
        owner = owners_by_id.get(lead.user_id) if lead.user_id else None
        rows.append(
            {
                "id": lead.id,
                "name": lead.name,
                "email": lead.email,
                "phone": lead.phone,
                "status": lead.status,
                "is_active": bool(lead.is_active),
                "owner_id": lead.user_id,
                "owner_label": _user_label(owner) if owner else None,
            }
        )

    return rows, pagination


def assign_lead_to_user(
    lead_id: int,
    user_id: int,
    assigned_by_user_id: int,
) -> Dict[str, Any]:
    lead = Lead.query.get(lead_id)
    if not lead:
        return {"ok": False, "error": "Lead not found"}

    user = User.query.get(user_id)
    if not user or not user.is_active:
        return {"ok": False, "error": "User not found or inactive"}

    if lead.user_id == user_id:
        return {
            "ok": True,
            "unchanged": True,
            "lead": lead,
            "user": user,
            "message": f'Lead "{lead.name}" is already assigned to {user.username}.',
        }

    try:
        lead.user_id = user_id
        lead.updated_at = datetime.utcnow()
        db.session.commit()

        try:
            from services.audit_service import log_audit_event

            log_audit_event(
                "lead_assigned",
                user_id=assigned_by_user_id,
                resource_type="lead",
                resource_id=str(lead_id),
                details={
                    "owner_id": user_id,
                    "owner_username": user.username,
                },
            )
        except Exception as audit_err:
            logger.warning("Lead assignment audit log failed: %s", audit_err)

        return {
            "ok": True,
            "lead": lead,
            "user": user,
            "message": f'Assigned lead "{lead.name}" to {user.username}.',
        }
    except Exception as e:
        db.session.rollback()
        logger.exception("assign_lead_to_user failed lead_id=%s", lead_id)
        return {"ok": False, "error": str(e)}


def bulk_assign_leads_to_user(
    lead_ids: List[int],
    user_id: int,
    assigned_by_user_id: int,
) -> Dict[str, Any]:
    if not lead_ids:
        return {
            "ok": False,
            "error": "No leads selected.",
            "assigned": 0,
            "unchanged": 0,
            "failed": 0,
        }

    user = User.query.get(user_id)
    if not user or not user.is_active:
        return {
            "ok": False,
            "error": "User not found or inactive",
            "assigned": 0,
            "unchanged": 0,
            "failed": 0,
        }

    assigned = 0
    unchanged = 0
    failed = 0
    errors: List[str] = []

    for lead_id in lead_ids:
        result = assign_lead_to_user(lead_id, user_id, assigned_by_user_id)
        if result.get("ok") and result.get("unchanged"):
            unchanged += 1
        elif result.get("ok"):
            assigned += 1
        else:
            failed += 1
            err = result.get("error") or "Unknown error"
            if len(errors) < 5:
                errors.append(f"Lead #{lead_id}: {err}")

    total = len(lead_ids)
    ok = failed == 0
    if assigned:
        message = (
            f"Assigned {assigned} lead(s) to {user.username}."
            + (f" {unchanged} unchanged." if unchanged else "")
            + (f" {failed} failed." if failed else "")
        )
    elif unchanged == total:
        message = f"All {total} selected lead(s) already belong to {user.username}."
        ok = True
    elif failed:
        message = f"Bulk assign failed for {failed} of {total} lead(s)."
    else:
        message = "No lead assignments were made."

    return {
        "ok": ok,
        "assigned": assigned,
        "unchanged": unchanged,
        "failed": failed,
        "errors": errors,
        "message": message,
        "user": user,
    }


def _user_label(user: User) -> str:
    name = (getattr(user, "username", None) or "").strip()
    email = (getattr(user, "email", None) or "").strip()
    if name and email:
        return f"{name} ({email})"
    return name or email or f"User #{user.id}"
