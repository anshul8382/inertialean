#!/usr/bin/env python3
"""
Daily MySQL logical backup: mysqldump → gzip under backups/, prune old files.

Intended for Airflow (database_backup_daily) or cron. Loads /home/inertia/app/.env
via python-dotenv when present, then uses config.Config for DB credentials.

Env (optional):
  BACKUP_DIR       — default: <app>/backups
  BACKUP_RETENTION_DAYS — default: 30
  CODEBASE_BACKUP_DRIVE_FOLDER_ID / ENABLED — Shared drive upload (same as codebase)
  BACKUP_DRIVE_LATEST_NAME — default KVM_<DB_NAME>_latest.sql.gz (replaced each run)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily MySQL dump + gzip + retention")
    parser.add_argument(
        "--app-root",
        default=str(_APP_ROOT),
        help="Application root (default: parent of scripts/)",
    )
    args = parser.parse_args()
    app_root = Path(args.app_root).resolve()

    os.chdir(app_root)
    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))

    try:
        from dotenv import load_dotenv

        load_dotenv(app_root / ".env")
    except ImportError:
        pass

    from config import Config

    cfg = Config()
    backup_dir = Path(os.environ.get("BACKUP_DIR") or (app_root / "backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        retention_days = int(os.environ.get("BACKUP_RETENTION_DAYS", "30"))
    except ValueError:
        retention_days = 30

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sql_path = backup_dir / f"{cfg.DB_NAME}_{ts}.sql"

    cmd = [
        "mysqldump",
        "-u",
        cfg.DB_USER,
        f"-p{cfg.DB_PASSWORD}",
        "-h",
        cfg.DB_HOST,
        "-P",
        str(cfg.DB_PORT),
        "--single-transaction",
        "--quick",
        "--no-tablespaces",
        cfg.DB_NAME,
    ]
    print(f"Backing up {cfg.DB_NAME} on {cfg.DB_HOST} → {sql_path.name}.gz …", flush=True)

    try:
        with open(sql_path, "wb") as f:
            proc = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=7200)
    except subprocess.TimeoutExpired:
        sql_path.unlink(missing_ok=True)
        print("mysqldump timed out after 7200s", file=sys.stderr)
        return 1

    err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    if proc.returncode != 0:
        sql_path.unlink(missing_ok=True)
        print(err or "mysqldump failed", file=sys.stderr)
        return proc.returncode

    if "Using a password on the command line" not in err and err.strip():
        print(err.strip(), flush=True)

    subprocess.run(["gzip", "-f", str(sql_path)], check=True)
    gz = sql_path.with_suffix(".sql.gz")
    print(f"OK {gz} ({gz.stat().st_size} bytes)", flush=True)

    cutoff = time.time() - retention_days * 86400
    removed = 0
    for p in backup_dir.glob(f"{cfg.DB_NAME}_*.sql.gz"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        print(f"Pruned {removed} backup(s) older than {retention_days} days", flush=True)

    # Optional: push latest DB dump to Google Shared drive (replaces same Drive filename)
    try:
        from services.google_drive_backup_service import (
            drive_upload_enabled,
            upload_file_to_drive_folder,
        )

        if drive_upload_enabled():
            drive_name = (
                os.environ.get("BACKUP_DRIVE_LATEST_NAME") or ""
            ).strip() or f"KVM_{cfg.DB_NAME}_latest.sql.gz"
            result = upload_file_to_drive_folder(gz, name=drive_name)
            if result.get("ok"):
                print(
                    f"Drive OK {result.get('name')} "
                    f"(replaced={result.get('replaced')}) {result.get('web_view_link') or ''}",
                    flush=True,
                )
            else:
                print(f"Drive upload failed (local kept): {result.get('error')}", flush=True)
    except Exception as exc:
        print(f"Drive upload skipped/failed (local kept): {exc}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
