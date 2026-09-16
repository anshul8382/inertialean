#!/usr/bin/env python3
"""
Test SMTP credentials from .env (no secrets printed).

Usage (from repo root):
  python3 scripts/test_smtp.py
  python3 scripts/test_smtp.py --send sharveen@equities4wealth.com
"""
from __future__ import annotations

import argparse
import os
import smtplib
import sys
from email.mime.text import MIMEText

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv

    env_path = os.path.join(ROOT, ".env")
    if os.path.isfile(env_path):
        load_dotenv(env_path)
except ImportError:
    pass


def _normalize_password(raw: str) -> str:
    return str(raw or "").strip().strip('"\'').replace(" ", "").replace("\r", "").replace("\n", "")


def smtp_config():
    user = (os.environ.get("MAIL_USERNAME") or os.environ.get("SMTP_USERNAME") or "").strip()
    pwd = _normalize_password(os.environ.get("MAIL_PASSWORD") or os.environ.get("SMTP_PASSWORD") or "")
    server = (os.environ.get("MAIL_SERVER") or os.environ.get("SMTP_SERVER") or "smtp.gmail.com").strip()
    port = int(os.environ.get("MAIL_PORT") or os.environ.get("SMTP_PORT") or 587)
    use_tls = str(os.environ.get("MAIL_USE_TLS", "true")).lower() in ("true", "1", "on", "yes")
    use_ssl = str(os.environ.get("MAIL_USE_SSL", "false")).lower() in ("true", "1", "on", "yes")
    sender = (os.environ.get("MAIL_DEFAULT_SENDER") or user).strip()
    return {
        "user": user,
        "password": pwd,
        "server": server,
        "port": port,
        "use_tls": use_tls,
        "use_ssl": use_ssl,
        "sender": sender,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Test SMTP login from MAIL_* env vars")
    parser.add_argument("--send", metavar="EMAIL", help="Send a one-line test message to this address")
    args = parser.parse_args()

    cfg = smtp_config()
    print("SMTP configuration (secrets hidden):")
    print(f"  MAIL_SERVER={cfg['server']}")
    print(f"  MAIL_PORT={cfg['port']}")
    print(f"  MAIL_USE_TLS={cfg['use_tls']}")
    print(f"  MAIL_USE_SSL={cfg['use_ssl']}")
    print(f"  MAIL_USERNAME={cfg['user'] or '(not set)'}")
    print(f"  MAIL_PASSWORD={'set (' + str(len(cfg['password'])) + ' chars)' if cfg['password'] else '(not set)'}")
    print(f"  MAIL_DEFAULT_SENDER={cfg['sender'] or '(not set)'}")

    if not cfg["user"] or not cfg["password"]:
        print("\nFAIL: MAIL_USERNAME and MAIL_PASSWORD must be set in .env")
        print("See docs/EMAIL_SETUP.md")
        return 1

    if cfg["sender"] and cfg["user"] and cfg["sender"].lower() != cfg["user"].lower():
        print("\nWARN: MAIL_DEFAULT_SENDER differs from MAIL_USERNAME — Gmail may reject sends.")

    print("\nConnecting and logging in…")
    try:
        if cfg["use_ssl"]:
            smtp = smtplib.SMTP_SSL(cfg["server"], cfg["port"], timeout=25)
        else:
            smtp = smtplib.SMTP(cfg["server"], cfg["port"], timeout=25)
            if cfg["use_tls"]:
                smtp.starttls()
        smtp.login(cfg["user"], cfg["password"])
        print("OK: SMTP login succeeded.")

        if args.send:
            try:
                from services.email_source_tag import tag_body, tag_subject

                subject = tag_subject("INERTIA SMTP test")
                body = tag_body(
                    "INERTIA SMTP test — if you received this, mail is configured correctly."
                ) or "INERTIA SMTP test — if you received this, mail is configured correctly."
            except Exception:
                subject = "INERTIA SMTP test"
                body = "INERTIA SMTP test — if you received this, mail is configured correctly."
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = cfg["sender"] or cfg["user"]
            msg["To"] = args.send
            smtp.sendmail(cfg["user"], [args.send], msg.as_string())
            print(f"OK: Test message sent to {args.send} (subject={subject!r})")

        smtp.quit()
        return 0
    except smtplib.SMTPAuthenticationError as e:
        print(f"\nFAIL: Authentication rejected (535): {e}")
        print("Fix: use a Gmail/Google Workspace App Password — not your normal login password.")
        print("See docs/EMAIL_SETUP.md")
        return 2
    except Exception as e:
        print(f"\nFAIL: {type(e).__name__}: {e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
