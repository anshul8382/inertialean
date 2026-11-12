"""Mint / verify short-lived SSO tokens for IIS (Inertia → FastAPI).

Token format (compact):
  base64url(payload_json).base64url(hmac_sha256)
Payload keys: uid, email, name, exp (unix seconds), roles (optional list)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Optional


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def sso_secret() -> str:
    return (os.environ.get("IIS_SSO_SECRET") or os.environ.get("SECRET_KEY") or "").strip()


def mint_inertia_iis_token(
    *,
    user_id: int | str,
    email: str,
    name: str = "",
    roles: list[str] | None = None,
    ttl_sec: int = 3600,
    secret: str | None = None,
) -> str:
    key = (secret if secret is not None else sso_secret()).encode("utf-8")
    if not key:
        raise RuntimeError("IIS_SSO_SECRET (or SECRET_KEY) is required to mint IIS SSO tokens")
    payload = {
        "uid": str(user_id),
        "email": (email or "").strip().lower(),
        "name": name or "",
        "roles": roles or [],
        "exp": int(time.time()) + max(60, int(ttl_sec)),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url_encode(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_inertia_iis_token(token: str, *, secret: str | None = None) -> Optional[dict[str, Any]]:
    if not token or "." not in token:
        return None
    key = (secret if secret is not None else sso_secret()).encode("utf-8")
    if not key:
        return None
    body, _, sig = token.partition(".")
    if not body or not sig:
        return None
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
