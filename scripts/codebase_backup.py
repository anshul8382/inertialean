#!/usr/bin/env python3
"""
Daily local codebase backup + retention prune.
Configure with env:
  CODEBASE_BACKUP_ROOT   (default: <parent of app>/inertia_codebase_backups)
  CODEBASE_BACKUP_SOURCE (default: app repo root)
  CODEBASE_BACKUP_DRIVE_FOLDER_ID  — Google Drive folder ID (share with SA email)
  CODEBASE_BACKUP_DRIVE_ENABLED=true  — optional; auto-on when folder ID set
  BACKUP_DRIVE_CODE_LATEST_NAME — Drive object name (default: KVM_inertia_code_latest.tar.gz)

Cron example (2:15 UTC):
  15 2 * * * cd /opt/Inertia2026v1 && ./venv/bin/python scripts/codebase_backup.py >> /var/log/inertia_codebase_backup.log 2>&1
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(APP_DIR, ".env"), override=True)
    except ImportError:
        pass

    from services.codebase_backup_service import run_daily_codebase_backup

    cfg = {
        "CODEBASE_BACKUP_ROOT": os.environ.get("CODEBASE_BACKUP_ROOT"),
        "CODEBASE_BACKUP_SOURCE": os.environ.get("CODEBASE_BACKUP_SOURCE"),
        "CODEBASE_BACKUP_DRIVE_FOLDER_ID": os.environ.get("CODEBASE_BACKUP_DRIVE_FOLDER_ID"),
        "CODEBASE_BACKUP_DRIVE_ENABLED": os.environ.get("CODEBASE_BACKUP_DRIVE_ENABLED"),
        "BACKUP_DRIVE_CODE_LATEST_NAME": os.environ.get("BACKUP_DRIVE_CODE_LATEST_NAME"),
        "GOOGLE_SERVICE_ACCOUNT_FILE": os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE"),
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
        drive = result.get("drive") or {}
        if drive:
            print("Drive", drive.get("ok"), drive.get("name") or drive.get("error"))
        return 0
    print("FAIL", result.get("error"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
