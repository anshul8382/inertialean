#!/usr/bin/env python3
"""
Scan templates/ for mobile UI risk patterns.

Usage:
  python3 scripts/audit_mobile_templates.py
  python3 scripts/audit_mobile_templates.py --json reports/mobile_audit.json
  python3 scripts/audit_mobile_templates.py --severity high
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

PATTERNS = {
    "no_base_layout": {
        "severity": "high",
        "regex": re.compile(r"extends\s+['\"]base\.html['\"]", re.I),
        "invert": True,
        "hint": "Does not extend base.html — misses global mobile stack",
    },
    "inline_width": {
        "severity": "medium",
        "regex": re.compile(r'style\s*=\s*["\'][^"\']*width\s*:', re.I),
        "hint": "Inline width styles often break narrow viewports",
    },
    "min_width_style": {
        "severity": "medium",
        "regex": re.compile(r"min-width\s*:", re.I),
        "hint": "min-width in template/style may force horizontal scroll",
    },
    "header_flex": {
        "severity": "low",
        "regex": re.compile(r"d-flex[^\"']*justify-content-between", re.I),
        "hint": "Title + actions row — global CSS stacks these; verify on phone",
    },
    "datatables": {
        "severity": "medium",
        "regex": re.compile(r"DataTable\s*\(|dataTables", re.I),
        "hint": "DataTables need mobile filter stacking; large tables may need column hiding",
    },
    "bare_table": {
        "severity": "low",
        "regex": re.compile(r"<table\b", re.I),
        "requires_absence": re.compile(r"table-responsive|mobile-card-table|rec-card-table|data-mobile-table", re.I),
        "hint": "Table without explicit wrapper/class — auto-wrapped by mobile-layout.js",
    },
    "col_md_only": {
        "severity": "low",
        "regex": re.compile(r'class\s*=\s*["\'][^"\']*\bcol-md-\d', re.I),
        "requires_absence": re.compile(r"col-12|col-sm-|col-lg-", re.I),
        "hint": "col-md without col-12 — Bootstrap stacks but verify spacing",
    },
}

SKIP_FILES = {
    "base.html",
    "base_clean.html",
    "email/base.html",
    "explainer.html",
    "simple_test.html",
    "test_asset_edit.html",
}


def score_file(path: Path, text: str) -> dict:
    rel = path.relative_to(TEMPLATES).as_posix()
    findings = []
    extends_base = bool(re.search(r"extends\s+['\"]base\.html['\"]", text, re.I))

    for name, cfg in PATTERNS.items():
        if name == "no_base_layout":
            if path.name in SKIP_FILES or rel.startswith("email/"):
                continue
            if not extends_base and "<html" in text.lower():
                findings.append({"rule": name, "severity": cfg["severity"], "hint": cfg["hint"], "count": 1})
            continue

        if not extends_base and name != "bare_table":
            continue

        if cfg.get("invert"):
            continue

        matches = cfg["regex"].findall(text)
        if not matches:
            continue

        if cfg.get("requires_absence") and cfg["requires_absence"].search(text):
            continue

        findings.append(
            {
                "rule": name,
                "severity": cfg["severity"],
                "hint": cfg["hint"],
                "count": len(matches),
            }
        )

    severity_rank = {"high": 3, "medium": 2, "low": 1}
    top = max((severity_rank[f["severity"]] for f in findings), default=0)
    inv_rank = {v: k for k, v in severity_rank.items()}
    return {
        "path": rel,
        "extends_base": extends_base,
        "severity": inv_rank.get(top, "none"),
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit templates for mobile UI risks")
    parser.add_argument("--json", type=Path, help="Write JSON report")
    parser.add_argument("--severity", choices=("high", "medium", "low"), help="Filter minimum severity")
    args = parser.parse_args()

    severity_order = {"high": 3, "medium": 2, "low": 1, "none": 0}
    min_sev = severity_order.get(args.severity or "low", 0)

    results = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        entry = score_file(path, text)
        if severity_order.get(entry["severity"], 0) >= min_sev and entry["findings"]:
            results.append(entry)

    results.sort(key=lambda r: (-severity_order.get(r["severity"], 0), -sum(f["count"] for f in r["findings"]), r["path"]))

    summary = defaultdict(int)
    for r in results:
        summary[r["severity"]] += 1
        for f in r["findings"]:
            summary[f"rule:{f['rule']}"] += 1

    report = {
        "template_count": len(list(TEMPLATES.rglob("*.html"))),
        "flagged_count": len(results),
        "summary": dict(summary),
        "files": results[:100],
    }

    print(f"Templates scanned: {report['template_count']}")
    print(f"Flagged files: {report['flagged_count']}")
    for sev in ("high", "medium", "low"):
        if summary.get(sev):
            print(f"  {sev}: {summary[sev]}")
    print("\nTop 20 by risk:")
    for entry in results[:20]:
        counts = ", ".join(f"{f['rule']}={f['count']}" for f in entry["findings"])
        print(f"  [{entry['severity']}] {entry['path']} — {counts}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
