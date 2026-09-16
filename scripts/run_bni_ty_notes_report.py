#!/usr/bin/env python3
"""
Weekly BNI TY Notes email (RECOS_GENERATED and/or recommendation sent_at since last success).
Invoked from Airflow; uses Flask app context and Flask-Mail.
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

_ENV_PATH = os.path.join(APP_DIR, ".env")
if os.path.isfile(_ENV_PATH):
    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_PATH)
    except ImportError:
        pass


def main() -> int:
    from main import create_app
    from services.bni_ty_notes_service import run_bni_ty_notes_email

    dry_run = "--dry-run" in sys.argv
    app = create_app()
    with app.app_context():
        out = run_bni_ty_notes_email(dry_run=dry_run)
        print(out)
        if out.get("error") == "no_recipients":
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
