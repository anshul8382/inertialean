#!/usr/bin/env python3
"""
Audit API v1/v2 modules for duplicated business logic vs shared services.

Usage:
  python3 scripts/audit_api_shared_services.py
  python3 scripts/audit_api_shared_services.py --fail-on findings
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "api" / "v1"
V2 = ROOT / "api" / "v2"
SERVICES = ROOT / "services"

# v2 importing calculation helpers from v1 is allowed short-term; flag heavy v1 imports
V1_IMPORT_RE = re.compile(r"^\s*from\s+api\.v1\.(\w+)", re.M)
V2_FILES = list(V2.glob("*.py")) if V2.exists() else []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-on", action="store_true", help="Exit 1 if any v2→v1 imports found")
    args = parser.parse_args()

    findings: list[str] = []
    for path in sorted(V2_FILES):
        if path.name.startswith("__"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in V1_IMPORT_RE.finditer(text):
            mod = m.group(1)
            if mod in ("auth_guard",):
                continue
            findings.append(f"{path.relative_to(ROOT)} imports api.v1.{mod} — prefer services/")

    # Routes with export/download without audit (quick scan)
    export_missing: list[str] = []
    export_re = re.compile(r"def\s+(\w*(?:export|download)\w*)\s*\(", re.I)
    for route_file in (ROOT / "routes").glob("**/*.py"):
        text = route_file.read_text(encoding="utf-8", errors="replace")
        if "send_file" not in text and "attachment" not in text:
            continue
        for m in export_re.finditer(text):
            fn = m.group(1)
            start = m.start()
            window = text[start : start + 2500]
            if "log_data_export" not in window and "log_audit_event" not in window:
                export_missing.append(f"{route_file.relative_to(ROOT)}::{fn}")

    print("=== API shared-service audit ===\n")
    if findings:
        print(f"v2 → v1 imports ({len(findings)}):")
        for f in findings:
            print(f"  [WARN] {f}")
    else:
        print("v2 → v1 imports: none")

    print()
    if export_missing:
        print(f"Possible exports without audit ({len(export_missing)}):")
        for f in export_missing[:20]:
            print(f"  [CHECK] {f}")
        if len(export_missing) > 20:
            print(f"  ... and {len(export_missing) - 20} more")
    else:
        print("Export audit scan: no obvious gaps")

    print(f"\nServices modules: {len(list(SERVICES.glob('**/*.py')))} files under services/")

    if args.fail_on and findings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
