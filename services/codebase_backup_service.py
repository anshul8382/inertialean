"""
Local codebase tar.gz backups with monthly retention:
- Current calendar month: keep every daily backup.
- Earlier months: keep only the backup dated the last calendar day of that month.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATUS_FILENAME = "codebase_backup_status.json"
ARCHIVE_GLOB = "inertia-code-*.tar.gz"
ARCHIVE_RE = re.compile(r"^inertia-code-(\d{4})-(\d{2})-(\d{2})\.tar\.gz$")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_backup_root(app_config: Optional[dict] = None) -> Path:
    raw = (app_config or {}).get("CODEBASE_BACKUP_ROOT") or os.environ.get("CODEBASE_BACKUP_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return (_repo_root().parent / "inertia_codebase_backups").resolve()


def resolve_source_root(app_config: Optional[dict] = None) -> Path:
    raw = (app_config or {}).get("CODEBASE_BACKUP_SOURCE") or os.environ.get("CODEBASE_BACKUP_SOURCE")
    if raw:
        return Path(raw).expanduser().resolve()
    return _repo_root().resolve()


def status_path(backup_root: Path) -> Path:
    return backup_root / STATUS_FILENAME


def last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def prune_old_backups(daily_dir: Path, today: Optional[date] = None) -> Dict[str, Any]:
    """
    Delete daily archives not in the current month unless they fall on the
    last day of their month (monthly retention anchor).
    """
    today = today or date.today()
    first_current = date(today.year, today.month, 1)
    removed: List[str] = []
    if not daily_dir.is_dir():
        return {"removed": removed, "removed_count": 0}
    for f in sorted(daily_dir.glob(ARCHIVE_GLOB)):
        m = ARCHIVE_RE.match(f.name)
        if not m:
            continue
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            bd = date(y, mo, d)
        except ValueError:
            continue
        if bd >= first_current:
            continue
        if bd == last_day_of_month(y, mo):
            continue
        try:
            f.unlink()
            removed.append(f.name)
        except OSError as e:
            logger.warning("Could not remove old backup %s: %s", f, e)
    return {"removed": removed, "removed_count": len(removed)}


def _default_excludes(source_name: str) -> List[str]:
    # Paths relative to parent of source (tar -C parent)
    return [
        f"{source_name}/__pycache__",
        f"{source_name}/.git",
        f"{source_name}/.venv",
        f"{source_name}/venv",
        f"{source_name}/.venv_airflow_test",
        f"{source_name}/node_modules",
        f"{source_name}/flask_sessions",
        f"{source_name}/logs",
        f"{source_name}/airflow/logs",
        f"{source_name}/*.pyc",
    ]


def run_daily_codebase_backup(app_config: Optional[dict] = None) -> Dict[str, Any]:
    """
    Create today's archive under backup_root/daily/, then prune per retention rules.
    """
    cfg = app_config or {}
    backup_root = resolve_backup_root(cfg)
    source = resolve_source_root(cfg)
    daily_dir = backup_root / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    if not source.is_dir():
        err = f"Source directory missing: {source}"
        logger.error(err)
        _write_status(backup_root, ok=False, error=err, archive=None, prune=None, source=str(source))
        return {"ok": False, "error": err}

    parent = source.parent
    folder = source.name
    today_str = date.today().strftime("%Y-%m-%d")
    archive_name = f"inertia-code-{today_str}.tar.gz"
    dest = daily_dir / archive_name

    excludes = cfg.get("CODEBASE_BACKUP_TAR_EXCLUDES")
    if not excludes:
        excludes = _default_excludes(folder)

    cmd: List[str] = [
        "tar",
        "--warning=no-file-changed",
        "--warning=no-file-removed",
        "-czf",
        str(dest),
        "-C",
        str(parent),
    ]
    for ex in excludes:
        cmd.append(f"--exclude={ex}")
    cmd.append(folder)

    started = datetime.utcnow().isoformat() + "Z"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        err_text = (proc.stderr or proc.stdout or "")[:2000]
        # GNU tar: live trees often get exit 1 (e.g. file changed while reading). Accept if archive exists and is non-trivial.
        if proc.returncode != 0:
            ok_archive = dest.is_file() and dest.stat().st_size > 1_000_000
            if proc.returncode == 1 and ok_archive:
                logger.warning(
                    "tar exited 1 but produced archive %s (%s bytes); stderr: %s",
                    dest,
                    dest.stat().st_size,
                    err_text[:500],
                )
            else:
                err = err_text or "tar failed"
                logger.error("tar backup failed: %s", err)
                _write_status(
                    backup_root,
                    ok=False,
                    error=err,
                    archive=None,
                    prune=None,
                    source=str(source),
                    started=started,
                )
                return {"ok": False, "error": err, "cmd": " ".join(cmd)}
    except subprocess.TimeoutExpired:
        err = "tar timed out after 1 hour"
        _write_status(backup_root, ok=False, error=err, archive=None, prune=None, source=str(source))
        return {"ok": False, "error": err}
    except OSError as e:
        err = str(e)
        _write_status(backup_root, ok=False, error=err, archive=None, prune=None, source=str(source))
        return {"ok": False, "error": err}

    size_bytes = dest.stat().st_size if dest.is_file() else 0
    prune = prune_old_backups(daily_dir)
    _write_status(
        backup_root,
        ok=True,
        error=None,
        archive=str(dest.relative_to(backup_root)),
        prune=prune,
        source=str(source),
        started=started,
        size_bytes=size_bytes,
    )
    return {
        "ok": True,
        "archive": str(dest),
        "archive_name": archive_name,
        "size_bytes": size_bytes,
        "prune": prune,
    }


def _write_status(
    backup_root: Path,
    ok: bool,
    error: Optional[str],
    archive: Optional[str],
    prune: Optional[Dict[str, Any]],
    source: str,
    started: Optional[str] = None,
    size_bytes: int = 0,
) -> None:
    backup_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_run_utc": datetime.utcnow().isoformat() + "Z",
        "backup_started_utc": started,
        "last_ok": ok,
        "last_error": error,
        "last_archive_relative": archive,
        "last_size_bytes": size_bytes,
        "source": source,
        "backup_root": str(backup_root),
        "retention": "current_month_all_dailies; older_months_last_day_only",
        "last_prune_removed": (prune or {}).get("removed", []),
        "last_prune_removed_count": (prune or {}).get("removed_count", 0),
    }
    try:
        status_path(backup_root).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not write backup status: %s", e)


def read_backup_status(app_config: Optional[dict] = None) -> Dict[str, Any]:
    """Load last status JSON; empty dict if missing."""
    backup_root = resolve_backup_root(app_config or {})
    p = status_path(backup_root)
    if not p.is_file():
        return {
            "backup_root": str(backup_root),
            "last_ok": None,
            "message": "No backup has been recorded yet.",
        }
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data["status_file"] = str(p)
        return data
    except (OSError, json.JSONDecodeError) as e:
        return {"backup_root": str(backup_root), "last_ok": False, "last_error": str(e)}


def list_recent_backups(app_config: Optional[dict] = None, limit: int = 14) -> List[Dict[str, Any]]:
    """Newest-first list of files in daily/ with sizes."""
    daily = resolve_backup_root(app_config or {}) / "daily"
    if not daily.is_dir():
        return []
    rows = []
    for f in sorted(daily.glob(ARCHIVE_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            st = f.stat()
            rows.append({"name": f.name, "size_bytes": st.st_size, "mtime_utc": datetime.utcfromtimestamp(st.st_mtime).isoformat() + "Z"})
        except OSError:
            continue
    return rows
