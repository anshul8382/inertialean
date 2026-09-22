"""Short peer-to-peer messages delivered via the notification bell."""
from __future__ import annotations

from datetime import datetime

from extensions import db


class UserPeerMessage(db.Model):
    __tablename__ = "user_peer_message"

    id = db.Column(db.Integer, primary_key=True)
    from_user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body = db.Column(db.String(280), nullable=False)
    # open | snoozed | done
    status = db.Column(db.String(20), nullable=False, default="open", index=True)
    snooze_until = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        db.Index("ix_peer_msg_to_status", "to_user_id", "status"),
    )
