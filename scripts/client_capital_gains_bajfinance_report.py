#!/usr/bin/env python3
"""
Client Capital Gains + Bajaj Finance + Top Gainers report.

This script is kept for cron / manual runs.
The report logic lives in services/client_capital_gains_bajfinance_report_service.py
so it can be reused from the web app (Investment Intelligence).
"""

from __future__ import annotations

import os
import sys
import smtplib
from datetime import date, datetime
from typing import Optional, Tuple


# Ensure we can import app modules when run from scripts/
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)


BAJFINANCE_CUTOFF_DATE = date(2025, 3, 15)


def _fmt_inr(x: float) -> str:
    try:
        return f"₹{float(x):,.2f}"
    except Exception:
        return "₹0.00"


def _safe_float(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def _get_mail_creds(app_config: Optional[dict] = None) -> Tuple[str, str, str, int, bool]:
    """
    Returns (username, password, server, port, use_tls).
    Prefers MAIL_* env vars; falls back to SMTP_*.
    """
    app_config = app_config or {}

    user = (
        (os.getenv("MAIL_USERNAME") or os.getenv("SMTP_USERNAME") or "").strip()
        or str(app_config.get("MAIL_USERNAME") or "").strip()
    )
    raw_pwd = os.getenv("MAIL_PASSWORD") or os.getenv("SMTP_PASSWORD") or app_config.get("MAIL_PASSWORD") or ""
    pwd = str(raw_pwd).strip().strip('"\'').replace(" ", "").replace("\r", "").replace("\n", "")
    server = (
        os.getenv("MAIL_SERVER")
        or os.getenv("SMTP_SERVER")
        or app_config.get("MAIL_SERVER")
        or "smtp.gmail.com"
    ).strip()
    port = int(os.getenv("MAIL_PORT") or os.getenv("SMTP_PORT") or app_config.get("MAIL_PORT") or "587")
    use_tls_raw = os.getenv("MAIL_USE_TLS")
    if use_tls_raw is None:
        use_tls_raw = app_config.get("MAIL_USE_TLS")
    use_tls = str(use_tls_raw if use_tls_raw is not None else "true").strip().lower() in ("true", "1", "on", "yes")
    return user, pwd, server, port, use_tls


def _send_email_with_attachment(
    *,
    subject: str,
    html_body: str,
    to_email: str,
    attachment_path: str,
    app_config: Optional[dict] = None,
) -> bool:
    """
    Send using the same approach as unified recommendations:
    - Build a Flask-Mail Message (UTF-8)
    - Send via direct SMTP using MAIL_* / SMTP_* (avoids Flask-Mail auth issues)
    """
    from flask_mail import Message

    app_config = app_config or {}

    # Build message (sender should match authenticated username for Gmail)
    sender_email = (
        (os.getenv("MAIL_USERNAME") or os.getenv("SMTP_USERNAME") or "").strip()
        or str(app_config.get("MAIL_USERNAME") or "").strip()
    )
    if not sender_email:
        sender_email = "anshul@equities4wealth.com"

    recipients = [str(to_email).strip()]
    if not recipients[0]:
        return False

    msg = Message(
        subject=subject,
        recipients=recipients,
        html=(html_body or ""),
        body="",
        sender=sender_email,
        charset="utf-8",
    )

    # Attach CSV
    try:
        with open(attachment_path, "rb") as f:
            data = f.read()
        filename = os.path.basename(attachment_path)
        msg.attach(filename=filename, content_type="text/csv", data=data)
    except Exception:
        return False

    # Direct SMTP send (same env normalization as services/email_service.py)
    _env = os.environ
    _user = (
        (_env.get("MAIL_USERNAME") or _env.get("SMTP_USERNAME") or "").strip()
        or str(app_config.get("MAIL_USERNAME") or "").strip()
        or "anshul@equities4wealth.com"
    )
    _raw = _env.get("MAIL_PASSWORD") or _env.get("SMTP_PASSWORD") or app_config.get("MAIL_PASSWORD") or ""
    _pwd = str(_raw).strip().strip('"\'').replace(" ", "").replace("\r", "").replace("\n", "")
    mail_username = _user
    mail_password = _pwd

    if not mail_username or not mail_password:
        # Mirror unified recommendation behavior: fail when creds missing.
        return False

    smtp_server = (_env.get("MAIL_SERVER") or _env.get("SMTP_SERVER") or str(app_config.get("MAIL_SERVER") or "") or "smtp.gmail.com").strip()
    smtp_port = int(_env.get("MAIL_PORT") or _env.get("SMTP_PORT") or app_config.get("MAIL_PORT") or 587)
    use_ssl = str(_env.get("MAIL_USE_SSL") or app_config.get("MAIL_USE_SSL") or "false").lower() in ("true", "1", "on")
    use_tls = str(_env.get("MAIL_USE_TLS") or app_config.get("MAIL_USE_TLS") or "true").lower() in ("true", "1", "on")

    smtp = None
    try:
        if use_ssl:
            smtp = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=25)
        else:
            smtp = smtplib.SMTP(smtp_server, smtp_port, timeout=25)
            if use_tls:
                smtp.starttls()
        smtp.login(mail_username, mail_password)
        msg_str = msg.as_string()
        msg_bytes = msg_str.encode("utf-8") if isinstance(msg_str, str) else msg_str
        smtp.sendmail(mail_username, msg.recipients, msg_bytes)
        return True
    except Exception:
        return False
    finally:
        try:
            if smtp is not None:
                smtp.quit()
        except Exception:
            pass


def main() -> int:
    from main import create_app

    app = create_app()
    with app.app_context():
        from flask import current_app

        from services.client_capital_gains_bajfinance_report_service import generate_csv_report

        csv_path, meta = generate_csv_report(out_dir=os.path.join(APP_ROOT, "tmp"))
        fy_start = meta["fy_start"]
        fy_end = meta["fy_end"]
        ltcg_limit = float(meta["ltcg_exemption_limit"] or 0.0)
        subject = (
            "Client Report: Realised STCG/LTCG + Unutilised LTCG Limit + "
            f"Bajaj Finance (as of {BAJFINANCE_CUTOFF_DATE.isoformat()})"
        )
        html = f"""
        <div style="font-family: Arial, sans-serif; line-height: 1.5">
          <p>Hi Anshul,</p>
          <p>Please find attached the client report with:</p>
          <ul>
            <li>Realised STCG/LTCG for FY {fy_start.strftime('%d %b %Y')} – {fy_end.strftime('%d %b %Y')} (from Capital Gains report)</li>
            <li>Unutilised LTCG exemption limit (FY setting: {_fmt_inr(ltcg_limit)})</li>
            <li>Bajaj Finance position as of {BAJFINANCE_CUTOFF_DATE.strftime('%d %b %Y')} (qty, avg cost, investment value, and current value = qty * current_price * 2)</li>
          </ul>
          <p>If you want “unutilised limit” to mean something else (e.g., a client-specific STCG/LTCG budget), reply with the rule and I’ll adjust.</p>
          <p>Thanks</p>
        </div>
        """.strip()

        sent = _send_email_with_attachment(
            subject=subject,
            html_body=html,
            to_email="anshul@equities4wealth.com",
            attachment_path=csv_path,
            app_config=dict(current_app.config),
        )

        if not sent:
            print(
                "⚠️ Report generated but email was not sent because MAIL_USERNAME/MAIL_PASSWORD "
                "(or SMTP_USERNAME/SMTP_PASSWORD) are not configured."
            )
            print(f"Report saved at: {csv_path}")

        # Always return success for generation; email may be disabled in some environments.
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

