"""Regulator-friendly audit log (login, client access, sensitive actions)."""
from __future__ import annotations

from datetime import datetime

from extensions import db


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True)
    action = db.Column(db.String(64), nullable=False, index=True)
    resource_type = db.Column(db.String(64), nullable=True, index=True)
    resource_id = db.Column(db.String(64), nullable=True)
    client_id = db.Column(db.Integer, nullable=True, index=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    details_json = db.Column(db.Text, nullable=True)

    user = db.relationship("User", passive_deletes=True)
