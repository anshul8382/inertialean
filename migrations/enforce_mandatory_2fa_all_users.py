#!/usr/bin/env python3
"""
Mark mandatory 2FA for every active user. Run once after deploy:
  python migrations/enforce_mandatory_2fa_all_users.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from services.two_factor_policy_service import TwoFactorPolicyService


def main():
    app = create_app()
    with app.app_context():
        result = TwoFactorPolicyService.enable_forced_2fa_for_all_users()
        if result.get("success"):
            r = result.get("results", {})
            print(f"OK: two_factor_required=True for {r.get('updated_users', 0)} active users")
            report = TwoFactorPolicyService.get_2fa_compliance_report()
            if report.get("success"):
                rep = report["report"]
                print(
                    f"Compliance: {rep['users_with_2fa']}/{rep['total_users']} "
                    f"({rep['compliance_percentage']:.1f}% with 2FA enabled)"
                )
        else:
            print("FAILED:", result.get("error"))
            sys.exit(1)


if __name__ == "__main__":
    main()
