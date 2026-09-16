#!/usr/bin/env python3
"""
Run Lighthouse on Flask HTML pages (local dev server required).

Usage:
  FLASK_ENV=development python3 run.py   # in another terminal
  python3 scripts/run_lighthouse_audit.py
  python3 scripts/run_lighthouse_audit.py --max-pages 30
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SKIP_PATH_PARTS = (
    "/api/",
    "/airflow/api/",
    "/airflow/proxy",
    "/static/",
)
SKIP_PATH_KEYWORDS = (
    "download",
    "export",
    ".csv",
    ".json",
    ".xml",
    ".pdf",
)
# Endpoints that return JSON/redirect only — not useful for UI Lighthouse
SKIP_ENDPOINT_PREFIXES = (
    "api.",
    "billing_api.",
    "client_status_api.",
)


@dataclass
class PageResult:
    path: str
    url: str
    status: Optional[int] = None
    error: Optional[str] = None
    scores: Dict[str, int] = field(default_factory=dict)
    issues: List[Dict[str, Any]] = field(default_factory=list)


def _node_bin() -> str:
    local = ROOT / "apps/mobile/.node-local/bin/npx"
    if local.exists():
        return str(local)
    return "npx"


def _is_ui_route(rule) -> bool:
    if "GET" not in (rule.methods or set()):
        return False
    path = rule.rule
    if path.startswith("/static"):
        return False
    for part in SKIP_PATH_PARTS:
        if part in path:
            return False
    low = path.lower()
    for kw in SKIP_PATH_KEYWORDS:
        if kw in low:
            return False
    ep = rule.endpoint or ""
    for pref in SKIP_ENDPOINT_PREFIXES:
        if ep.startswith(pref):
            return False
  # Skip raw Flask/WTF utilities
    if ep.startswith(("static", "_")):
        return False
    return True


def _sample_view_args(app, rule) -> Optional[Dict[str, Any]]:
    """Best-effort IDs for <int:...> placeholders using the DB."""
    import re

    args: Dict[str, Any] = {}
    for name in rule.arguments:
        if not re.search(rf"<int:{re.escape(name)}>", rule.rule):
            return None
        val = None
        try:
            if "client" in name:
                from models import Client

                row = Client.query.order_by(Client.id).first()
                val = row.id if row else None
            elif "user" in name:
                from models import User

                row = User.query.order_by(User.id).first()
                val = row.id if row else None
            elif "agreement" in name:
                from models import Agreement

                row = Agreement.query.order_by(Agreement.id).first()
                val = row.id if row else None
            elif "alert" in name:
                from alert_system_models import Alert

                row = Alert.query.order_by(Alert.id).first()
                val = row.id if row else None
            elif "workflow" in name or "review" in name:
                from models import ReviewWorkflow

                row = ReviewWorkflow.query.order_by(ReviewWorkflow.id).first()
                val = row.id if row else None
            elif "task" in name:
                from models import OpsTask

                row = OpsTask.query.order_by(OpsTask.id).first()
                val = row.id if row else None
            elif "head" in name:
                from models import AccountHead

                row = AccountHead.query.order_by(AccountHead.id).first()
                val = row.id if row else None
            elif "statement" in name:
                from models import BankStatement

                row = BankStatement.query.order_by(BankStatement.id).first()
                val = row.id if row else None
            elif "security" in name:
                from models import Security

                row = Security.query.order_by(Security.id).first()
                val = row.id if row else None
            elif "lead" in name:
                from models import Lead

                row = Lead.query.order_by(Lead.id).first()
                val = row.id if row else None
            elif "invoice" in name:
                from models import Invoice

                row = Invoice.query.order_by(Invoice.id).first()
                val = row.id if row else None
            elif "document" in name:
                from models import Document

                row = Document.query.order_by(Document.id).first()
                val = row.id if row else None
            elif "role" in name:
                from models import Role

                row = Role.query.order_by(Role.id).first()
                val = row.id if row else None
            elif name.endswith("_id") or name == "id":
                val = 1
            else:
                return None
        except Exception:
            return None
        if val is None:
            return None
        args[name] = val
    return args


def _build_page_urls(app, max_pages: int) -> Tuple[List[str], List[str]]:
    """Return (public_paths, auth_paths)."""
    public: Set[str] = set()
    auth: Set[str] = set()
    public_eps = {"auth.login", "auth.forgot_password", "auth.reset_password"}

    with app.app_context():
        for rule in app.url_map.iter_rules():
            if not _is_ui_route(rule):
                continue
            path = rule.rule
            if "<" in path:
                view_args = _sample_view_args(app, rule)
                if view_args is None:
                    continue
                try:
                    path = app.url_map.bind("127.0.0.1").build(rule.endpoint, view_args)
                except Exception:
                    continue
            if rule.endpoint in public_eps or path.startswith("/auth/"):
                public.add(path)
            else:
                auth.add(path)

    public_sorted = sorted(public)
    auth_sorted = sorted(auth)
    # Prioritize high-traffic shells first
    priority = [
        "/",
        "/dashboard",
        "/clients-v2/",
        "/hub/",
        "/tasks/",
        "/agents/",
        "/auth/login",
    ]
    ordered_auth: List[str] = []
    for p in priority:
        if p in auth_sorted:
            ordered_auth.append(p)
            auth_sorted.remove(p)
    ordered_auth.extend(auth_sorted)
    cap = max(max_pages, 1)
    return public_sorted[:cap], ordered_auth[: max(0, cap - len(public_sorted))]


def _session_cookie_header(app) -> str:
    """Authenticated session via Flask-Login session (local audit; bypasses password/2FA)."""
    from models import User

    user = User.query.filter_by(is_active=True).order_by(User.is_admin.desc(), User.id).first()
    if not user:
        raise RuntimeError("No active user in DB for authenticated Lighthouse runs")

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
    client.get("/dashboard")
    cookie = client.get_cookie("inertia_session")
    if not cookie:
        raise RuntimeError("Could not obtain inertia_session cookie")
    return f"inertia_session={cookie}"


def _preflight(url: str, cookie: Optional[str]) -> Tuple[int, str]:
    import urllib.request

    req = urllib.request.Request(url)
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, ctype
    except Exception as exc:
        return 0, str(exc)


def _run_lighthouse(url: str, cookie: Optional[str], out_json: Path) -> None:
    headers = json.dumps({"Cookie": cookie}) if cookie else None
    cmd = [
        _node_bin(),
        "--yes",
        "lighthouse@12",
        url,
        "--only-categories=performance,accessibility,best-practices,seo",
        "--output=json",
        f"--output-path={out_json}",
        '--chrome-flags=--headless --no-sandbox --disable-gpu --disable-dev-shm-usage',
        "--quiet",
        "--max-wait-for-load=45000",
    ]
    if headers:
        cmd.extend(["--extra-headers", headers])
    subprocess.run(cmd, check=True, timeout=120, cwd=str(ROOT))


def _parse_lighthouse(path: Path) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
    data = json.loads(path.read_text())
    scores = {k: int(v["score"] * 100) for k, v in data["categories"].items() if v.get("score") is not None}
    issues: List[Dict[str, Any]] = []
    for audit in data.get("audits", {}).values():
        score = audit.get("score")
        mode = audit.get("scoreDisplayMode")
        if score is None or score >= 1:
            continue
        if mode not in ("binary", "numeric", "manual"):
            continue
        issues.append(
            {
                "id": audit.get("id"),
                "title": audit.get("title"),
                "score": score,
                "description": (audit.get("description") or "")[:240],
            }
        )
    issues.sort(key=lambda x: x["score"])
    return scores, issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Lighthouse audit for Flask UI pages")
    parser.add_argument("--base-url", default=os.environ.get("LIGHTHOUSE_BASE_URL", "http://127.0.0.1:5001"))
    parser.add_argument("--max-pages", type=int, default=40, help="Max pages to audit (public + auth)")
    parser.add_argument("--write-report", action="store_true", help="Write docs/LIGHTHOUSE_REPORT.md")
    args = parser.parse_args()

    from config import DevelopmentConfig
    from main import create_app

    app = create_app(DevelopmentConfig)
    with app.app_context():
        public_paths, auth_paths = _build_page_urls(app, args.max_pages)
        try:
            cookie = _session_cookie_header(app)
        except Exception as exc:
            print(f"WARN: authenticated runs skipped ({exc})")
            cookie = None
            auth_paths = []

    results: List[PageResult] = []
    tmpdir = Path(tempfile.mkdtemp(prefix="lh-audit-"))

    for path in public_paths:
        url = args.base_url.rstrip("/") + path
        status, meta = _preflight(url, None)
        pr = PageResult(path=path, url=url, status=status)
        if status != 200 or "text/html" not in meta:
            pr.error = f"skip: HTTP {status} ({meta})"
            results.append(pr)
            continue
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", path.strip("/") or "root")
        out = (tmpdir / slug).with_suffix(".json")
        try:
            _run_lighthouse(url, None, out)
            pr.scores, pr.issues = _parse_lighthouse(out)
        except Exception as exc:
            pr.error = str(exc)
        results.append(pr)
        print(f"[public] {path} perf={pr.scores.get('performance', '—')} a11y={pr.scores.get('accessibility', '—')}")

    if cookie:
        for path in auth_paths:
            url = args.base_url.rstrip("/") + path
            status, meta = _preflight(url, cookie)
            pr = PageResult(path=path, url=url, status=status)
            if status != 200 or "text/html" not in meta:
                pr.error = f"skip: HTTP {status} ({meta})"
                results.append(pr)
                continue
            slug = "auth_" + re.sub(r"[^a-zA-Z0-9]+", "_", path.strip("/") or "root")
            out = (tmpdir / slug).with_suffix(".json")
            try:
                _run_lighthouse(url, cookie, out)
                pr.scores, pr.issues = _parse_lighthouse(out)
            except Exception as exc:
                pr.error = str(exc)
            results.append(pr)
            print(
                f"[auth] {path} perf={pr.scores.get('performance', '—')} "
                f"a11y={pr.scores.get('accessibility', '—')}"
            )

    # Summary
    audited = [r for r in results if r.scores]
    skipped = [r for r in results if r.error]
    print("\n=== Lighthouse summary ===")
    print(f"Audited: {len(audited)}  Skipped: {len(skipped)}  Total queued: {len(results)}")

    if audited:
        avg = {}
        for cat in ("performance", "accessibility", "best-practices", "seo"):
            vals = [r.scores[cat] for r in audited if cat in r.scores]
            if vals:
                avg[cat] = round(sum(vals) / len(vals))
        print("Average scores:", avg)

    # Aggregate recurring issues
    issue_counts: Dict[str, Dict[str, Any]] = {}
    for r in audited:
        for issue in r.issues:
            iid = issue["id"]
            if iid not in issue_counts:
                issue_counts[iid] = {**issue, "pages": 0}
            issue_counts[iid]["pages"] += 1
    recurring = sorted(issue_counts.values(), key=lambda x: (-x["pages"], x["score"]))
    if recurring:
        print("\nRecurring issues (across audited pages):")
        for item in recurring[:25]:
            print(f"  [{item['score']:.2f}] {item['id']} ({item['pages']} pages): {item['title']}")

    worst = sorted(audited, key=lambda r: min(r.scores.values()) if r.scores else 100)[:10]
    if worst:
        print("\nLowest-scoring pages:")
        for r in worst:
            mins = min(r.scores.values()) if r.scores else 0
            print(f"  {r.path} -> {r.scores} (min {mins})")

    if skipped:
        print(f"\nSkipped ({len(skipped)}):")
        for r in skipped[:15]:
            print(f"  {r.path}: {r.error}")
        if len(skipped) > 15:
            print(f"  ... +{len(skipped) - 15} more")

    report_path = ROOT / "docs" / "LIGHTHOUSE_REPORT.md"
    if args.write_report:
        lines = [
            "# Lighthouse audit report",
            "",
            f"Base URL: `{args.base_url}`",
            f"Pages audited: {len(audited)} (skipped {len(skipped)})",
            "",
        ]
        if audited:
            lines.append("## Average scores")
            for cat, val in avg.items():
                lines.append(f"- **{cat}**: {val}")
            lines.append("")
        if recurring:
            lines.append("## Recurring issues")
            for item in recurring[:30]:
                lines.append(f"- `{item['id']}` ({item['pages']} pages, score {item['score']:.2f}): {item['title']}")
            lines.append("")
        lines.append("## Per-page results")
        for r in results:
            lines.append(f"### `{r.path}`")
            if r.error:
                lines.append(f"- Skipped: {r.error}")
            else:
                lines.append(f"- Scores: {r.scores}")
                if r.issues:
                    lines.append("- Issues:")
                    for i in r.issues[:8]:
                        lines.append(f"  - {i['id']}: {i['title']} ({i['score']})")
            lines.append("")
        report_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nWrote {report_path}")

    json_path = ROOT / "reports" / "lighthouse_latest.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            [
                {
                    "path": r.path,
                    "url": r.url,
                    "status": r.status,
                    "error": r.error,
                    "scores": r.scores,
                    "issues": r.issues,
                }
                for r in results
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
