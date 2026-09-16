"""
JWT helpers for mobile and API clients (Bearer token on /api/v1/*).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

try:
    import jwt
except ImportError:
    jwt = None  # type: ignore

from config import Config

logger = logging.getLogger(__name__)

# PyJWT recommends >= 32 bytes for HS256 (see SEC-CFG-002 / PyJWT InsecureKeyLengthWarning).
MIN_JWT_HMAC_KEY_BYTES = 32


def _secret() -> str:
    raw = (getattr(Config, "JWT_SECRET", None) or Config.SECRET_KEY or "").strip()
    if not raw:
        raw = "dev-key-please-change-in-production"
    if len(raw.encode("utf-8")) < MIN_JWT_HMAC_KEY_BYTES:
        logger.warning(
            "JWT signing key is shorter than %s bytes; set JWT_SECRET or a longer SECRET_KEY",
            MIN_JWT_HMAC_KEY_BYTES,
        )
    return raw


def issue_access_token(user_id: int, hours: int = 24) -> Optional[str]:
    if jwt is None:
        return None
    payload = {
        "user_id": user_id,
        "type": "access",
        "exp": datetime.utcnow() + timedelta(hours=hours),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def issue_2fa_pending_token(user_id: int, minutes: int = 5) -> Optional[str]:
    if jwt is None:
        return None
    payload = {
        "user_id": user_id,
        "type": "2fa_pending",
        "exp": datetime.utcnow() + timedelta(minutes=minutes),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    if jwt is None or not token:
        return None
    try:
        return jwt.decode(token, _secret(), algorithms=["HS256"])
    except Exception:
        return None


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    return payload


def user_from_pending_2fa_token(token: str):
    payload = decode_token(token)
    if not payload or payload.get("type") != "2fa_pending":
        return None
    from models import User

    return User.query.get(payload.get("user_id"))


def user_from_token(token: str):
    """Return User model instance or None."""
    payload = decode_access_token(token)
    if not payload:
        return None
    from models import User

    return User.query.get(payload.get("user_id"))
