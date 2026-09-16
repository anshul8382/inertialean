#!/usr/bin/env python3
"""
Daily local codebase backup + retention prune.
Configure with env:
  CODEBASE_BACKUP_ROOT   (default: <parent of app>/inertia_codebase_backups)
  CODEBASE_BACKUP_SOURCE (default: app repo root)

Cron example (2:15 UTC):
  15 2 * * * cd /home/inertia/app && /usr/bin/python3 scripts/codebase_backup.py >> /var/log/inertia_codebase_backup.log 2>&1
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def main() -> int:
    from services.codebase_backup_service import run_daily_codebase_backup

    cfg = {
        "CODEBASE_BACKUP_ROOT": os.environ.get("CODEBASE_BACKUP_ROOT"),
        "CODEBASE_BACKUP_SOURCE": os.environ.get("CODEBASE_BACKUP_SOURCE"),
    }
    cfg = {k: v for k, v in cfg.items() if v}
    result = run_daily_codebase_backup(cfg)
    if result.get("ok"):
        print(
            "OK",
            result.get("archive_name"),
            result.get("size_bytes"),
            "prune_removed",
            result.get("prune", {}).get("removed_count"),
        )
        return 0
    print("FAIL", result.get("error"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
