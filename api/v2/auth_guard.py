"""V2 API auth — session or Bearer JWT; preserve public health check."""
from __future__ import annotations

from flask import request
from flask_login import current_user

PUBLIC_V2_PATH_SUFFIXES = (
    "/transactions/health",
)


def is_public_v2_request() -> bool:
    if request.method == "OPTIONS":
        return True
    path = request.path or ""
    for suffix in PUBLIC_V2_PATH_SUFFIXES:
        if path.endswith(suffix):
            return True
    return False


def enforce_v2_api_auth():
    from api.core.response import APIResponse

    if is_public_v2_request():
        return None
    if current_user.is_authenticated:
        return None
    return APIResponse.error(
        "Authentication required",
        status_code=401,
        error_code="unauthorized",
    )
