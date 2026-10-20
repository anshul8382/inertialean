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
    by_name = dict(data.get("by_name") or {})
    removed = []
    for key in list(by_name.keys()):
        kl = str(key).lower()
        if any(t in kl for t in ("bees", "gold bee", "liquid bee", "goldbees", "liquidbees")):
            removed.append((key, by_name.pop(key)))
    if not removed:
        print("No Bees-related by_name entries found.")
        return 0
    data["by_name"] = by_name
    MAPS.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Removed {len(removed)} by_name entr(y/ies) from {MAPS}:")
    for k, v in removed:
        print(f"  {k!r} -> {v}")
    print("Re-load statement from Drive (or Refresh after deploying preserve fix), then rematch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
