"""Public risk assessment questionnaire (no login)."""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user

from services.risk_profile_scoring_service import QUESTION_OPTIONS

logger = logging.getLogger(__name__)

risk_assessment_bp = Blueprint("risk_assessment", __name__)

# Simple in-memory rate limit: IP → timestamps
_RATE: dict = defaultdict(list)
_RATE_MAX = 8
_RATE_WINDOW = 3600


def _rate_ok(ip: str) -> bool:
    now = time.time()
    bucket = _RATE[ip]
    _RATE[ip] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(_RATE[ip]) >= _RATE_MAX:
        return False
    _RATE[ip].append(now)
    return True


@risk_assessment_bp.route("/risk-assessment", methods=["GET", "POST"])
def risk_assessment_form():
    from services.risk_assessment_service import parse_lead_invite_token, submit_assessment

    lead_token = request.args.get("t") or request.form.get("lead_token") or ""
    prefill_name = ""
    prefill_email = ""
    lead_id = parse_lead_invite_token(lead_token) if lead_token else None
    if lead_id:
        try:
            from models import Lead

            lead = Lead.query.get(lead_id)
            if lead:
                prefill_name = lead.name or ""
                prefill_email = lead.email or ""
        except Exception:
            logger.debug("Prefill lead skipped", exc_info=True)

    if request.method == "POST":
        # Honeypot
        if (request.form.get("company_website") or "").strip():
            flash("Submission received.", "success")
            return redirect(url_for("risk_assessment.risk_assessment_thanks"))

        ip = request.remote_addr or "unknown"
        if not _rate_ok(ip):
            flash("Too many submissions from this network. Please try again later.", "error")
            return redirect(url_for("risk_assessment.risk_assessment_form", t=lead_token or None))

        holdings = request.form.getlist("holdings")
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        required = [
            "withdraw_begin",
            "spend_down",
            "knowledge",
            "attitude",
            "crash",
            "chart",
        ]
        missing = [f for f in required if not (request.form.get(f) or "").strip()]
        if not name or not email or missing or not holdings:
            flash("Please complete all required fields.", "error")
            return render_template(
                "risk_assessment/form.html",
                options=QUESTION_OPTIONS,
                lead_token=lead_token,
                prefill_name=name or prefill_name,
                prefill_email=email or prefill_email,
                form=request.form,
            )

        try:
            scores, submission = submit_assessment(
                request.form,
                lead_token=lead_token,
                holdings=holdings,
            )
            if submission is None:
                flash("Assessment could not be stored right now. Please try again later.", "error")
                return render_template(
                    "risk_assessment/form.html",
                    options=QUESTION_OPTIONS,
                    lead_token=lead_token,
                    prefill_name=name or prefill_name,
                    prefill_email=email or prefill_email,
                    form=request.form,
                )
            session["risk_assessment_result"] = scores
            return redirect(url_for("risk_assessment.risk_assessment_thanks"))
        except ValueError:
            flash("One or more answers were invalid. Please review the form and try again.", "error")
        except Exception:
            logger.exception("Risk assessment submit failed")
            flash("Something went wrong saving your assessment. Please try again.", "error")

    return render_template(
        "risk_assessment/form.html",
        options=QUESTION_OPTIONS,
        lead_token=lead_token,
        prefill_name=prefill_name,
        prefill_email=prefill_email,
        form=None,
        is_staff=bool(getattr(current_user, "is_authenticated", False)),
    )


@risk_assessment_bp.route("/risk-assessment/thanks")
def risk_assessment_thanks():
    result = session.pop("risk_assessment_result", None)
    return render_template("risk_assessment/thanks.html", result=result)
