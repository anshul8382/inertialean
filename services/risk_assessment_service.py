"""Persist risk assessment submissions and match leads."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import inspect

from extensions import db
from services.risk_profile_scoring_service import score_assessment

logger = logging.getLogger(__name__)

_TABLE_OK: Optional[bool] = None


def risk_assessment_tables_ready() -> bool:
    global _TABLE_OK
    if _TABLE_OK is not None:
        return _TABLE_OK
    try:
        _TABLE_OK = inspect(db.engine).has_table("risk_assessment_submission")
    except Exception:
        _TABLE_OK = False
    return bool(_TABLE_OK)


def reset_table_cache() -> None:
    global _TABLE_OK
    _TABLE_OK = None


def _signer():
    from flask import current_app

    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="risk-assessment-lead")


def lead_invite_token(lead_id: int) -> str:
    return _signer().dumps({"lead_id": int(lead_id)})


def parse_lead_invite_token(token: str) -> Optional[int]:
    if not token:
        return None
    try:
        data = _signer().loads(token)
        return int(data.get("lead_id"))
    except (BadSignature, TypeError, ValueError, AttributeError):
        return None


def _match_lead_id(email: str, explicit_lead_id: Optional[int]) -> Optional[int]:
    from models import Lead

    if not explicit_lead_id:
        return None
    lead = Lead.query.get(explicit_lead_id)
    if not lead:
        return None
    submitted_email = (email or "").strip().lower()
    expected_email = (lead.email or "").strip().lower()
    if not submitted_email or submitted_email != expected_email:
        return None
    return lead.id


def submit_assessment(
    form_data: Dict[str, Any],
    *,
    lead_token: Optional[str] = None,
    holdings: Optional[list] = None,
) -> Tuple[Dict[str, Any], Optional[Any]]:
    """
    Score answers, persist submission when table exists, optionally update lead status.
    Returns (score_dict, submission_or_None).
    """
    from models.lead_onboarding import RiskAssessmentSubmission

    answers = {
        "name": (form_data.get("name") or "").strip(),
        "email": (form_data.get("email") or "").strip(),
        "withdraw_begin": form_data.get("withdraw_begin") or "",
        "spend_down": form_data.get("spend_down") or "",
        "knowledge": form_data.get("knowledge") or "",
        "attitude": form_data.get("attitude") or "",
        "holdings": holdings if holdings is not None else form_data.get("holdings") or [],
        "crash": form_data.get("crash") or "",
        "chart": form_data.get("chart") or "",
        "investments_narrative": (form_data.get("investments_narrative") or "").strip(),
    }
    scores = score_assessment(answers)
    explicit_id = parse_lead_invite_token(lead_token) if lead_token else None
    lead_id = _match_lead_id(answers["email"], explicit_id)

    submission = None
    if risk_assessment_tables_ready():
        submission = RiskAssessmentSubmission(
            lead_id=lead_id,
            name=answers["name"][:120],
            email=answers["email"][:120],
            answers_json=answers,
            time_horizon_score=scores["time_horizon_score"],
            risk_tolerance_score=scores["risk_tolerance_score"],
            horizon_band=scores.get("horizon_band"),
            risk_profile=scores["risk_profile"],
            max_equity_pct=scores["max_equity_pct"],
            client_risk_profile=scores.get("client_risk_profile"),
        )
        db.session.add(submission)
        if lead_id:
            _mark_lead_risk_received(lead_id, scores)
        db.session.commit()
    else:
        logger.warning("risk_assessment_submission table missing; scored but not persisted")

    scores["lead_id"] = lead_id
    scores["name"] = answers["name"]
    scores["email"] = answers["email"]
    return scores, submission


def _mark_lead_risk_received(lead_id: int, scores: Dict[str, Any]) -> None:
    from models import Lead
    from workflow_service import WorkflowService

    lead = Lead.query.get(lead_id)
    if not lead:
        return
    early = {
        None,
        "",
        "new",
        "contacted",
        "qualified",
        "referral",
        "meeting",
        "risk_profile",
        "risk_profile_sent",
    }
    if (lead.status or "").lower() in early or lead.status == "risk_profile_sent":
        lead.status = "risk_profile_received"
        lead.updated_at = datetime.utcnow()
        try:
            wf = WorkflowService.get_workflow("lead", lead.id)
            if wf:
                WorkflowService.update_stage(wf.id, "RISK_PROFILE")
        except Exception:
            logger.debug("Workflow stage update skipped for lead %s", lead_id, exc_info=True)
    note = (
        f"[Risk assessment] Profile={scores.get('risk_profile')}; "
        f"TH={scores.get('time_horizon_score')}; RT={scores.get('risk_tolerance_score')}; "
        f"Max equity={scores.get('max_equity_pct')}%"
    )
    existing = (lead.notes or "").strip()
    lead.notes = f"{existing}\n{note}".strip() if existing else note


def latest_for_lead(lead_id: int):
    if not risk_assessment_tables_ready():
        return None
    from models.lead_onboarding import RiskAssessmentSubmission

    return (
        RiskAssessmentSubmission.query.filter_by(lead_id=lead_id)
        .order_by(RiskAssessmentSubmission.created_at.desc())
        .first()
    )


def mark_risk_sent(lead_id: int) -> None:
    from models import Lead
    from workflow_service import WorkflowService

    lead = Lead.query.get(lead_id)
    if not lead:
        return
    early = {
        None,
        "",
        "new",
        "contacted",
        "qualified",
        "referral",
        "meeting",
        "risk_profile",
    }
    if (lead.status or "").lower() in early:
        lead.status = "risk_profile_sent"
        lead.updated_at = datetime.utcnow()
        try:
            wf = WorkflowService.get_workflow("lead", lead.id)
            if wf:
                WorkflowService.update_stage(wf.id, "RISK_PROFILE")
        except Exception:
            pass
    db.session.commit()
