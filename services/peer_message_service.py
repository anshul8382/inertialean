"""Peer short messages via the notification bell."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from extensions import db
from services.db_cutover import peer_messages_enabled

logger = logging.getLogger(__name__)

MAX_BODY = 280


def list_recipients(exclude_user_id: int) -> List[Dict[str, Any]]:
    from models import User

    users = (
        User.query.filter(User.is_active == True, User.id != exclude_user_id)  # noqa: E712
        .order_by(User.username.asc())
        .all()
    )
    out = []
    for u in users:
        label = u.username
        if getattr(u, "role", None):
            label = f"{u.username} ({u.role})"
        out.append({"id": u.id, "username": u.username, "label": label})
    return out


def send_message(*, from_user, to_user_id: int, body: str) -> Dict[str, Any]:
    if not peer_messages_enabled():
        return {"ok": False, "error": "peer_messages_disabled"}
    body = (body or "").strip()
    if not body:
        return {"ok": False, "error": "empty_body"}
    if len(body) > MAX_BODY:
        return {"ok": False, "error": "body_too_long", "max": MAX_BODY}
    to_user_id = int(to_user_id)
    if to_user_id == from_user.id:
        return {"ok": False, "error": "cannot_message_self"}

    from models import User
    from models.user_peer_message import UserPeerMessage

    to_user = User.query.get(to_user_id)
    if not to_user or not to_user.is_active:
        return {"ok": False, "error": "recipient_not_found"}

    row = UserPeerMessage(
        from_user_id=from_user.id,
        to_user_id=to_user_id,
        body=body,
        status="open",
    )
    db.session.add(row)
    db.session.commit()
    return {
        "ok": True,
        "id": row.id,
        "to_user_id": to_user_id,
        "to_username": to_user.username,
    }


def list_inbox(user, *, view: str = "open") -> Dict[str, Any]:
    if not peer_messages_enabled():
        return {"enabled": False, "count": 0, "items": []}

    from models import User
    from models.user_peer_message import UserPeerMessage

    now = datetime.utcnow()
    view = (view or "open").lower()
    q = UserPeerMessage.query.filter_by(to_user_id=user.id)
    if view == "snoozed":
        rows = (
            q.filter_by(status="snoozed")
            .filter(UserPeerMessage.snooze_until.isnot(None))
            .filter(UserPeerMessage.snooze_until > now)
            .order_by(UserPeerMessage.created_at.desc())
            .all()
        )
    else:
        # open + expired snoozes
        rows = (
            q.filter(UserPeerMessage.status.in_(["open", "snoozed"]))
            .order_by(UserPeerMessage.created_at.desc())
            .all()
        )
        filtered = []
        for r in rows:
            if r.status == "snoozed" and r.snooze_until and r.snooze_until > now:
                continue
            filtered.append(r)
        rows = filtered

    items = []
    for r in rows:
        sender = User.query.get(r.from_user_id)
        from_name = sender.username if sender else f"User #{r.from_user_id}"
        items.append(
            {
                "signal_id": "MSG",
                "entity_type": "peer_message",
                "entity_id": r.id,
                "title": f"Message from {from_name}",
                "nudge_text": r.body,
                "severity": "info",
                "urgent": False,
                "age_label": _age_label(r.created_at, now),
                "ignore_allowed": False,
                "member_count": 1,
                "detail_lines": [],
                "app_path": None,
                "facts": {
                    "from_user_id": r.from_user_id,
                    "from_username": from_name,
                    "member_ids": [r.id],
                    "snooze_until": (
                        r.snooze_until.isoformat() + "Z" if r.snooze_until else None
                    ),
                    "done_label": "Done",
                },
            }
        )
    return {"enabled": True, "count": len(items), "items": items}


def open_count(user) -> int:
    if not peer_messages_enabled():
        return 0
    return list_inbox(user, view="open")["count"]


def apply_decision(
    user,
    *,
    message_id: int,
    decision: str,
    snooze_until: Optional[datetime] = None,
) -> Dict[str, Any]:
    if not peer_messages_enabled():
        return {"ok": False, "error": "peer_messages_disabled"}

    from models.user_peer_message import UserPeerMessage

    row = UserPeerMessage.query.filter_by(id=int(message_id), to_user_id=user.id).first()
    if not row:
        return {"ok": False, "error": "not_found"}

    decision = (decision or "").lower().strip()
    if decision in ("done", "committed"):
        row.status = "done"
        row.snooze_until = None
    elif decision == "snoozed":
        row.status = "snoozed"
        row.snooze_until = snooze_until or (datetime.utcnow() + timedelta(hours=4))
    elif decision == "unsnooze":
        row.status = "open"
        row.snooze_until = None
    else:
        return {"ok": False, "error": "invalid_decision"}
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return {"ok": True, "decision": decision, "id": row.id}


def _age_label(created: datetime, now: datetime) -> str:
    if not created:
        return ""
    mins = int((now - created).total_seconds() / 60)
    if mins < 60:
        return f"{mins}m ago"
    hours = mins / 60
    if hours < 48:
        return f"{hours:.0f}h ago"
    return f"{hours / 24:.1f}d ago"
