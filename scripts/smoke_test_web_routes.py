#!/usr/bin/env python3
"""
Smoke-test GET HTML routes as a logged-in admin (skips API/static/binary downloads).

Usage (from repo root):
  python3 scripts/smoke_test_web_routes.py
  python3 scripts/smoke_test_web_routes.py --limit 50
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from werkzeug.routing import Rule

# Repo root on path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))


def _sample_ids() -> Dict[str, str]:
    from extensions import db
    from models import Client, User

    samples: Dict[str, str] = {}
    admin = User.query.filter_by(is_active=True, is_admin=True).first()
    if admin:
        samples["user_id"] = str(admin.id)
    client = Client.query.filter_by(is_active=True).first()
    if client:
        samples["client_id"] = str(client.id)
    # Common FK fallbacks
    for name, model_path in (
        ("alert_id", "models:Alert"),
        ("agreement_id", "models:Agreement"),
        ("template_id", "models:AgreementTemplate"),
        ("lead_id", "models:Lead"),
        ("head_id", "models:CreditDebitHead"),
        ("statement_id", "models:BankStatement"),
        ("role_id", "models:Role"),
        ("attendance_id", "models:Attendance"),
        ("document_id", "models:Document"),
        ("config_id", "models:SlaConfiguration"),
        ("agent_name", "data_integrity_manager"),
    ):
        if name in samples:
            continue
        try:
            if name == "agent_name":
                samples[name] = "data_integrity_manager"
                continue
            mod_name, cls_name = model_path.split(":")
            if mod_name == "models":
                import models as m

                cls = getattr(m, cls_name, None)
            else:
                cls = None
            if cls is not None:
                row = db.session.query(cls).first()
                if row is not None:
                    samples[name] = str(getattr(row, "id", row))
        except Exception:
            pass
    return samples


def _build_path(app, rule: Rule, samples: Dict[str, Any]) -> Optional[str]:
    from flask import url_for
    from werkzeug.routing import BuildError

    kwargs: Dict[str, Any] = {}
    for arg in rule.arguments:
        if arg in samples:
            val = samples[arg]
            kwargs[arg] = int(val) if str(val).isdigit() else val
        elif arg == "path":
            kwargs[arg] = "test"
        else:
            return None
    try:
        with app.test_request_context():
            return url_for(rule.endpoint, **kwargs)
    except BuildError:
        return None


def _is_html_route(rule: Rule) -> bool:
    if "GET" not in rule.methods:
        return False
    r = rule.rule
    if r.startswith("/static") or r.startswith("/api/"):
        return False
    skip_endpoints = {
        "auth.login",
        "auth.logout",
        "auth.forgot_password",
    }
    if rule.endpoint in skip_endpoints:
        return False
    return True


def _error_signatures(body: str) -> List[str]:
    hits = []
    for pat in (
        "UndefinedError",
        "TemplateSyntaxError",
        "Internal Server Error",
        "Traceback (most recent call last)",
        "sqlalchemy.exc.",
        "jinja2.exceptions",
    ):
        if pat in body:
            hits.append(pat)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Max routes to test (0=all)")
    parser.add_argument("--user-id", type=int, default=0, help="Login as user id (default: first admin)")
    args = parser.parse_args()

    from main import create_app
    from flask_login import login_user
    from models import User

    app = create_app()
    app.config["TESTING"] = True

    failures: List[Tuple[str, str, int, str]] = []
    skipped: List[str] = []
    ok = 0

    with app.app_context():
        from extensions import db

        uid = args.user_id
        if not uid:
            u = User.query.filter_by(is_active=True, is_admin=True).first()
            uid = u.id if u else 1
        user = User.query.get(uid)
        if not user:
            print(f"No user id={uid}")
            return 1

        samples = _sample_ids()
        rules = [r for r in app.url_map.iter_rules() if _is_html_route(r)]
        rules.sort(key=lambda r: r.rule)

        if args.limit:
            rules = rules[: args.limit]

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["_user_id"] = str(user.id)
                sess["_fresh"] = True
                sess["2fa_verified"] = True

            for rule in rules:
                path = _build_path(app, rule, samples)
                if path is None:
                    skipped.append(f"{rule.rule} ({rule.endpoint}) — missing sample id")
                    continue

                try:
                    resp = client.get(path, follow_redirects=True)
                except Exception as exc:
                    failures.append((path, rule.endpoint, -1, str(exc)[:200]))
                    continue

                ctype = (resp.content_type or "").lower()
                if any(
                    x in ctype
                    for x in (
                        "application/pdf",
                        "spreadsheet",
                        "octet-stream",
                        "image/",
                        "text/csv",
                    )
                ):
                    ok += 1
                    continue

                try:
                    body = resp.get_data(as_text=True) or ""
                except UnicodeDecodeError:
                    ok += 1
                    continue

                sigs = _error_signatures(body)
                if resp.status_code >= 500 or sigs:
                    detail = sigs[0] if sigs else f"HTTP {resp.status_code}"
                    failures.append((path, rule.endpoint, resp.status_code, detail))
                elif resp.status_code == 404:
                    skipped.append(f"{path} ({rule.endpoint}) — 404 (missing fixture row)")
                elif resp.status_code >= 400:
                    failures.append((path, rule.endpoint, resp.status_code, "client error"))
                else:
                    ok += 1

    print(f"OK: {ok}  Failed: {len(failures)}  Skipped (no id): {len(skipped)}")
    if failures:
        print("\n--- Failures ---")
        for path, ep, code, msg in failures[:40]:
            print(f"  [{code}] {path}  ({ep})  — {msg}")
        if len(failures) > 40:
            print(f"  ... and {len(failures) - 40} more")
    if skipped and len(skipped) <= 15:
        print("\n--- Skipped ---")
        for s in skipped:
            print(f"  {s}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
