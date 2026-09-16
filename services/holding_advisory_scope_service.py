"""
Per-client security exclusions from advisory AUA metrics (billing, practice dashboard).

Holdings data and recommendation/review flows are unchanged; only scoped aggregations
apply this filter.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Literal, Optional, Set

from extensions import db

logger = logging.getLogger(__name__)

SCOPE_BILLING = "billing"
SCOPE_PRACTICE_AUA = "practice_aua"
AdvisoryScope = Literal["billing", "practice_aua"]


def _scope_column(scope: AdvisoryScope) -> str:
    if scope == SCOPE_BILLING:
        return "exclude_from_billing"
    if scope == SCOPE_PRACTICE_AUA:
        return "exclude_from_practice_aua"
    raise ValueError(f"Unknown advisory scope: {scope}")


def get_excluded_security_ids(client_id: int, scope: AdvisoryScope) -> Set[int]:
    """Security IDs excluded for one client and scope."""
    from models import ClientSecurityAdvisoryExclusion

    col = getattr(ClientSecurityAdvisoryExclusion, _scope_column(scope))
    try:
        rows = (
            ClientSecurityAdvisoryExclusion.query.filter_by(client_id=client_id)
            .filter(col.is_(True))
            .with_entities(ClientSecurityAdvisoryExclusion.security_id)
            .all()
        )
        return {int(r[0]) for r in rows}
    except Exception as e:
        logger.warning(
            "Advisory exclusion lookup failed for client %s (table may be missing): %s",
            client_id,
            e,
        )
        return set()


def get_excluded_map(client_ids: Iterable[int], scope: AdvisoryScope) -> Dict[int, Set[int]]:
    """Batch: client_id -> excluded security_ids for scope."""
    ids = [int(c) for c in client_ids if c is not None]
    if not ids:
        return {}

    from models import ClientSecurityAdvisoryExclusion

    col = getattr(ClientSecurityAdvisoryExclusion, _scope_column(scope))
    out: Dict[int, Set[int]] = {cid: set() for cid in ids}
    try:
        rows = (
            ClientSecurityAdvisoryExclusion.query.filter(ClientSecurityAdvisoryExclusion.client_id.in_(ids))
            .filter(col.is_(True))
            .with_entities(
                ClientSecurityAdvisoryExclusion.client_id,
                ClientSecurityAdvisoryExclusion.security_id,
            )
            .all()
        )
        for client_id, security_id in rows:
            out.setdefault(int(client_id), set()).add(int(security_id))
    except Exception as e:
        logger.warning("Advisory exclusion batch lookup failed (table may be missing): %s", e)
    return out


def is_excluded(client_id: int, security_id: int, scope: AdvisoryScope) -> bool:
    from models import ClientSecurityAdvisoryExclusion

    col = getattr(ClientSecurityAdvisoryExclusion, _scope_column(scope))
    row = ClientSecurityAdvisoryExclusion.query.filter_by(
        client_id=client_id, security_id=security_id
    ).first()
    return bool(row and getattr(row, col.key))


def list_exclusions(client_id: int) -> List[Dict[str, Any]]:
    from models import ClientSecurityAdvisoryExclusion, Security

    rows = (
        ClientSecurityAdvisoryExclusion.query.filter_by(client_id=client_id)
        .order_by(ClientSecurityAdvisoryExclusion.updated_at.desc())
        .all()
    )
    out: List[Dict[str, Any]] = []
    for row in rows:
        sec = Security.query.get(row.security_id)
        out.append(
            {
                "security_id": row.security_id,
                "symbol": sec.symbol if sec else None,
                "name": sec.name if sec else None,
                "exclude_from_billing": bool(row.exclude_from_billing),
                "exclude_from_practice_aua": bool(row.exclude_from_practice_aua),
                "reason": row.reason,
                "notes": row.notes,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return out


def exclusions_by_security_id(client_id: int) -> Dict[int, Dict[str, Any]]:
    return {item["security_id"]: item for item in list_exclusions(client_id)}


def set_exclusion(
    client_id: int,
    security_id: int,
    *,
    excluded: bool,
    reason: Optional[str] = None,
    user_id: Optional[int] = None,
    exclude_from_billing: bool = True,
    exclude_from_practice_aua: bool = True,
) -> Dict[str, Any]:
    """
    Mark or clear advisory AUA exclusion for one client/security pair.
    When excluded=True, upserts a row; when False, deletes it.
    """
    from models import Client, Security, ClientSecurityAdvisoryExclusion

    Client.query.get_or_404(client_id)
    Security.query.get_or_404(security_id)

    row = ClientSecurityAdvisoryExclusion.query.filter_by(
        client_id=client_id, security_id=security_id
    ).first()

    if not excluded:
        if row:
            db.session.delete(row)
            db.session.commit()
        return {"security_id": security_id, "excluded": False}

    if row is None:
        row = ClientSecurityAdvisoryExclusion(
            client_id=client_id,
            security_id=security_id,
            created_by=user_id,
        )
        db.session.add(row)

    row.exclude_from_billing = exclude_from_billing
    row.exclude_from_practice_aua = exclude_from_practice_aua
    if reason is not None:
        row.reason = (reason or "").strip() or None
    row.updated_at = datetime.utcnow()
    db.session.commit()

    return {
        "security_id": security_id,
        "excluded": True,
        "exclude_from_billing": row.exclude_from_billing,
        "exclude_from_practice_aua": row.exclude_from_practice_aua,
        "reason": row.reason,
    }


def holding_security_id(holding: Dict[str, Any]) -> Optional[int]:
    sid = holding.get("security_id")
    if sid is not None and str(sid).isdigit():
        return int(sid)
    return None


def filter_portfolio_holdings(
    holdings: List[Dict[str, Any]],
    excluded_security_ids: Set[int],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (included, excluded) holding dicts."""
    included: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for h in holdings or []:
        sid = holding_security_id(h)
        if sid is not None and sid in excluded_security_ids:
            excluded.append(h)
        else:
            included.append(h)
    return included, excluded


def sum_holding_values(holdings: Iterable[Dict[str, Any]]) -> float:
    total = 0.0
    for h in holdings:
        val = h.get("current_value")
        if val is None:
            val = h.get("value")
        try:
            total += float(val or 0)
        except (TypeError, ValueError):
            continue
    return total


def merge_security_exclusions(keep_id: int, drop_id: int, report: Dict[str, Any]) -> None:
    """Repoint drop_id exclusions to keep_id during security merge."""
    from models import ClientSecurityAdvisoryExclusion

    drop_rows = ClientSecurityAdvisoryExclusion.query.filter_by(security_id=drop_id).all()
    moved, skipped = 0, 0
    for row in drop_rows:
        clash = ClientSecurityAdvisoryExclusion.query.filter_by(
            client_id=row.client_id, security_id=keep_id
        ).first()
        if clash:
            db.session.delete(row)
            skipped += 1
        else:
            row.security_id = keep_id
            moved += 1
    report.setdefault("moved", {})["client_security_advisory_exclusion"] = moved
    report.setdefault("deleted_conflicts", {})["client_security_advisory_exclusion"] = skipped
