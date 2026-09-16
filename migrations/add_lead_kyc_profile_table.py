#!/usr/bin/env python3
"""Create lead_kyc_profile table. Run: python migrations/add_lead_kyc_profile_table.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def main():
    app = create_app()
    with app.app_context():
        from models.lead_onboarding import LeadKycProfile

        LeadKycProfile.__table__.create(db.engine, checkfirst=True)
        print("lead_kyc_profile table ready")


if __name__ == "__main__":
    main()
