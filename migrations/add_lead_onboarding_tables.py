#!/usr/bin/env python3
"""Create lead onboarding tables. Run: python migrations/add_lead_onboarding_tables.py

Listed in docs/DB_CUTOVER_REGISTRY.md. Gate: DEFER_DB_FEATURES includes lead_onboarding.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def main():
    app = create_app()
    with app.app_context():
        from models.lead_onboarding import LeadProposal, RiskAssessmentSubmission

        RiskAssessmentSubmission.__table__.create(db.engine, checkfirst=True)
        LeadProposal.__table__.create(db.engine, checkfirst=True)
        print("risk_assessment_submission + lead_proposal tables ready")


if __name__ == "__main__":
    main()
