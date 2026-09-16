"""
Health / readiness endpoints for ops and deploy probes.

Public (no auth): GET /api/v1/health, GET /api/v1/health/ready
"""
from __future__ import annotations

from flask import Blueprint

from api.core.response import APIResponse
from services.health_service import liveness_payload, readiness_payload

health_bp = Blueprint("health_api", __name__)


@health_bp.route("/health", methods=["GET"])
def health_liveness():
    return APIResponse.success(data=liveness_payload(), message="OK")


@health_bp.route("/health/ready", methods=["GET"])
def health_readiness():
    payload = readiness_payload()
    status = 200 if payload.get("status") == "ok" else 503
    return APIResponse.success(data=payload, message=payload.get("status", "unknown"), status_code=status)
