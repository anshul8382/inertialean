"""Verify Inertia-minted SSO tokens inside IIS."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional

from iis.config import settings


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def verify_token(token: str) -> Optional[dict[str, Any]]:
    secret = (settings.inertia_sso_secret or "").strip()
    if not secret or not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    if not body or not sig:
        return None
    key = secret.encode("utf-8")
    expected = _b64url_encode(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        exp = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    if exp < int(time.time()):
        return None
    if not payload.get("uid") or not payload.get("email"):
        return None
    return payload
