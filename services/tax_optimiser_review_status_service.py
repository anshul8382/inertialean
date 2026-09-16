"""Persist interactive tax optimiser review state per advisor × client × FY (saved vs client email sent)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from extensions import db

_LOG = logging.getLogger(__name__)

STATUS_SAVED = "saved"
STATUS_EMAIL_SENT = "email_sent"


def _fy_start_year_from_session(session_data: Dict[str, Any]) -> Optional[int]:
    meta = session_data.get("meta") or {}
    for src in (meta, meta.get("start_payload") or {}):
        v = src.get("fy_start_year")
        if v is None or v == "":
            continue
        try:
            y = int(v)
            if y > 1900:
                return y
        except (TypeError, ValueError):
            continue
    return None


def _scoped_client_id_from_session(session_data: Dict[str, Any]) -> Optional[int]:
    start = (session_data.get("meta") or {}).get("start_payload") or {}
    cid = start.get("client_id")
    if cid is None or cid == "":
        return None
    try:
        i = int(cid)
    except (TypeError, ValueError):
        return None
    return i if i > 0 else None


def client_ids_from_interactive_session(session_data: Dict[str, Any]) -> List[int]:
    ids: Set[int] = set()
    sc = _scoped_client_id_from_session(session_data)
    if sc is not None:
        ids.add(int(sc))
    for it in session_data.get("items") or []:
        if not isinstance(it, dict):
            continue
        cid = it.get("client_id")
        if cid is None or cid == "":
            continue
        try:
            i = int(cid)
            if i > 0:
                ids.add(i)
        except (TypeError, ValueError):
            continue
    return sorted(ids)


def record_interactive_review_saved(
    session_data: Dict[str, Any],
    *,
    advisor_user_id: int,
    saved_review_session_id: int,
) -> None:
    """Call after a review snapshot is persisted (Save button)."""
    try:
        from models import TaxOptimiserClientReviewStatus
    except Exception as ex:
        _LOG.debug("TaxOptimiserClientReviewStatus unavailable: %s", ex)
        return

    fy = _fy_start_year_from_session(session_data)
    if fy is None:
        return
    cids = client_ids_from_interactive_session(session_data)
    if not cids:
        return

    now = datetime.utcnow()
    aid = int(advisor_user_id)
    sid = int(saved_review_session_id)

    try:
        for cid in cids:
            row = (
                TaxOptimiserClientReviewStatus.query.filter_by(
                    advisor_user_id=aid,
                    client_id=int(cid),
                    fy_start_year=int(fy),
                ).first()
            )
            if row is None:
                db.session.add(
                    TaxOptimiserClientReviewStatus(
                        advisor_user_id=aid,
                        client_id=int(cid),
                        fy_start_year=int(fy),
                        review_status=STATUS_SAVED,
                        saved_at=now,
                        email_sent_at=None,
                        last_saved_review_session_id=sid,
                    )
                )
            else:
                row.saved_at = now
                row.last_saved_review_session_id = sid
                if row.review_status != STATUS_EMAIL_SENT:
                    row.review_status = STATUS_SAVED
        db.session.commit()
    except Exception as ex:
        db.session.rollback()
        _LOG.warning("record_interactive_review_saved: %s", ex)


def record_interactive_review_email_sent(
    session_data: Dict[str, Any],
    *,
    advisor_user_id: int,
) -> None:
    """Call after client email is sent successfully from interactive review."""
    try:
        from models import TaxOptimiserClientReviewStatus
    except Exception as ex:
        _LOG.debug("TaxOptimiserClientReviewStatus unavailable: %s", ex)
        return

    fy = _fy_start_year_from_session(session_data)
    if fy is None:
        return
    cids = client_ids_from_interactive_session(session_data)
    if not cids:
        return

    now = datetime.utcnow()
    aid = int(advisor_user_id)

    try:
        for cid in cids:
            row = (
                TaxOptimiserClientReviewStatus.query.filter_by(
                    advisor_user_id=aid,
                    client_id=int(cid),
                    fy_start_year=int(fy),
                ).first()
            )
            if row is None:
                db.session.add(
                    TaxOptimiserClientReviewStatus(
                        advisor_user_id=aid,
                        client_id=int(cid),
                        fy_start_year=int(fy),
                        review_status=STATUS_EMAIL_SENT,
                        saved_at=now,
                        email_sent_at=now,
                        last_saved_review_session_id=None,
                    )
                )
            else:
                row.review_status = STATUS_EMAIL_SENT
                row.email_sent_at = now
                if row.saved_at is None:
                    row.saved_at = now
        db.session.commit()
    except Exception as ex:
        db.session.rollback()
        _LOG.warning("record_interactive_review_email_sent: %s", ex)


def attach_interactive_review_status_to_report(
    report: Dict[str, Any],
    *,
    advisor_user_id: int,
) -> None:
    """Mutates each client dict with ``interactive_review`` summary."""
    try:
        from models import TaxOptimiserClientReviewStatus
    except Exception:
        return

    fy = report.get("fy_start_year")
    if fy is None:
        return
    clients: List[Dict[str, Any]] = list(report.get("clients") or [])
    ids = [int(c["client_id"]) for c in clients if c.get("client_id") is not None]
    if not ids:
        return

    aid = int(advisor_user_id)
    rows = (
        TaxOptimiserClientReviewStatus.query.filter(
            TaxOptimiserClientReviewStatus.advisor_user_id == aid,
            TaxOptimiserClientReviewStatus.fy_start_year == int(fy),
            TaxOptimiserClientReviewStatus.client_id.in_(ids),
        ).all()
    )
    by_cid = {int(r.client_id): r for r in rows}

    for c in clients:
        cid = c.get("client_id")
        if cid is None:
            continue
        r = by_cid.get(int(cid))
        if not r:
            c["interactive_review"] = {
                "status": None,
                "saved_at": None,
                "email_sent_at": None,
                "last_saved_review_session_id": None,
            }
            continue
        c["interactive_review"] = {
            "status": r.review_status,
            "saved_at": r.saved_at.isoformat() if r.saved_at else None,
            "email_sent_at": r.email_sent_at.isoformat() if r.email_sent_at else None,
            "last_saved_review_session_id": r.last_saved_review_session_id,
        }


def mark_client_reviewed_for_fy(
    *,
    advisor_user_id: int,
    client_id: int,
    fy_start_year: int,
) -> Dict[str, Any]:
    """
    Mark one advisor × client × FY row as reviewed (saved).
    Keeps email_sent intact if that stronger state is already present.
    """
    try:
        from models import TaxOptimiserClientReviewStatus
    except Exception as ex:
        return {"ok": False, "error": f"Review status model unavailable: {ex}"}

    try:
        aid = int(advisor_user_id)
        cid = int(client_id)
        fy = int(fy_start_year)
    except (TypeError, ValueError):
        return {"ok": False, "error": "advisor_user_id, client_id, fy_start_year must be integers"}
    if cid <= 0 or fy <= 1900:
        return {"ok": False, "error": "Invalid client or financial year"}

    now = datetime.utcnow()
    try:
        row = (
            TaxOptimiserClientReviewStatus.query.filter_by(
                advisor_user_id=aid,
                client_id=cid,
                fy_start_year=fy,
            ).first()
        )
        if row is None:
            row = TaxOptimiserClientReviewStatus(
                advisor_user_id=aid,
                client_id=cid,
                fy_start_year=fy,
                review_status=STATUS_SAVED,
                saved_at=now,
                email_sent_at=None,
                last_saved_review_session_id=None,
            )
            db.session.add(row)
        else:
            row.saved_at = now
            if row.review_status != STATUS_EMAIL_SENT:
                row.review_status = STATUS_SAVED
        db.session.commit()
        return {
            "ok": True,
            "interactive_review": {
                "status": row.review_status,
                "saved_at": row.saved_at.isoformat() if row.saved_at else None,
                "email_sent_at": row.email_sent_at.isoformat() if row.email_sent_at else None,
                "last_saved_review_session_id": row.last_saved_review_session_id,
            },
        }
    except Exception as ex:
        db.session.rollback()
        _LOG.warning("mark_client_reviewed_for_fy: %s", ex)
        return {"ok": False, "error": str(ex)}
