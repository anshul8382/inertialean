#!/usr/bin/env python3
"""
OWASP ZAP baseline scan (opt-in DAST step).

Runs the official `zaproxy/zap-stable` Docker image's `zap-baseline.py` against
a staging URL and writes the HTML + JSON reports under `reports/zap/`.

Usage
-----
  ZAP_TARGET_URL=https://staging.example.com python3 scripts/run_zap_baseline.py
  python3 scripts/run_zap_baseline.py --target http://192.168.1.10:5001
  python3 scripts/run_zap_baseline.py --target http://host --include-active

Exit codes
----------
  0  scan ran; no WARN/FAIL above threshold
  2  ZAP reported findings above --fail-on threshold
  3  ZAP could not be invoked (Docker missing, target unreachable, opt-in skip)
  1  internal error

The scan is **opt-in**: if neither `--target` nor `ZAP_TARGET_URL` is set, the
script exits 3 with a clear "skipped" message — safe to include in the agent
approval loop without breaking local dev.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "zap"
REPORT_MD = ROOT / "docs" / "ZAP_BASELINE_REPORT.md"

ZAP_IMAGE = "zaproxy/zap-stable"


def _run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _write_skip_report(reason: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(
        f"# OWASP ZAP baseline report\n\nGenerated: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"_Skipped: {reason}_\n\nTo enable: deploy `fresh-app-2026-05-26` to a staging URL, then run:\n\n"
        f"```bash\nZAP_TARGET_URL=http://<staging-host>:<port> python3 scripts/run_zap_baseline.py\n```\n"
    )


def _write_summary_report(target: str, html_path: Path, json_path: Path, exit_code: int) -> None:
    REPORT_MD.write_text(
        f"# OWASP ZAP baseline report\n\n"
        f"Generated: {datetime.now(timezone.utc).isoformat()}\n"
        f"Target: `{target}`\n"
        f"ZAP image: `{ZAP_IMAGE}`\n"
        f"ZAP exit code: `{exit_code}` (0=clean, 1=warn, 2=fail, 3=internal)\n\n"
        f"Reports written:\n"
        f"- HTML: `{html_path.relative_to(ROOT)}`\n"
        f"- JSON: `{json_path.relative_to(ROOT)}`\n\n"
        f"Re-run:\n```bash\nZAP_TARGET_URL={target} python3 scripts/run_zap_baseline.py\n```\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="OWASP ZAP baseline DAST scan")
    parser.add_argument("--target", default=os.environ.get("ZAP_TARGET_URL"), help="Target URL (or set ZAP_TARGET_URL)")
    parser.add_argument("--include-active", action="store_true", help="Run zap-full-scan.py instead of zap-baseline.py")
    parser.add_argument("--fail-on", choices=["warn", "fail"], default="fail", help="Treat WARN or FAIL as failure (default: fail)")
    parser.add_argument("--write-report", action="store_true", help="Write docs/ZAP_BASELINE_REPORT.md regardless of skip")
    args = parser.parse_args()

    if not args.target:
        msg = "no target URL (set --target or ZAP_TARGET_URL); ZAP scan skipped"
        print(msg)
        if args.write_report:
            _write_skip_report(msg)
        return 3

    docker = shutil.which("docker")
    if not docker:
        msg = "Docker not installed; ZAP runs via the `zaproxy/zap-stable` Docker image"
        print(msg)
        if args.write_report:
            _write_skip_report(msg)
        return 3

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    html_path = REPORT_DIR / f"zap_baseline_{stamp}.html"
    json_path = REPORT_DIR / f"zap_baseline_{stamp}.json"

    script = "zap-full-scan.py" if args.include_active else "zap-baseline.py"
    cmd = [
        docker, "run", "--rm",
        "-v", f"{REPORT_DIR}:/zap/wrk/:rw",
        ZAP_IMAGE, script,
        "-t", args.target,
        "-r", html_path.name,
        "-J", json_path.name,
    ]
    print("Running:", " ".join(cmd))
    code, stdout, stderr = _run(cmd)
    print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)

    if args.write_report:
        _write_summary_report(args.target, html_path, json_path, code)

    # ZAP exit codes: 0 clean, 1 WARN, 2 FAIL, 3 internal
    if code == 0:
        return 0
    if code == 1 and args.fail_on == "warn":
        return 2
    if code == 2:
        return 2
    if code == 3:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as exc:  # pragma: no cover
        print(f"zap scan failed: {exc}", file=sys.stderr)
        sys.exit(1)
