#!/usr/bin/env python3
"""Install IST crontab lines for price_update_sheets and nifty_price_update."""
from __future__ import annotations

import re
import subprocess
import sys

APP_DIR = "/home/inertia/app"
PYTHON = f"{APP_DIR}/venv/bin/python"

PRICE_BLOCK = f"""# Stock prices: 9:30, 12:30, 4:00 PM IST (Asia/Kolkata) — 4 PM saves historical_price
30 9 * * 1-5 cd {APP_DIR} && {PYTHON} {APP_DIR}/price_update_sheets.py >> {APP_DIR}/logs/price_update.log 2>&1
30 12 * * 1-5 cd {APP_DIR} && {PYTHON} {APP_DIR}/price_update_sheets.py >> {APP_DIR}/logs/price_update.log 2>&1
0 16 * * 1-5 cd {APP_DIR} && {PYTHON} {APP_DIR}/price_update_sheets.py >> {APP_DIR}/logs/price_update.log 2>&1
"""

NIFTY_BLOCK = f"""# Nifty: 9:30, 12:30, 4:00 PM IST
30 9 * * 1-5 cd {APP_DIR} && {PYTHON} {APP_DIR}/nifty_price_update.py >> {APP_DIR}/logs/nifty_update.log 2>&1
30 12 * * 1-5 cd {APP_DIR} && {PYTHON} {APP_DIR}/nifty_price_update.py >> {APP_DIR}/logs/nifty_update.log 2>&1
0 16 * * 1-5 cd {APP_DIR} && {PYTHON} {APP_DIR}/nifty_price_update.py >> {APP_DIR}/logs/nifty_update.log 2>&1
"""

DROP = (
    re.compile(r"price_update_sheets\.py"),
    re.compile(r"nifty_price_update\.py"),
    re.compile(r"^#.*Stock [Pp]rice"),
    re.compile(r"^#.*Nifty"),
    re.compile(r"^#.*9:30 AM IST"),
    re.compile(r"^#.*12:30 PM IST"),
    re.compile(r"^#.*4:00 PM IST"),
    re.compile(r"^#.*4:30 PM IST"),
    re.compile(r"^# Stock Price Updates"),
    re.compile(r"^# Nifty Price Update"),
)


def _drop(line: str) -> bool:
    s = line.strip()
    return bool(s and any(p.search(s) for p in DROP))


def main() -> int:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = r.stdout if r.returncode == 0 else ""
    kept = [ln for ln in existing.splitlines() if not _drop(ln)]
    text = "\n".join(kept).rstrip() + "\n\n" + PRICE_BLOCK + "\n" + NIFTY_BLOCK + "\n"
    p = subprocess.run(["crontab", "-"], input=text, text=True, capture_output=True)
    if p.returncode:
        print(p.stderr or p.stdout, file=sys.stderr)
        return 1
    print("Installed IST price cron. Active lines:")
    subprocess.run(["crontab", "-l"], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
