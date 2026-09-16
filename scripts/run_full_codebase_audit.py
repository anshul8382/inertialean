#!/usr/bin/env python3
"""
Full codebase audit — aligns with Cursor expert agents (development gate).

Usage:
  python3 scripts/run_full_codebase_audit.py
  python3 scripts/run_full_codebase_audit.py --json reports/audit_latest.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _grep_count(pattern: str, glob: str = "**/*.py") -> int:
    try:
        r = subprocess.run(
            ["rg", "-l", pattern, str(ROOT), "-g", glob, "--glob", "!_deprecated/**",
             "--glob", "!.venv/**", "--glob", "!venv/**",
             "--glob", "!tests/**",
             "--glob", "!agents/security_audit_agent.py",
             "--glob", "!scripts/audit_sebi_compliance.py",
             "--glob", "!scripts/run_full_codebase_audit.py"],
            capture_output=True,
            text=True,
        )
        return len([x for x in r.stdout.strip().split("\n") if x])
    except FileNotFoundError:
        return -1


def _file_exists(rel: str) -> bool:
    return (ROOT / rel).is_file()


def _dir_size_mb(rel: str) -> float:
    p = ROOT / rel
    if not p.exists():
        return 0.0
    total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return round(total / (1024 * 1024), 1)


def run_audit() -> dict:
    findings = []

    # --- Technology / security ---
    hardcoded_pw = _grep_count(r"!Nert!a2025|password.*=.*['\"]Sharveen")
    if hardcoded_pw > 0:
        findings.append({"agent": "SEBI", "severity": "high", "item": f"Hardcoded credentials in {hardcoded_pw} Python files"})

    if not _file_exists("services/jwt_service.py"):
        findings.append({"agent": "Mobile", "severity": "high", "item": "Missing services/jwt_service.py"})
    if not _file_exists("api/v1/auth.py"):
        findings.append({"agent": "Mobile", "severity": "high", "item": "Missing api/v1/auth.py"})

    # --- Docs (guideline / BA / architect) ---
    required_docs = [
        "docs/MOBILE_STRATEGY.md",
        "docs/CODEBASE_STANDARDIZATION_PLAN.md",
        "docs/ARCHITECTURE_MODULES_AND_ALIGNMENT.md",
        "docs/ACCESSIBILITY_CHECKLIST.md",
        "docs/DEVELOP_WORKFLOW.md",
        "agents/registry.py",
    ]
    for doc in required_docs:
        if not _file_exists(doc):
            findings.append({"agent": "Guideline", "severity": "medium", "item": f"Missing {doc}"})

    # --- QA ---
    test_files = list((ROOT / "tests").rglob("test_*.py")) if (ROOT / "tests").exists() else []
    if len(test_files) < 8:
        findings.append({"agent": "QA", "severity": "medium", "item": f"Only {len(test_files)} test modules under tests/"})

    # --- Structure ---
    if (ROOT / "routes" / "main.py").exists():
        lines = len((ROOT / "routes" / "main.py").read_text(errors="ignore").splitlines())
        if lines > 3000:
            findings.append({"agent": "Technology", "severity": "medium", "item": f"routes/main.py is {lines} lines — extract to services"})

    deprecated_routes = [
        "routes/v2_transactions_ui.py",
        "routes/audit_routes.py",
        "routes/routes.py",
        "backend/api/app.py",
    ]
    for rel in deprecated_routes:
        if _file_exists(rel):
            findings.append({"agent": "Technology", "severity": "low", "item": f"Deprecated file still present: {rel}"})

    # --- Mobile ---
    if not (ROOT / "apps" / "mobile").is_dir():
        findings.append({"agent": "Mobile", "severity": "medium", "item": "apps/mobile scaffold missing"})

    # --- Agents registry ---
    if _file_exists("agents/registry.py"):
        reg = (ROOT / "agents/registry.py").read_text()
        for name in ("data_integrity_manager", "rec_exec_monitor", "task_assignment_agent"):
            if name not in reg:
                findings.append({"agent": "Architect", "severity": "high", "item": f"agents/registry.py missing {name}"})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(ROOT),
        "sizes_mb": {
            "services": _dir_size_mb("services"),
            "routes": _dir_size_mb("routes"),
            "agents": _dir_size_mb("agents"),
        },
        "test_file_count": len(test_files),
        "findings": findings,
        "finding_count": len(findings),
        "status": "pass" if not any(f["severity"] == "high" for f in findings) else "action_required",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="Write JSON report to path")
    args = parser.parse_args()
    report = run_audit()
    print(f"Audit status: {report['status']} ({report['finding_count']} findings)")
    for f in report["findings"]:
        print(f"  [{f['severity']}] ({f['agent']}) {f['item']}")
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(f"Wrote {out}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
