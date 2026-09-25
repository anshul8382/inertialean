"""Suitability reports uploaded to a client's Google Drive folder."""
from __future__ import annotations

from datetime import datetime

from extensions import db


class SuitabilityReport(db.Model):
    __tablename__ = "suitability_report"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(
        db.Integer, db.ForeignKey("client.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = db.Column(db.String(255), nullable=False, default="")
    drive_file_id = db.Column(db.String(128), nullable=True, index=True)
    web_view_link = db.Column(db.String(500), nullable=True)
    risk_profile_used = db.Column(db.String(64), nullable=True)
    asset_classes_json = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    client = db.relationship("Client", backref=db.backref("suitability_reports", lazy="dynamic"))
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
