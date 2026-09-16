"""
Mandatory 2FA policy — all active users must enable and verify TOTP each session.
"""
from __future__ import annotations

from typing import Optional

from flask import current_app, request, session, url_for
from flask_login import current_user


def is_global_2fa_required(app=None) -> bool:
    app_obj = app or current_app
    try:
        return bool(app_obj.config.get("FORCE_2FA_FOR_ALL_USERS", True))
    except RuntimeError:
        return True


def user_needs_2fa_setup(user) -> bool:
    if not user or not getattr(user, "is_active", True):
        return False
    if not is_global_2fa_required():
        return bool(getattr(user, "two_factor_required", False)) and not user.two_factor_enabled
    return not bool(getattr(user, "two_factor_enabled", False))


def user_needs_2fa_verify(user) -> bool:
    if not user or not user.two_factor_enabled:
        return False
    return not session.get("2fa_verified")


def _endpoint_exempt(endpoint: Optional[str]) -> bool:
    if not endpoint:
        return True
    exempt_prefixes = (
        "auth.",
        "two_factor.",
        "static",
    )
    exempt_exact = {
        "auth.login",
        "auth.logout",
        "auth.forgot_password",
        "auth.reset_password",
    }
    if endpoint in exempt_exact or endpoint.startswith(exempt_prefixes):
        return True
    if request.path.startswith("/api/v1/auth/login") or request.path.startswith("/api/v1/auth/verify-2fa"):
        return True
    return False


def enforce_session_2fa():
    """
    Redirect authenticated users who lack 2FA setup or verification.
    Returns a Flask response or None.
    """
    from flask import redirect

    if not is_global_2fa_required():
        return None
    try:
        if current_app.config.get("TESTING"):
            return None
    except RuntimeError:
        pass

    endpoint = request.endpoint or ""

    # Mid-login forced setup (not yet login_user)
    if session.get("2fa_setup_required") and session.get("2fa_user_id"):
        if endpoint.startswith("two_factor.") or endpoint.startswith("auth."):
            return None
        return redirect(url_for("two_factor.setup_2fa"))

    if session.get("2fa_required") and session.get("2fa_user_id"):
        if endpoint.startswith("two_factor.") or endpoint == "auth.login":
            return None
        return redirect(url_for("two_factor.verify_2fa"))

    if not current_user.is_authenticated:
        return None

    if _endpoint_exempt(endpoint):
        return None

    if user_needs_2fa_setup(current_user):
        session["2fa_user_id"] = current_user.id
        session["2fa_setup_required"] = True
        return redirect(url_for("two_factor.setup_2fa"))

    if user_needs_2fa_verify(current_user):
        session["2fa_user_id"] = current_user.id
        session["2fa_required"] = True
        return redirect(url_for("two_factor.verify_2fa"))

    return None


def apply_2fa_policy_to_new_user(user) -> None:
    """Set flags on newly created users when global 2FA is mandatory."""
    if is_global_2fa_required():
        user.two_factor_required = True
