#!/usr/bin/env python3
"""Admin: reset a user's 2FA so they can set up again. Run from app root with venv active.

Examples:
  python scripts/reset_user_2fa.py prathmesh
  python scripts/reset_user_2fa.py prathamesh@equities4wealth.com
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from main import create_app
from config import DevelopmentConfig
from extensions import db
from models import User
from services.two_factor_service import TwoFactorService


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/reset_user_2fa.py <username_or_email>")
        return 1

    needle = sys.argv[1].strip()
    app = create_app(DevelopmentConfig)
    with app.app_context():
        user = User.query.filter(
            (User.username == needle) | (User.email == needle)
        ).first()
        if not user:
            user = User.query.filter(
                (User.username.ilike(f"%{needle}%")) | (User.email.ilike(f"%{needle}%"))
            ).first()
        if not user:
            print(f"No user matching {needle!r}")
            return 1

        result = TwoFactorService.admin_reset_2fa(user)
        if not result.get("success"):
            print(result.get("error", "reset failed"))
            return 1
        db.session.commit()
        print(
            f"OK: reset 2FA for id={user.id} username={user.username} email={user.email}\n"
            "Tell the user to:\n"
            "  1. Delete ALL old 'Inertia Investment' entries for this email in their auth app\n"
            "  2. Log in again and scan the new QR once (do not refresh the page)\n"
            "  3. Enter the 6-digit code within 30 seconds"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
