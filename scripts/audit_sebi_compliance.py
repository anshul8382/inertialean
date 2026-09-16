#!/usr/bin/env python3
"""
Lightweight SEBI compliance scan for docs/SEBI_COMPLIANCE_CHECKLIST.md.
Run from repo root: python3 scripts/audit_sebi_compliance.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="ignore")


def _grep_files(pattern: str, glob: str = "**/*.py") -> list:
    hits = []
    rx = re.compile(pattern)
    for path in ROOT.glob(glob):
        if "_deprecated" in path.parts or ".venv" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if rx.search(text):
            hits.append(str(path.relative_to(ROOT)))
    return hits[:15]


def _add(findings, area, status, detail):
    findings.append((area, status, detail))


def main() -> int:
    findings = []
    config = _read("config.py")
    models_text = _read("models/__init__.py") + _read("models.py")
    ac_paths = ["scripts/utilities/access_control.py", "access_control.py"]
    ac = ""
    for p in ac_paths:
        ac += _read(p)

    _add(
        findings,
        "1 Auth - bcrypt",
        "PASS" if "bcrypt" in models_text else "FAIL",
        "User password hashing",
    )
    has_2fa = (ROOT / "routes/two_factor.py").exists()
    force_2fa = "FORCE_2FA_FOR_ALL_USERS" in config and "true" in config.lower().split("FORCE_2FA_FOR_ALL_USERS", 1)[-1][:20].lower() if "FORCE_2FA_FOR_ALL_USERS" in config else False
    if not force_2fa:
        force_2fa = "FORCE_2FA_FOR_ALL_USERS = os.environ.get('FORCE_2FA_FOR_ALL_USERS', 'true').lower() == 'true'" in config
    has_enforcement = (ROOT / "services/two_factor_enforcement.py").exists()
    if has_2fa and force_2fa and has_enforcement:
        tfa_status, tfa_detail = "PASS", "Mandatory 2FA enforced (two_factor_enforcement + FORCE_2FA_FOR_ALL_USERS)"
    elif has_2fa:
        tfa_status, tfa_detail = "PARTIAL", "routes/two_factor.py (policy not fully enforced)"
    else:
        tfa_status, tfa_detail = "FAIL", "missing"
    _add(findings, "1 Auth - 2FA", tfa_status, tfa_detail)
    _add(
        findings,
        "1 Auth - session cookies",
        "PASS" if "SESSION_COOKIE_HTTPONLY" in config else "FAIL",
        "config.py SESSION_COOKIE_*",
    )
    _add(
        findings,
        "3 Access - client_access",
        "PASS" if ("can_access_client" in ac or "client_access_required" in ac) else "FAIL",
        "access_control helpers",
    )
    audit_model = "class AuditLog" in models_text or (ROOT / "models/audit_log.py").exists() or (ROOT / "models/audit_log.py").exists()
    audit_svc = (ROOT / "services/audit_service.py").exists()
    audit_trail = (ROOT / "services/audit_trail_service.py").exists()
    if audit_model and audit_svc:
        audit_status, audit_detail = "PASS", "AuditLog + audit_service (+ file audit_trail_service)"
    elif audit_trail:
        audit_status, audit_detail = "PARTIAL", "file-based audit_trail_service only (no AuditLog model)"
    else:
        audit_status, audit_detail = "FAIL", "no AuditLog model or audit_service"
    _add(findings, "4 Audit - logging", audit_status, audit_detail)
    _add(
        findings,
        "5 API - versioned prefix",
        "PASS" if (ROOT / "api/v1").is_dir() else "FAIL",
        "api/v1/",
    )

    api_dir = ROOT / "api/v1"
    v1_guard = (ROOT / "api/v1/auth_guard.py").exists()
    v1_hook = "@api_v1.before_request" in _read("api/v1/__init__.py")
    v2_guard = (ROOT / "api/v2/auth_guard.py").exists()
    unauth = []
    if api_dir.is_dir() and not (v1_guard and v1_hook):
        for f in api_dir.glob("*.py"):
            if f.name.startswith("_") or f.name in ("auth_guard.py", "auth.py"):
                continue
            t = f.read_text(encoding="utf-8", errors="ignore")
            if "Blueprint" in t and "login_required" not in t and "require_" not in t:
                unauth.append(f.name)
    if v1_guard and v1_hook and v2_guard:
        api_auth_status, api_auth_detail = "PASS", "global auth_guard on api/v1 + api/v2 (JWT + session)"
    elif unauth:
        api_auth_status, api_auth_detail = "PARTIAL", "no auth decorator: " + ", ".join(unauth[:8])
    else:
        api_auth_status, api_auth_detail = "PASS", "blueprint decorators"
    _add(findings, "5 API - login on blueprints", api_auth_status, api_auth_detail)
    _add(
        findings,
        "11 Accessibility doc",
        "PASS" if (ROOT / "docs/ACCESSIBILITY_CHECKLIST.md").exists() else "FAIL",
        "docs/ACCESSIBILITY_CHECKLIST.md",
    )

    hardcoded = _grep_files(r"!Nert!a2025|password\s*=\s*['\"]Sharveen['\"]", "scripts/**/*.py")
    hardcoded = [p for p in hardcoded if "audit_sebi" not in p and "run_full_codebase_audit" not in p and "run_agent_approval" not in p]
    _add(
        findings,
        "Security - hardcoded passwords",
        "WARN" if hardcoded else "PASS",
        ", ".join(hardcoded) if hardcoded else "none in active scripts/",
    )

    print("SEBI compliance scan (automated)\n" + "=" * 60)
    for area, status, detail in findings:
        print("[%s] %s" % (status.ljust(7), area))
        print("          %s\n" % detail)

    fails = sum(1 for _, s, _ in findings if s == "FAIL")
    warns = sum(1 for _, s, _ in findings if s in ("WARN", "PARTIAL"))
    print("Summary: %d FAIL, %d PARTIAL/WARN, %d PASS" % (fails, warns, len(findings) - fails - warns))
    print("Full checklist: docs/SEBI_COMPLIANCE_CHECKLIST.md")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
