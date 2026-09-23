"""SEBI-style advisory register — one row per advice communication."""
from __future__ import annotations

from datetime import datetime

from extensions import db


class AdvisoryRegisterEntry(db.Model):
    __tablename__ = "advisory_register_entry"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(
        db.Integer, db.ForeignKey("client.id", ondelete="SET NULL"), nullable=True, index=True
    )
    client_name_snapshot = db.Column(db.String(255), nullable=False, default="")
    advice_date = db.Column(db.Date, nullable=False, index=True)
    nature_of_advice = db.Column(db.Text, nullable=False, default="")
    products_securities = db.Column(db.Text, nullable=False, default="")
    fee_charged = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    products_hash = db.Column(db.String(64), nullable=True, index=True)

    source = db.Column(db.String(32), nullable=False, default="inertia_send", index=True)
    recommendation_session_id = db.Column(
        db.Integer,
        db.ForeignKey("recommendation_session.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    email_log_id = db.Column(
        db.Integer, db.ForeignKey("email_log.id", ondelete="SET NULL"), nullable=True
    )
    gmail_message_id = db.Column(db.String(255), nullable=True, unique=True)

    subject = db.Column(db.String(500), nullable=True)
    raw_excerpt = db.Column(db.Text, nullable=True)
    import_confidence = db.Column(db.String(16), nullable=False, default="high")

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by_user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    client = db.relationship("Client", foreign_keys=[client_id])
    session = db.relationship("RecommendationSession", foreign_keys=[recommendation_session_id])
    email_log = db.relationship("EmailLog", foreign_keys=[email_log_id])
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        db.Index(
            "ix_adv_reg_client_date_hash",
            "client_id",
            "advice_date",
            "products_hash",
        ),
    )
