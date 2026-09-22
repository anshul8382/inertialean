"""
API v1 authentication guard — session cookie OR Bearer JWT (mobile).

Public endpoints are explicitly allowlisted; new public routes must be added here
deliberately (do not remove existing authenticated features).
"""
from __future__ import annotations

from flask import request
from flask_login import current_user

# Paths under /api/v1 that stay unauthenticated (suffix match on full path)
PUBLIC_V1_PATH_SUFFIXES = (
    "/auth/login",
    "/auth/verify-2fa",
    "/public/contact",
    "/public/careers",
    "/public/booking-intake",
    "/public/booking-prep-email",
    "/whatsapp/webhook",
    "/leegality/webhook",
    "/health",
    "/health/ready",
)

# Blueprints that implement their own auth or are intentionally public
PUBLIC_V1_BLUEPRINT_MARKERS = (
    "auth_api",
    "public_contact",
)


def is_public_v1_request() -> bool:
    if request.method == "OPTIONS":
        return True
    path = request.path or ""
    if not path.startswith("/api/v1"):
        return False
    for suffix in PUBLIC_V1_PATH_SUFFIXES:
        if path.endswith(suffix) or suffix in path:
            return True
    return False


def enforce_v1_api_auth():
    from api.core.response import APIResponse

    if is_public_v1_request():
        return None
    if current_user.is_authenticated:
        return None
    return APIResponse.error(
        "Authentication required",
        status_code=401,
        error_code="unauthorized",
    )
