#!/usr/bin/env python3
"""Clear poisoned Gold/Liquid Bees → DBCORP name maps (local or on KVM).

Run on the app host:
  cd /opt/Inertia2026v1 && ./venv/bin/python scripts/repair_holdings_bees_dbcorp_maps.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAPS = ROOT / "var" / "holdings_reconciliation" / "security_maps.json"


def main() -> int:
    if not MAPS.is_file():
        print(f"No maps file at {MAPS}")
        return 0
    data = json.loads(MAPS.read_text(encoding="utf-8"))
    removed = []
    for section in ("by_name", "by_isin", "by_nse", "by_broker_code"):
        bucket = dict(data.get(section) or {})
        for key in list(bucket.keys()):
            kl = str(key).lower()
            if section == "by_name" and any(
                t in kl
                for t in ("bees", "gold bee", "liquid bee", "goldbees", "liquidbees")
            ):
                removed.append((section, key, bucket.pop(key)))
            elif section == "by_isin" and kl.startswith("inf204kb"):
                # Nippon Bees ISINs — drop if mapped (often wrongly to DBCORP)
                removed.append((section, key, bucket.pop(key)))
        data[section] = bucket
    if not removed:
        print("No Bees-related map entries found.")
        return 0
    MAPS.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Removed {len(removed)} entr(y/ies) from {MAPS}:")
    for section, k, v in removed:
        print(f"  {section} {k!r} -> {v}")
    print("Map Gold→GOLDBEES and Liquid→LIQUIDBEES on confirm, then rematch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
