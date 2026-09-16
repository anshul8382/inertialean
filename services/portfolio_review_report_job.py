"""
Background hybrid portfolio review report jobs.

Long-running Claude calls exceed typical reverse-proxy timeouts (60s). The UI POSTs with
async=true, receives a job_id immediately, then polls GET .../jobs/<id> until done|error.
Jobs are stored on disk under Flask instance_path (works across Gunicorn workers on one host).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_JOB_FILE_AGE_SEC = 6 * 3600


def _jobs_dir(app) -> str:
    p = os.path.join(app.instance_path, "hybrid_report_jobs")
    os.makedirs(p, exist_ok=True)
    return p


def _job_path(app, job_id: str) -> str:
    return os.path.join(_jobs_dir(app), f"{job_id}.json")


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def cleanup_old_job_files(app) -> None:
    """Best-effort prune of stale job JSON (avoids unbounded disk use)."""
    try:
        root = _jobs_dir(app)
        now = time.time()
        for name in os.listdir(root):
            if not name.endswith(".json"):
                continue
            fp = os.path.join(root, name)
            try:
                if now - os.path.getmtime(fp) > MAX_JOB_FILE_AGE_SEC:
                    os.unlink(fp)
            except OSError:
                pass
    except OSError as ex:
        logger.debug("hybrid_report_job cleanup skipped: %s", ex)


def _read_job_record(app, job_id: str) -> Optional[Dict[str, Any]]:
    path = _job_path(app, job_id)
    for _ in range(8):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            time.sleep(0.04)
    return None


def validate_job_id(job_id: str) -> bool:
    try:
        uuid.UUID(str(job_id))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def enqueue(app, user_id: int, body: Dict[str, Any]) -> str:
    cleanup_old_job_files(app)
    job_id = str(uuid.uuid4())
    rec: Dict[str, Any] = {
        "job_id": job_id,
        "user_id": int(user_id),
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "body": body,
    }
    _atomic_write_json(_job_path(app, job_id), rec)
    t = threading.Thread(target=_run_job, args=(app, job_id), daemon=True)
    t.start()
    return job_id


def _run_job(app, job_id: str) -> None:
    path = _job_path(app, job_id)
    with app.app_context():
        try:
            from services.portfolio_hybrid_report_generator import (
                ReportGeneratorError,
                generate_hybrid_report_from_dict,
            )
        except Exception as ex:
            logger.exception("hybrid_report_job import failed: %s", ex)
            _atomic_write_json(
                path,
                {
                    "job_id": job_id,
                    "status": "error",
                    "error": f"Server misconfiguration: {ex}",
                    "http_status": 500,
                },
            )
            return

        body: Dict[str, Any] = {}
        uid = 0
        try:
            cur = _read_job_record(app, job_id) or {}
            body = cur.get("body") or {}
            uid = int(cur.get("user_id") or 0)
            running = {
                "job_id": job_id,
                "user_id": uid,
                "status": "running",
                "created_at": cur.get("created_at"),
            }
            _atomic_write_json(path, running)
        except Exception as ex:
            logger.exception("hybrid_report_job could not mark running: %s", ex)
            return

        try:
            from pydantic import ValidationError

            out = generate_hybrid_report_from_dict(body)
            try:
                from routes.enhanced_review_routes import convert_dates_to_strings

                safe = convert_dates_to_strings(out)
            except Exception as conv_err:
                logger.warning("hybrid_report_job convert_dates_to_strings: %s", conv_err, exc_info=True)
                safe = out
            done = {
                "job_id": job_id,
                "user_id": uid,
                "status": "done",
                "created_at": cur.get("created_at"),
                "result": safe,
            }
            _atomic_write_json(path, done)
        except ValidationError as e:
            _atomic_write_json(
                path,
                {
                    "job_id": job_id,
                    "user_id": uid,
                    "status": "error",
                    "created_at": cur.get("created_at"),
                    "error": "Invalid request body",
                    "http_status": 400,
                    "details": e.errors(),
                },
            )
        except ReportGeneratorError as e:
            err: Dict[str, Any] = {
                "job_id": job_id,
                "user_id": uid,
                "status": "error",
                "created_at": cur.get("created_at"),
                "error": str(e),
                "http_status": int(e.status_code),
            }
            if e.status_code == 422 and isinstance(e.detail, dict):
                try:
                    from routes.enhanced_review_routes import convert_dates_to_strings

                    err["detail"] = convert_dates_to_strings(e.detail)
                except Exception:
                    err["detail"] = e.detail
            _atomic_write_json(path, err)
        except Exception as e:
            logger.exception("hybrid_report_job generate failed: %s", e)
            _atomic_write_json(
                path,
                {
                    "job_id": job_id,
                    "user_id": uid,
                    "status": "error",
                    "created_at": cur.get("created_at"),
                    "error": str(e),
                    "http_status": 500,
                },
            )


def get_status_for_user(app, job_id: str, user_id: int) -> Tuple[Dict[str, Any], int]:
    """
    Return (payload_dict, http_status) for the polling endpoint.
    """
    if not validate_job_id(job_id):
        return {"success": False, "error": "Invalid job id"}, 400
    rec = _read_job_record(app, job_id)
    if not rec:
        return {"success": False, "error": "Job not found"}, 404
    if int(rec.get("user_id") or -1) != int(user_id):
        return {"success": False, "error": "Forbidden"}, 403

    status = rec.get("status")
    if status in ("queued", "running"):
        return {"success": True, "job_id": job_id, "status": status}, 200

    if status == "done":
        result = rec.get("result") or {}
        if not isinstance(result, dict):
            result = {}
        return {"success": True, "job_id": job_id, "status": "done", **result}, 200

    if status == "error":
        code = int(rec.get("http_status") or 500)
        payload: Dict[str, Any] = {
            "success": False,
            "job_id": job_id,
            "status": "error",
            "error": rec.get("error") or "Generation failed",
        }
        if isinstance(rec.get("detail"), dict):
            payload["detail"] = rec["detail"]
        if rec.get("details") is not None:
            payload["details"] = rec["details"]
        return payload, code

    return {"success": False, "error": "Unknown job status"}, 500
