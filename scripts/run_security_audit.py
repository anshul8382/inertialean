#!/usr/bin/env python3
"""
Run the Security Audit Agent (VAPT) against the codebase.

Examples
--------
  python3 scripts/run_security_audit.py
  python3 scripts/run_security_audit.py --write-report
  python3 scripts/run_security_audit.py --severity high
  python3 scripts/run_security_audit.py --theme INTERNAL
  python3 scripts/run_security_audit.py --json reports/security_latest.json --md docs/SECURITY_VAPT_REPORT.md
  python3 scripts/run_security_audit.py --fail-on critical    # exit 1 if any critical findings

Exit codes
----------
  0  no findings at or above --fail-on (default: no enforcement)
  2  findings at or above --fail-on
  1  internal error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make repo importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.security_audit_agent import (  # noqa: E402
    SecurityAuditAgent,
    SEVERITY_ORDER,
    THREAT_THEMES,
)


def _color(s: str, sev: str) -> str:
    if not sys.stdout.isatty():
        return s
    code = {
        "critical": "\033[1;31m",
        "high": "\033[31m",
        "medium": "\033[33m",
        "low": "\033[36m",
        "info": "\033[37m",
    }.get(sev, "")
    return f"{code}{s}\033[0m"


def main() -> int:
    p = argparse.ArgumentParser(description="Security audit (VAPT) for the Inertia codebase.")
    p.add_argument("--root", default=str(ROOT), help="Repo root (defaults to repo root)")
    p.add_argument("--severity", choices=list(SEVERITY_ORDER.keys()), default="info",
                   help="Filter findings at this severity or higher")
    p.add_argument("--theme", choices=list(THREAT_THEMES.keys()),
                   help="Show only findings for this threat theme")
    p.add_argument("--json", dest="json_path", help="Write JSON report to this path")
    p.add_argument("--md", dest="md_path", help="Write Markdown report to this path")
    p.add_argument("--write-report",
                   action="store_true",
                   help="Shortcut: write docs/SECURITY_VAPT_REPORT.md + reports/security_latest.json")
    p.add_argument("--ignore-file", default=".security_audit_ignore",
                   help="File listing check_ids to suppress, one per line")
    p.add_argument("--fail-on", choices=list(SEVERITY_ORDER.keys()),
                   help="Exit 2 if any finding is at this severity or higher")
    args = p.parse_args()

    root = Path(args.root).resolve()
    ignore_ids = set()
    ignore_path = root / args.ignore_file
    if ignore_path.exists():
        for line in ignore_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ignore_ids.add(line)

    agent = SecurityAuditAgent(root, ignore_ids=ignore_ids)
    findings = agent.run_audit()

    # Apply filters
    sev_floor = SEVERITY_ORDER[args.severity]
    findings = [f for f in findings if SEVERITY_ORDER[f.severity] <= sev_floor]
    if args.theme:
        findings = [f for f in findings if f.theme == args.theme]

    # Console summary
    print(f"Security Audit Agent v{agent.agent_version}  •  root: {root}")
    summary = agent.summary(findings)
    print("\nSummary:")
    for sev in ("critical", "high", "medium", "low", "info"):
        print(f"  {_color(sev.ljust(8), sev)} {summary.get(sev, 0)}")
    print(f"  total    {summary['total']}\n")

    if findings:
        print("Findings (sorted by severity):")
        for f in findings:
            loc = f.file or "-"
            if f.line:
                loc = f"{loc}:{f.line}"
            print(f"  [{_color(f.severity.upper().ljust(8), f.severity)}] "
                  f"{f.check_id}  {f.title}  ({f.theme})  {loc}")
        print()
        # If nothing was filtered, hint at full report
        if args.severity == "info" and not args.theme:
            print("Tip: --write-report writes the full Markdown report with fixes.")
    else:
        print("No findings at the selected severity / theme. Clean.")

    if args.write_report:
        args.md_path = args.md_path or "docs/SECURITY_VAPT_REPORT.md"
        args.json_path = args.json_path or "reports/security_latest.json"

    if args.md_path:
        out = root / args.md_path
        agent.write_markdown(findings, out)
        print(f"Wrote Markdown report: {out.relative_to(root)}")

    if args.json_path:
        out = root / args.json_path
        agent.write_json(findings, out)
        print(f"Wrote JSON report:     {out.relative_to(root)}")

    if args.fail_on:
        threshold = SEVERITY_ORDER[args.fail_on]
        offenders = [f for f in findings if SEVERITY_ORDER[f.severity] <= threshold]
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
        print(f"audit failed: {exc}", file=sys.stderr)
        sys.exit(1)
