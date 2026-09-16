#!/usr/bin/env python3
"""
Smoke-test navbar templates: every url_for endpoint used in base.html / base_clean.html
must be registered; templates must render under a logged-in user.

Run from repo root:  python scripts/smoke_nav.py
Exit 0 on success, 1 on failure.
"""
from __future__ import annotations

import os
import re
import sys

# Repo root (parent of scripts/)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.chdir(ROOT)


def extract_url_for_endpoints(html: str) -> set[str]:
    """Match url_for('endpoint' or url_for("endpoint" — skip static."""
    out = set()
    for m in re.finditer(r"""url_for\(\s*['\"]([a-zA-Z0-9_.]+)['\"]""", html):
        ep = m.group(1)
        if ep == "static":
            continue
        out.add(ep)
    return out


def dummy_kwargs_for_rule(rule) -> dict:
    kw = {}
    for arg in rule.arguments:
        if arg in ("filename", "subdomain"):
            continue
        if arg.endswith("_id") or arg in ("id", "page", "year", "month", "day"):
            kw[arg] = 1
        else:
            kw[arg] = "x"
    return kw


# Endpoints referenced only inside {% if config.get('NAV_*_ENABLED') %} in base.html;
# they are absent from app.view_functions when optional blueprints fail to import.
OPTIONAL_WHEN_MISSING = frozenset(
    {
        "tasks.list_tasks",
        "financial_planning.dashboard",
        "account_management.dashboard",
        "account_management.heads_list",
        "account_management.upload",
        "task_assignment_rules.list_rules",
    }
)


def main() -> int:
    from werkzeug.routing import BuildError

    from __init__ import create_app
    from flask import render_template
    from flask_login import login_user

    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False

    templates_to_check = [
        os.path.join(ROOT, "templates", "base.html"),
        os.path.join(ROOT, "templates", "base_clean.html"),
    ]

    all_eps: set[str] = set()
    for path in templates_to_check:
        with open(path, encoding="utf-8") as f:
            all_eps |= extract_url_for_endpoints(f.read())

    failures: list[str] = []
    with app.app_context():
        from flask import url_for

        for ep in sorted(all_eps):
            if ep not in app.view_functions:
                if ep in OPTIONAL_WHEN_MISSING:
                    continue
                failures.append(f"Missing endpoint (not registered): {ep}")
                continue
            rules = [r for r in app.url_map.iter_rules() if r.endpoint == ep]
            if not rules:
                failures.append(f"No URL rule for endpoint: {ep}")
                continue
            try:
                kw = dummy_kwargs_for_rule(rules[0])
                if ep == "auth.logout":
                    url_for(ep, _external=True)
                else:
                    url_for(ep, **kw)
            except BuildError as e:
                failures.append(f"url_for({ep!r}) BuildError: {e}")

        if failures:
            print("Navbar url_for smoke: FAIL")
            for line in failures:
                print(" ", line)
            return 1

        from models import User

        user = User.query.first()
        if not user:
            print("Navbar url_for smoke: OK (no User row — skip render test)")
            return 0

        for name in ("base.html", "base_clean.html"):
            with app.test_request_context("/"):
                login_user(user)
                try:
                    render_template(name, body="")
                except Exception as e:
                    print(f"Render {name}: FAIL {type(e).__name__}: {e}")
                    return 1
            print(f"Render {name}: OK")

    print("Navbar url_for smoke: OK (%d endpoints checked)" % len(all_eps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
