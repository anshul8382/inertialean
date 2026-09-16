#!/usr/bin/env python3
"""
Mobile UI / Capacitor parity check for the agent approval loop.

Verifies the global mobile stack exists and tests pass. Optional template audit.

Usage:
  python3 scripts/run_mobile_ui_check.py
  python3 scripts/run_mobile_ui_check.py --strict   # fail if audit flags high-severity templates
  python3 scripts/run_mobile_ui_check.py --write-report
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "static/css/mobile-layout.css",
    "static/css/mobile-ui.css",
    "static/js/mobile-layout.js",
    "static/js/mobile-config.js",
    "templates/base.html",
    "apps/capacitor-shell/package.json",
    "docs/MOBILE_STRATEGY.md",
    "docs/MOBILE_UI_AUDIT.md",
    "static/css/inertia-mobile-app.css",
    "static/js/inertia-mobile-app.js",
    "docs/MOBILE_REDESIGN_PLAN.md",
    "scripts/audit_mobile_templates.py",
]

BASE_HTML_MARKERS = [
    "mobile-layout.css",
    "mobile-ui.css",
    "inertia-mobile-app.css",
    "mobile-config.js",
    "mobile-layout.js",
    "inertia-mobile-app.js",
]


def _run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _python_with_pytest() -> str:
    """
    The approval loop invokes this script via `python3`, which may be a system
    interpreter without project deps (including pytest). Prefer a venv python
    when it has pytest available.
    """
    candidates = [
        sys.executable,
        str(ROOT / "venv" / "bin" / "python"),
        str(ROOT / ".venv" / "bin" / "python"),
    ]
    for py in candidates:
        try:
            r = subprocess.run([py, "-c", "import pytest"], cwd=str(ROOT), capture_output=True, text=True)
        except Exception:
            continue
        if r.returncode == 0:
            return py
    return sys.executable


def check_required_files() -> list[str]:
    missing = [p for p in REQUIRED_FILES if not (ROOT / p).exists()]
    return missing


def check_base_html() -> list[str]:
    base = ROOT / "templates" / "base.html"
    if not base.exists():
        return ["templates/base.html missing"]
    text = base.read_text(encoding="utf-8")
    return [m for m in BASE_HTML_MARKERS if m not in text]


def check_capacitor_env_example() -> list[str]:
    issues = []
    env_example = ROOT / "apps/capacitor-shell/.env.example"
    if not env_example.exists():
        issues.append("apps/capacitor-shell/.env.example missing")
    else:
        text = env_example.read_text(encoding="utf-8")
        if "CAP_SERVER_URL" not in text:
            issues.append(".env.example missing CAP_SERVER_URL")
    return issues


def run_mobile_tests() -> tuple[bool, str]:
    py = _python_with_pytest()
    code, out = _run(
        [
            py,
            "-m",
            "pytest",
            "tests/test_mobile_ui_audit.py",
            "-q",
            "--tb=short",
            "-k",
            "not test_run_mobile_ui_check_script",
        ]
    )
    return code == 0, out.strip()


def run_template_audit(strict: bool) -> tuple[str, str, int]:
    """Return status PASS/PARTIAL/FAIL, detail, high_count."""
    code, out = _run([sys.executable, "scripts/audit_mobile_templates.py"])
    high_count = 0
    standalone = 0
    for line in out.splitlines():
        if "  high:" in line:
            try:
                high_count = int(line.split("high:")[1].strip())
            except ValueError:
                pass
        if "[high]" in line and "campaign_studio" in line:
            standalone += 1

    if code != 0:
        return "FAIL", "audit_mobile_templates.py failed", high_count

    if strict and high_count > standalone:
        return "FAIL", f"{high_count} high-severity template(s) outside known standalone UIs", high_count

    if high_count > 0:
        return "PARTIAL", f"template audit: {high_count} high (standalone UIs expected)", high_count

    return "PASS", "template audit clean", high_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Mobile UI / Capacitor parity check")
    parser.add_argument("--strict", action="store_true", help="Fail on unexpected high-severity templates")
    parser.add_argument("--write-report", action="store_true", help="Write reports/mobile_ui_check.json")
    args = parser.parse_args()

    findings: list[str] = []
    status = "PASS"

    missing = check_required_files()
    if missing:
        findings.extend(f"missing file: {m}" for m in missing)
        status = "FAIL"

    base_issues = check_base_html()
    if base_issues:
        findings.extend(f"base.html: {m}" for m in base_issues)
        status = "FAIL"

    cap_issues = check_capacitor_env_example()
    if cap_issues:
        findings.extend(cap_issues)
        if status == "PASS":
            status = "PARTIAL"

    tests_ok, test_out = run_mobile_tests()
    if not tests_ok:
        findings.append("mobile UI tests failed")
        status = "FAIL"

    audit_status, audit_detail, _ = run_template_audit(args.strict)
    if audit_status == "FAIL":
        status = "FAIL"
        findings.append(audit_detail)
    elif audit_status == "PARTIAL" and status == "PASS":
        status = "PARTIAL"
        findings.append(audit_detail)

    jwt_ok = (ROOT / "services/jwt_service.py").exists()
    expo_ok = (ROOT / "apps/mobile/App.tsx").exists()
    if not jwt_ok:
        findings.append("services/jwt_service.py missing (Expo path)")
        if status == "PASS":
            status = "PARTIAL"

    detail_parts = [
        "Capacitor shell + mobile-ui stack",
        f"Expo scaffold: {'yes' if expo_ok else 'no'}",
        f"JWT: {'yes' if jwt_ok else 'no'}",
        audit_detail,
    ]
    detail = "; ".join(detail_parts)

    report = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "detail": detail,
        "findings": findings,
        "required_files_ok": not missing,
        "tests_ok": tests_ok,
        "audit_status": audit_status,
    }

    print("=== Mobile UI check ===")
    print(f"Status: {status}")
    print(f"Detail: {detail}")
    if findings:
        print("Findings:")
        for f in findings:
            print(f"  - {f}")
    if not tests_ok:
        print("\nTest output:")
        print(test_out[-2000:])

    if args.write_report:
        out_path = ROOT / "reports" / "mobile_ui_check.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {out_path}")

    return 0 if status == "PASS" else (1 if status == "FAIL" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
