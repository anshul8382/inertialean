"""IIS gateway — Inertia session → SSO → proxy to local FastAPI IIS.

Enable with IIS_ENABLED=true. Upstream defaults to http://127.0.0.1:8080.
All /iis/* traffic requires an authenticated Inertia user (and normal 2FA).
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

import httpx
from flask import Blueprint, Response, current_app, redirect, request, url_for
from flask_login import current_user, login_required

logger = logging.getLogger(__name__)

iis_bp = Blueprint("iis_gateway", __name__, url_prefix="/iis")

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _upstream() -> str:
    return (current_app.config.get("IIS_UPSTREAM") or os.environ.get("IIS_UPSTREAM") or "http://127.0.0.1:8080").rstrip(
        "/"
    )


def _roles_for_user(user) -> list[str]:
    roles: list[str] = []
    for attr, label in (
        ("is_admin", "admin"),
        ("is_manager", "manager"),
        ("is_advisor", "advisor"),
        ("is_ops_manager", "ops_manager"),
        ("is_research", "research"),
    ):
        if getattr(user, attr, False):
            roles.append(label)
    # Role relationship (lean User model)
    try:
        role = getattr(user, "role", None)
        name = getattr(role, "name", None) if role is not None else None
        if name and name not in roles:
            roles.append(str(name))
    except Exception:
        pass
    return roles


def _mint_sso() -> str:
    from services.iis_sso import mint_inertia_iis_token

    ttl = int(current_app.config.get("IIS_SSO_TTL_SEC") or os.environ.get("IIS_SSO_TTL_SEC") or 3600)
    return mint_inertia_iis_token(
        user_id=current_user.id,
        email=getattr(current_user, "email", "") or "",
        name=getattr(current_user, "name", None)
        or getattr(current_user, "full_name", None)
        or getattr(current_user, "username", "")
        or "",
        roles=_roles_for_user(current_user),
        ttl_sec=ttl,
    )


def _filter_request_headers(sso: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers:
        lk = key.lower()
        if lk in _HOP_BY_HOP or lk == "cookie":
            continue
        headers[key] = value
    headers["X-Inertia-SSO"] = sso
    # Prefer SSO over any stale IIS API token from the browser
    headers.pop("X-API-Token", None)
    headers.pop("Authorization", None)
    return headers


def _filter_response_headers(resp: httpx.Response) -> Iterable[tuple[str, str]]:
    for key, value in resp.headers.multi_items():
        if key.lower() in _HOP_BY_HOP:
            continue
        yield key, value


@iis_bp.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@iis_bp.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@login_required
def proxy(path: str):
    """Reverse-proxy to the IIS FastAPI process with Inertia SSO injected."""
    if not current_app.config.get("NAV_IIS_ENABLED"):
        return Response("IIS is disabled (set IIS_ENABLED=true).", status=404)

    # Normalize: /iis → /iis/
    if path == "" and not request.path.endswith("/"):
        qs = request.query_string.decode() if request.query_string else ""
        target = url_for("iis_gateway.proxy", path="")
        if qs:
            target = f"{target}?{qs}"
        return redirect(target)

    upstream = _upstream()
    url = f"{upstream}/{path}" if path else f"{upstream}/"
    if request.query_string:
        url = f"{url}?{request.query_string.decode()}"

    try:
        sso = _mint_sso()
    except Exception as e:
        logger.exception("IIS SSO mint failed")
        return Response(f"IIS SSO not configured: {e}", status=503)

    headers = _filter_request_headers(sso)
    body = request.get_data()

    try:
        with httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0), follow_redirects=False) as client:
            upstream_resp = client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body if body else None,
            )
    except httpx.ConnectError:
        return Response(
            "IIS backend is not running. Start it with: ./scripts/run_iis_dev.sh\n"
            f"(expected upstream {upstream})",
            status=503,
            mimetype="text/plain",
        )
    except Exception as e:
        logger.exception("IIS proxy error")
        return Response(f"IIS proxy error: {e}", status=502)

    return Response(
        upstream_resp.content,
        status=upstream_resp.status_code,
        headers=list(_filter_response_headers(upstream_resp)),
    )
