from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException

from iis.auth.models import User, hash_password
from iis.auth.store import UserStore
from iis.config import settings

_user_store: UserStore | None = None


def get_user_store() -> UserStore:
    global _user_store
    if _user_store is None:
        _user_store = UserStore()
        if settings.bootstrap_admin:
            _user_store.ensure_default_admin()
    return _user_store


def _user_from_inertia_sso(token: str, store: UserStore) -> User | None:
    from iis.auth.inertia_sso import verify_token

    payload = verify_token(token)
    if not payload:
        return None
    email = str(payload.get("email") or "").strip().lower()
    name = str(payload.get("name") or email or "Inertia user")
    uid = str(payload.get("uid") or "")
    if not email or not uid:
        return None

    existing = store.get_by_email(email)
    if existing:
        return existing

    user = User(
        id=f"inertia-{uid}",
        email=email,
        name=name,
        password_hash=hash_password(f"inertia-sso:{uid}:{email}"),
    )
    store.save(user)
    return store.get_by_email(email) or user


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    x_api_token: Annotated[str | None, Header(alias="X-API-Token")] = None,
    x_inertia_sso: Annotated[str | None, Header(alias="X-Inertia-SSO")] = None,
    store: UserStore = Depends(get_user_store),
) -> User:
    sso = x_inertia_sso
    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()

    if settings.inertia_sso_secret:
        for candidate in (sso, bearer):
            if not candidate:
                continue
            sso_user = _user_from_inertia_sso(candidate, store)
            if sso_user:
                return sso_user
        if settings.inertia_auth_only and (sso or bearer):
            raise HTTPException(401, "Invalid or expired Inertia SSO token")

    if not settings.auth_required and not settings.inertia_auth_only:
        admin = store.get_by_email(settings.default_admin_email)
        if admin:
            return admin
        return store.ensure_default_admin()

    token = x_api_token or bearer
    if not token:
        raise HTTPException(
            401,
            "Authentication required. Provide X-Inertia-SSO, X-API-Token, or Authorization: Bearer",
        )

    if settings.inertia_sso_secret:
        sso_user = _user_from_inertia_sso(token, store)
        if sso_user:
            return sso_user

    user = store.get_by_token(token)
    if not user:
        raise HTTPException(401, "Invalid API token")
    return user


def get_optional_user(
    authorization: Annotated[str | None, Header()] = None,
    x_api_token: Annotated[str | None, Header(alias="X-API-Token")] = None,
    x_inertia_sso: Annotated[str | None, Header(alias="X-Inertia-SSO")] = None,
    store: UserStore = Depends(get_user_store),
) -> User | None:
    if (
        not settings.auth_required
        and not settings.inertia_auth_only
        and not authorization
        and not x_api_token
        and not x_inertia_sso
    ):
        return None
    try:
        return get_current_user(authorization, x_api_token, x_inertia_sso, store)
    except HTTPException:
        return None
