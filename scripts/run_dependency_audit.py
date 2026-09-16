#!/usr/bin/env python3
"""
Dependency vulnerability audit (Python + Node).

Runs:
  - `pip-audit` against `requirements.txt`
  - `npm audit --omit=dev` inside `apps/capacitor-shell/`

Writes a Markdown report to `docs/DEPENDENCY_AUDIT.md` and a JSON snapshot
under `reports/dependency_audit_latest.json`.

Exit codes
----------
  0  no vulnerabilities at or above --fail-on threshold
  2  vulnerabilities present at or above --fail-on threshold
  1  internal error

Examples
--------
  python3 scripts/run_dependency_audit.py
  python3 scripts/run_dependency_audit.py --write-report
  python3 scripts/run_dependency_audit.py --fail-on high
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
CAPACITOR_DIR = ROOT / "apps" / "capacitor-shell"
REPORT_MD = ROOT / "docs" / "DEPENDENCY_AUDIT.md"
REPORT_JSON = ROOT / "reports" / "dependency_audit_latest.json"

SEVERITY_ORDER = {"critical": 0, "high": 1, "moderate": 2, "medium": 2, "low": 3, "info": 4, "unknown": 5}


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _pip_audit() -> dict:
    """Run pip-audit; return structured dict with findings and meta."""
    if not REQUIREMENTS.exists():
        return {"available": False, "reason": "requirements.txt not found", "findings": []}

    if shutil.which("pip-audit") is None:
        try:
            import pip_audit  # noqa: F401
        except ImportError:
            return {
                "available": False,
                "reason": "pip-audit not installed (run: python3 -m pip install pip-audit)",
                "findings": [],
            }
        cmd = [sys.executable, "-m", "pip_audit"]
    else:
        cmd = ["pip-audit"]

    cmd += ["-r", str(REQUIREMENTS), "--format", "json", "--progress-spinner", "off"]
    code, stdout, stderr = _run(cmd)
    if not stdout.strip():
        return {"available": True, "exit_code": code, "stderr": stderr, "findings": []}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {"available": True, "exit_code": code, "stderr": stderr, "raw": stdout, "findings": []}

    findings = []
    deps = data.get("dependencies", data) if isinstance(data, dict) else []
    if isinstance(deps, dict):
        deps = deps.get("dependencies", [])
    for entry in deps:
        name = entry.get("name") or entry.get("package")
        version = entry.get("version")
        for vuln in entry.get("vulns", []) or []:
            findings.append(
                {
                    "package": name,
                    "version": version,
                    "id": vuln.get("id"),
                    "fix_versions": vuln.get("fix_versions") or [],
                    "description": (vuln.get("description") or "").strip().split("\n")[0][:240],
                    "severity": (vuln.get("severity") or "unknown").lower(),
                }
            )
    return {"available": True, "exit_code": code, "findings": findings}


def _npm_audit() -> dict:
    """Run npm audit (production deps only) in apps/capacitor-shell/."""
    if not (CAPACITOR_DIR / "package.json").exists():
        return {"available": False, "reason": "apps/capacitor-shell/package.json missing", "findings": []}

    npm_path = shutil.which("npm")
    if not npm_path:
        local_npm = ROOT / "apps" / "mobile" / ".node-local" / "bin" / "npm"
        if local_npm.exists():
            npm_path = str(local_npm)
    if not npm_path:
        return {"available": False, "reason": "npm not installed", "findings": []}

    env = os.environ.copy()
    env["PATH"] = f"{Path(npm_path).parent}{os.pathsep}{env.get('PATH', '')}"

    code, stdout, stderr = _run([npm_path, "audit", "--omit=dev", "--json"], cwd=CAPACITOR_DIR, env=env)
    if not stdout.strip():
        return {"available": True, "exit_code": code, "stderr": stderr, "findings": []}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {"available": True, "exit_code": code, "stderr": stderr, "raw": stdout[:400], "findings": []}

    findings = []
    for name, vuln in (data.get("vulnerabilities") or {}).items():
        findings.append(
            {
                "package": name,
                "severity": (vuln.get("severity") or "unknown").lower(),
                "via": [v if isinstance(v, str) else v.get("title", v.get("name")) for v in vuln.get("via", [])],
                "range": vuln.get("range"),
                "fix_available": bool(vuln.get("fixAvailable")),
            }
        )
    summary = (data.get("metadata") or {}).get("vulnerabilities") or {}
    return {"available": True, "exit_code": code, "findings": findings, "summary": summary}


def _severity_counts(findings: list[dict]) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "moderate": 0, "low": 0, "info": 0, "unknown": 0}
    for f in findings:
        sev = f.get("severity") or "unknown"
        if sev == "medium":
            sev = "moderate"
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def _render_markdown(pip_result: dict, npm_result: dict) -> str:
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    lines = [
        "# Dependency vulnerability audit",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Python runtime: {py_ver}",
        "Scanners: `pip-audit` (Python) + `npm audit --omit=dev` (Capacitor shell)",
        "",
        "## Summary",
        "",
        "| Source | Available | Findings |",
        "|--------|-----------|----------|",
    ]
    pip_total = len(pip_result.get("findings", []))
    npm_total = len(npm_result.get("findings", []))
    lines.append(f"| Python (`requirements.txt`) | {'yes' if pip_result.get('available') else 'no'} | {pip_total} |")
    lines.append(f"| Capacitor shell (`apps/capacitor-shell/`) | {'yes' if npm_result.get('available') else 'no'} | {npm_total} |")
    lines.append("")

    # Python details
    lines += ["## Python (`pip-audit`)", ""]
    if not pip_result.get("available"):
        lines.append(f"_Skipped: {pip_result.get('reason', 'unknown')}_")
    elif not pip_result["findings"]:
        lines.append("No known vulnerabilities.")
    else:
        counts = _severity_counts(pip_result["findings"])
        lines.append(f"**Severity counts:** " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
        lines.append("")
        lines += ["| Package | Version | Advisory | Severity | Fix versions | Note |",
                  "|---------|---------|----------|----------|--------------|------|"]
        for f in sorted(pip_result["findings"], key=lambda x: (SEVERITY_ORDER.get(x.get("severity", "unknown"), 9), x.get("package", ""))):
            fixes = ", ".join(f.get("fix_versions", [])) or "—"
            note = (f.get("description") or "").replace("|", " ")
            lines.append(f"| {f.get('package')} | {f.get('version')} | {f.get('id')} | {f.get('severity', 'unknown')} | {fixes} | {note} |")
    lines.append("")

    # Node details
    lines += ["## Capacitor shell (`npm audit`)", ""]
    if not npm_result.get("available"):
        lines.append(f"_Skipped: {npm_result.get('reason', 'unknown')}_")
    elif not npm_result["findings"]:
        lines.append("No known vulnerabilities in production dependencies.")
    else:
        lines += ["| Package | Severity | Range | Fix available | Via |",
                  "|---------|----------|-------|---------------|-----|"]
        for f in sorted(npm_result["findings"], key=lambda x: (SEVERITY_ORDER.get(x.get("severity", "unknown"), 9), x.get("package", ""))):
            via = ", ".join(str(v) for v in (f.get("via") or [])) or "—"
            fix = "yes" if f.get("fix_available") else "no"
            lines.append(f"| {f.get('package')} | {f.get('severity', 'unknown')} | {f.get('range', '—')} | {fix} | {via[:80]} |")
    lines.append("")

    lines += [
        "---",
        "",
        "**Triage:** upgrade critical/high vulnerabilities in a focused PR. Run the app's full pytest suite before deploy.",
        "",
        "## Python version note",
        "",
        f"This audit ran on Python **{py_ver}**. Several upstream advisories ship the fix only in",
        "package versions that require **Python 3.10+** (Pillow 12.x, urllib3 2.7+, requests 2.33+,",
        "python-dotenv 1.2.2+, weasyprint 68+). `requirements.txt` uses conditional pins so a Python",
        "3.10+ environment (recommended for production) installs the clean versions automatically.",
        "Local dev on Python 3.9 will show these as residual findings.",
        "",
        "Re-run:",
        "```bash",
        "python3 scripts/run_dependency_audit.py --write-report",
        "```",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Python + Node dependency vulnerability audit")
    parser.add_argument("--write-report", action="store_true", help="Write docs/DEPENDENCY_AUDIT.md + reports/dependency_audit_latest.json")
    parser.add_argument("--fail-on", choices=["critical", "high", "moderate", "low"], help="Exit 2 if any finding at this severity or higher")
    args = parser.parse_args()

    pip_result = _pip_audit()
    npm_result = _npm_audit()

    md = _render_markdown(pip_result, npm_result)
    print(md)

    if args.write_report:
        REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
        REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        REPORT_MD.write_text(md)
        REPORT_JSON.write_text(json.dumps({"python": pip_result, "node": npm_result}, indent=2, default=str))
        print(f"Wrote {REPORT_MD.relative_to(ROOT)}")
        print(f"Wrote {REPORT_JSON.relative_to(ROOT)}")

    if args.fail_on:
        threshold = SEVERITY_ORDER[args.fail_on]
        all_findings = pip_result.get("findings", []) + npm_result.get("findings", [])
        offenders = [f for f in all_findings if SEVERITY_ORDER.get((f.get("severity") or "unknown").replace("medium", "moderate"), 9) <= threshold]
        if offenders:
            print(f"\n{len(offenders)} finding(s) at severity >= {args.fail_on}. Failing.")
            return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as exc:  # pragma: no cover
        print(f"dependency audit failed: {exc}", file=sys.stderr)
        sys.exit(1)
