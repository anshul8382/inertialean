"""Tag outbound email so Lean / new-VPS mail is distinguishable from BigRock prod.

Set in .env (new VPS only):
  EMAIL_SOURCE_TAG=Lean server

Empty / unset = no tagging (old prod behaviour).
"""
from __future__ import annotations

import os
from typing import Any, Optional


def get_email_source_tag() -> str:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            tag = (current_app.config.get("EMAIL_SOURCE_TAG") or "").strip()
            if tag:
                return tag
    except Exception:
        pass
    return (os.environ.get("EMAIL_SOURCE_TAG") or "").strip()


def tag_subject(subject: Optional[str]) -> str:
    tag = get_email_source_tag()
    subj = (subject or "").strip()
    if not tag:
        return subj
    prefix = f"[{tag}] "
    if subj.startswith(prefix) or subj.startswith(f"[{tag}]"):
        return subj
    return f"{prefix}{subj}" if subj else f"[{tag}]"


def tag_html(html: Optional[str]) -> Optional[str]:
    tag = get_email_source_tag()
    if not tag or html is None:
        return html
    marker = f"<!-- EMAIL_SOURCE_TAG:{tag} -->"
    if marker in html:
        return html
    banner = (
        f'{marker}<div style="background:#fff3cd;border:1px solid #ffc107;'
        f'padding:8px 12px;margin:0 0 16px;font-family:sans-serif;font-size:13px;">'
        f"<strong>[{tag}]</strong> This email was sent from the Lean / new VPS "
        f"(parallel environment — not BigRock production)."
        f"</div>"
    )
    lower = html.lower()
    body_idx = lower.find("<body")
    if body_idx >= 0:
        gt = html.find(">", body_idx)
        if gt >= 0:
            return html[: gt + 1] + banner + html[gt + 1 :]
    return banner + html


def tag_body(body: Optional[str]) -> Optional[str]:
    tag = get_email_source_tag()
    if not tag or body is None:
        return body
    line = f"[{tag}] Sent from Lean / new VPS (not BigRock production).\n\n"
    if body.startswith(f"[{tag}]"):
        return body
    return line + body


def apply_to_flask_message(msg: Any) -> Any:
    """Mutate a flask_mail.Message in place before send."""
    if not get_email_source_tag():
        return msg
    try:
        msg.subject = tag_subject(getattr(msg, "subject", None))
    except Exception:
        pass
    try:
        if getattr(msg, "html", None):
            msg.html = tag_html(msg.html)
        if getattr(msg, "body", None):
            msg.body = tag_body(msg.body)
    except Exception:
        pass
    return msg
