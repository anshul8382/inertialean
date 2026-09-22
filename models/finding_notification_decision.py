"""Per-user decisions on findings/reviews for the in-app notification centre.

One row per (user, entity). Decisions: snoozed | ignored | committed.
Ignore removes the nudge only — the finding/review stays on reports.
"""
from __future__ import annotations

from datetime import datetime

from extensions import db


class FindingNotificationDecision(db.Model):
    __tablename__ = "finding_notification_decision"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type = db.Column(db.String(20), nullable=False)  # finding | review
    entity_id = db.Column(db.Integer, nullable=False)
    decision = db.Column(db.String(20), nullable=False)  # snoozed | ignored | committed
    snooze_until = db.Column(db.DateTime, nullable=True)
    reason = db.Column(db.Text)
    ops_task_id = db.Column(
        db.Integer, db.ForeignKey("ops_task.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "entity_type", "entity_id", name="uq_finding_notif_user_entity"
        ),
        db.Index("ix_finding_notif_user_decision", "user_id", "decision"),
    )
