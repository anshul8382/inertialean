"""Frozen SEBI IA clients/fees/AUA statement snapshots (quarter / half / year)."""
from __future__ import annotations

from datetime import datetime

from extensions import db


class IaRegulatorySnapshot(db.Model):
    __tablename__ = "ia_regulatory_snapshot"

    id = db.Column(db.Integer, primary_key=True)
    period_type = db.Column(db.String(16), nullable=False, index=True)  # quarter | half | year
    period_key = db.Column(db.String(32), nullable=False, index=True)  # e.g. 2025-26-Q2 / H1 / FY
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="frozen")  # frozen
    payload_json = db.Column(db.JSON, nullable=False)
    computed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    frozen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    frozen_by_user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    frozen_by = db.relationship("User", foreign_keys=[frozen_by_user_id])
    client_events = db.relationship(
        "IaRegulatorySnapshotClientEvent",
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "period_type",
            "period_key",
            name="uq_ia_reg_snap_period",
        ),
    )


class IaRegulatorySnapshotClientEvent(db.Model):
    __tablename__ = "ia_regulatory_snapshot_client_event"

    id = db.Column(db.Integer, primary_key=True)
    snapshot_id = db.Column(
        db.Integer,
        db.ForeignKey("ia_regulatory_snapshot.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = db.Column(db.String(32), nullable=False, index=True)
    # entered | expired | unbilled_18m
    client_id = db.Column(
        db.Integer, db.ForeignKey("client.id", ondelete="SET NULL"), nullable=True, index=True
    )
    client_name = db.Column(db.String(255), nullable=False, default="")
    event_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.String(500), nullable=True)

    snapshot = db.relationship("IaRegulatorySnapshot", back_populates="client_events")
    client = db.relationship("Client", foreign_keys=[client_id])
