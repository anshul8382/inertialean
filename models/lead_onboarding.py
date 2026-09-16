"""Lead onboarding models: risk assessment submissions + proposals."""
from __future__ import annotations

from datetime import datetime

from extensions import db


class RiskAssessmentSubmission(db.Model):
    __tablename__ = "risk_assessment_submission"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(
        db.Integer, db.ForeignKey("lead.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name = db.Column(db.String(120), nullable=False, default="")
    email = db.Column(db.String(120), nullable=False, default="", index=True)
    answers_json = db.Column(db.JSON, nullable=False)
    time_horizon_score = db.Column(db.Integer, nullable=False, default=0)
    risk_tolerance_score = db.Column(db.Integer, nullable=False, default=0)
    horizon_band = db.Column(db.String(16), nullable=True)
    risk_profile = db.Column(db.String(64), nullable=False, default="")
    max_equity_pct = db.Column(db.Integer, nullable=False, default=50)
    client_risk_profile = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    lead = db.relationship(
        "Lead",
        backref=db.backref("risk_assessments", lazy="dynamic"),
        passive_deletes=True,
    )


class LeadProposal(db.Model):
    __tablename__ = "lead_proposal"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(
        db.Integer, db.ForeignKey("lead.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = db.Column(db.String(200), nullable=False, default="Investment Proposal")
    details_json = db.Column(db.JSON, nullable=True)
    writeup_text = db.Column(db.Text, nullable=True)
    docx_path = db.Column(db.String(512), nullable=True)
    status = db.Column(db.String(32), nullable=False, default="draft")  # draft|generated|pending_approval|approved|sent
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    sent_at = db.Column(db.DateTime, nullable=True)

    lead = db.relationship(
        "Lead",
        backref=db.backref("proposals", lazy="dynamic"),
        passive_deletes=True,
    )


class LeadKycProfile(db.Model):
    """KYC / CKYC capture for a lead (manual until CERSAI API is wired)."""

    __tablename__ = "lead_kyc_profile"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(
        db.Integer,
        db.ForeignKey("lead.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    pan = db.Column(db.String(10), nullable=True)
    aadhaar_last4 = db.Column(db.String(4), nullable=True)
    ckyc_number = db.Column(db.String(32), nullable=True)
    full_name_as_per_pan = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(32), nullable=False, default="pending")
    provider = db.Column(db.String(32), nullable=True, default="manual")
    provider_ref = db.Column(db.String(64), nullable=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    lead = db.relationship(
        "Lead",
        backref=db.backref("kyc_profile", uselist=False, cascade="all, delete-orphan"),
        passive_deletes=True,
    )
