#!/usr/bin/env python3
"""
Run all agent-aligned checks until green (or report remaining PARTIAL items).

Preserves new features: does not fail on optional blueprints (parrva, campaign_studio, etc.)
or intentional public endpoints listed in api/v1/auth_guard.py.
Financial planning is excluded unless FINANCIAL_PLANNING_ENABLED=true.

Usage:
  python3 scripts/run_agent_approval_loop.py
  python3 scripts/run_agent_approval_loop.py --write-status
  python3 scripts/run_agent_approval_loop.py --pre-commit   # git pre-commit (no report files)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], cwd: Path = ROOT) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-status", action="store_true", help="Write docs/AGENT_APPROVAL_STATUS.md")
    parser.add_argument(
        "--pre-commit",
        action="store_true",
        help="Read-only checks for git pre-commit (no --write-report on audit scripts)",
    )
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    write_reports = not args.pre_commit

    results: list[tuple[str, str, str]] = []  # agent, status, detail

    # Technology + Guideline — app factory
    code, out = _run([sys.executable, "-c", "from main import create_app; create_app(); print('ok')"])
    reg_ok = (ROOT / "agents/registry.py").exists()
    eng_detail = "create_app + agents/registry.py" if code == 0 and reg_ok else "see logs"
    results.append(("Engineering", "PASS" if code == 0 and reg_ok else "FAIL", eng_detail))

    # Guideline Manager — codebase audit
    code, out = _run([sys.executable, "scripts/run_full_codebase_audit.py"])
    results.append(("Guideline Manager", "PASS" if code == 0 else "FAIL", out.strip().split("\n")[0] if out else "audit"))

    # DB cutover registry (prod schema lag policy)
    reg = ROOT / "docs/DB_CUTOVER_REGISTRY.md"
    cutover_svc = ROOT / "services/db_cutover.py"
    if reg.is_file() and cutover_svc.is_file():
        sys.path.insert(0, str(ROOT))
        try:
            from services.db_cutover import deferred_features, registry as cutover_registry

            defer = sorted(deferred_features())
            n = len(cutover_registry())
            dc_status = "PASS" if defer else "PARTIAL"
            dc_detail = (
                f"registry: {n} feature(s); DEFER_DB_FEATURES={','.join(defer) or '(none)'}"
                if defer
                else f"registry: {n} feature(s); no deferrals (cutover may be done)"
            )
        except Exception as exc:
            dc_status = "PARTIAL"
            dc_detail = f"registry read error: {exc}"
    else:
        dc_status = "FAIL"
        dc_detail = "missing docs/DB_CUTOVER_REGISTRY.md or services/db_cutover.py"
    results.append(("DB cutover", dc_status, dc_detail))

    # SEBI regulatory unit
    code, out = _run([sys.executable, "scripts/audit_sebi_compliance.py"])
    if "[FAIL" in out:
        sebi_status = "FAIL"
    elif "[PARTIAL" in out or "[WARN" in out:
        sebi_status = "PARTIAL"
    else:
        sebi_status = "PASS"
    results.append(("SEBI regulatory", sebi_status, "see audit_sebi_compliance.py output"))

    # Security (VAPT) — static scan
    sec_cmd = [sys.executable, "scripts/run_security_audit.py", "--fail-on", "critical"]
    if write_reports:
        sec_cmd.append("--write-report")
    code, out = _run(sec_cmd)
    if code == 2:
        sec_status = "FAIL"
    elif "high     0" in out and "medium   0" in out:
        sec_status = "PASS"
    else:
        sec_status = "PARTIAL"
    sec_detail = "VAPT: see docs/SECURITY_VAPT_REPORT.md"
    for line in out.splitlines():
        if line.strip().startswith("total"):
            sec_detail = f"VAPT ({line.strip()}) → docs/SECURITY_VAPT_REPORT.md"
            break
    results.append(("Security (VAPT)", sec_status, sec_detail))

    # Security — dependency vulnerabilities (pip-audit + npm audit)
    dep_cmd = [sys.executable, "scripts/run_dependency_audit.py", "--fail-on", "critical"]
    if write_reports:
        dep_cmd.append("--write-report")
    code, out = _run(dep_cmd)
    py_count = 0
    npm_count = 0
    for line in out.splitlines():
        if line.startswith("| Python (`requirements.txt`)"):
            parts = [p.strip() for p in line.strip().strip("|").split("|")]
            if len(parts) >= 3 and parts[-1].isdigit():
                py_count = int(parts[-1])
        elif line.startswith("| Capacitor shell"):
            parts = [p.strip() for p in line.strip().strip("|").split("|")]
            if len(parts) >= 3 and parts[-1].isdigit():
                npm_count = int(parts[-1])
    if code == 2:
        dep_status = "FAIL"
    elif py_count == 0 and npm_count == 0:
        dep_status = "PASS"
    else:
        dep_status = "PARTIAL"
    dep_detail = f"deps: {py_count} Python, {npm_count} Node → docs/DEPENDENCY_AUDIT.md"
    results.append(("Security (deps)", dep_status, dep_detail))

    # Security — OWASP ZAP baseline (DAST, opt-in via ZAP_TARGET_URL)
    zap_cmd = [sys.executable, "scripts/run_zap_baseline.py"]
    if write_reports:
        zap_cmd.append("--write-report")
    code, out = _run(zap_cmd)
    if code == 0:
        zap_status = "PASS"
    elif code == 2:
        zap_status = "FAIL"
    elif code == 3:
        zap_status = "SKIP"
    else:
        zap_status = "PARTIAL"
    zap_detail = "ZAP: see docs/ZAP_BASELINE_REPORT.md"
    if zap_status == "SKIP":
        zap_detail = "set ZAP_TARGET_URL=http://<staging> to enable"
    results.append(("Security (ZAP)", zap_status, zap_detail))

    # Mobile Optimiser — Capacitor shell + global mobile UI stack
    code, out = _run([sys.executable, "scripts/run_mobile_ui_check.py"])
    if code == 0:
        mobile_status = "PASS"
    elif code == 2:
        mobile_status = "PARTIAL"
    else:
        mobile_status = "FAIL"
    mobile_detail = "run_mobile_ui_check.py"
    for line in out.splitlines():
        if line.startswith("Detail:"):
            mobile_detail = line.replace("Detail:", "").strip()
            break
    results.append(("Mobile Optimiser", mobile_status, mobile_detail))

    # Delivery quality — tests
    if not args.skip_tests:
        try:
            import pytest  # noqa: F401
        except ImportError:
            results.append(("Delivery quality", "PARTIAL", "pytest not installed; pip install pytest"))
        else:
            code, out = _run([sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"], cwd=ROOT)
            results.append(("Delivery quality", "PASS" if code == 0 else "FAIL", f"pytest exit {code}"))

            # Functional tests (explicit) — some teams expect these to be called out separately.
            f_code, f_out = _run(
                [sys.executable, "-m", "pytest", "tests/functional", "-q", "--tb=no"],
                cwd=ROOT,
            )
            if f_code == 0:
                f_status = "PASS"
            else:
                f_status = "FAIL"
            # Include a tiny hint if nothing was collected.
            f_detail = f"pytest functional exit {f_code}"
            if "collected 0 items" in (f_out or ""):
                f_detail = "no functional tests collected"
                f_status = "FAIL"
            results.append(("Delivery quality (functional)", f_status, f_detail))

            # Functional orchestrator — module-level "test cards" + targeted pytest.
            o_code, o_out = _run(
                [sys.executable, "scripts/functional_test_orchestrator.py", "--changed", "--run"],
                cwd=ROOT,
            )
            if o_code == 0:
                o_status = "PASS"
            else:
                o_status = "FAIL"
            if "No impacted modules detected" in (o_out or ""):
                o_detail = "no impacted modules"
            else:
                o_detail = f"exit {o_code}"
            results.append(("Delivery quality (functional packs)", o_status, o_detail))
    else:
        results.append(("Delivery quality", "SKIP", "—skip-tests"))

    # Docs
    docs = [
        "docs/MOBILE_STRATEGY.md",
        "docs/MOBILE_UI_AUDIT.md",
        "docs/MOBILE_UI_DEVELOPER_CHECKLIST.md",
        "docs/CODEBASE_STANDARDIZATION_PLAN.md",
        "docs/ACCESSIBILITY_CHECKLIST.md",
        "docs/ARCHITECTURE_MODULES_AND_ALIGNMENT.md",
        "docs/ARCHITECTURE_LIVE.md",
        "docs/DB_CUTOVER_REGISTRY.md",
        "docs/DEPLOY_WORKFLOW.md",
    ]
    missing = [d for d in docs if not (ROOT / d).exists()]
    results.append(("Delivery quality (docs)", "PASS" if not missing else "PARTIAL", "missing: " + ", ".join(missing) if missing else "core docs present"))

    # Live architecture map — must exist; agents keep content current via Engineering gate
    live_arch = ROOT / "docs/ARCHITECTURE_LIVE.md"
    if live_arch.is_file():
        text = live_arch.read_text(encoding="utf-8", errors="replace")
        has_keep = "Agent keep-updated" in text or "Keep this doc current" in text
        has_exclusions = "not live" in text.lower() or "Explicitly not live" in text
        la_status = "PASS" if has_keep and has_exclusions else "PARTIAL"
        la_detail = "docs/ARCHITECTURE_LIVE.md (live surfaces map)"
        if not has_keep:
            la_detail += "; missing keep-updated section"
        if not has_exclusions:
            la_detail += "; missing exclusions"
    else:
        la_status = "FAIL"
        la_detail = "missing docs/ARCHITECTURE_LIVE.md — create from Engineering gate"
    results.append(("Live architecture map", la_status, la_detail))

    fails = [r for r in results if r[1] == "FAIL"]
    partials = [r for r in results if r[1] in ("PARTIAL", "SKIP")]

    print("\n=== Agent approval loop ===\n")
    for agent, status, detail in results:
        print(f"  [{status:7}] {agent}: {detail}")
    print(f"\nSummary: {len(fails)} FAIL, {len(partials)} PARTIAL/SKIP, {len(results) - len(fails) - len(partials)} PASS")

    if args.write_status:
        lines = [
            "# Agent approval status",
            "",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            "",
            "| Agent | Status | Detail |",
            "|-------|--------|--------|",
        ]
        for agent, status, detail in results:
            lines.append(f"| {agent} | {status} | {detail[:120]} |")
        lines.append("")
        lines.append("Re-run: `python3 scripts/run_agent_approval_loop.py --write-status`")
        (ROOT / "docs/AGENT_APPROVAL_STATUS.md").write_text("\n".join(lines))
        print("Wrote docs/AGENT_APPROVAL_STATUS.md")

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
