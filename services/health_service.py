"""
Health and readiness checks for ops / deploy probes.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict

from flask import current_app


def _app_version() -> str:
    return os.environ.get("APP_VERSION", "inertia-dev")


def liveness_payload() -> Dict[str, Any]:
    """Process is up — no dependency checks."""
    return {
        "status": "ok",
        "service": "inertia",
        "version": _app_version(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def readiness_payload() -> Dict[str, Any]:
    """Check DB and critical config; used by load balancers / k8s."""
    checks: Dict[str, Any] = {}
    overall = "ok"

    try:
        from extensions import db
        from sqlalchemy import text

        db.session.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        checks["database"] = {"status": "error", "detail": str(exc)[:200]}
        overall = "degraded"

    secret = current_app.config.get("SECRET_KEY") or ""
    if not secret or secret in ("", "change-me", "change-me-to-a-long-random-string"):
        checks["secret_key"] = {"status": "warn", "detail": "SECRET_KEY not configured for production"}
        if overall == "ok":
            overall = "degraded"
    else:
        checks["secret_key"] = {"status": "ok"}

    return {
        "status": overall,
        "service": "inertia",
        "version": _app_version(),
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
