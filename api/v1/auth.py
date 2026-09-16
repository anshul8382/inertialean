"""
Mobile / API authentication — JWT Bearer tokens + mandatory 2FA verify step.
"""
from flask import Blueprint, request
from models import User
from api.core.response import APIResponse
from services.jwt_service import issue_access_token, issue_2fa_pending_token, user_from_pending_2fa_token
from services.two_factor_service import TwoFactorService
from services.two_factor_enforcement import user_needs_2fa_setup

auth_api_bp = Blueprint("auth_api", __name__)


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": getattr(user, "name", None) or user.email,
        "is_admin": bool(getattr(user, "is_admin", False)),
        "is_advisor": bool(getattr(user, "is_advisor", False)),
        "is_manager": bool(getattr(user, "is_manager", False)),
        "two_factor_enabled": bool(getattr(user, "two_factor_enabled", False)),
    }


@auth_api_bp.route("/login", methods=["POST"])
def api_login():
    """
    JSON body: { "email", "password" }
    - If 2FA enabled: { requires_2fa: true, pending_token, user }
    - If setup required: 403 error_code setup_required (complete setup on web first)
  - Else (policy off only): access_token
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return APIResponse.error("email and password are required", status_code=400)

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return APIResponse.error("Invalid email or password", status_code=401)

    if not getattr(user, "is_active", True):
        return APIResponse.error("Account is inactive", status_code=403)

    if user_needs_2fa_setup(user):
        return APIResponse.error(
            "Two-factor authentication setup is required before API login. "
            "Sign in on the web app once to configure your authenticator.",
            status_code=403,
            error_code="2fa_setup_required",
        )

    if getattr(user, "two_factor_enabled", False):
        pending = issue_2fa_pending_token(user.id)
        if not pending:
            return APIResponse.error("JWT support not available (install PyJWT)", status_code=503)
        return APIResponse.success(
            data={
                "requires_2fa": True,
                "pending_token": pending,
                "token_type": "2fa_pending",
                "expires_in_minutes": 5,
                "user": _user_payload(user),
            },
            message="Enter TOTP code from authenticator app",
        )

    token = issue_access_token(user.id)
    if not token:
        return APIResponse.error("JWT support not available (install PyJWT)", status_code=503)

    try:
        from services.audit_service import log_audit_event

        log_audit_event("login_success", user_id=user.id, details={"channel": "api_jwt"})
    except Exception:
        pass

    return APIResponse.success(
        data={
            "access_token": token,
            "token_type": "Bearer",
            "expires_in_hours": 24,
            "user": _user_payload(user),
        },
        message="Login successful",
    )


@auth_api_bp.route("/verify-2fa", methods=["POST"])
def api_verify_2fa():
    """JSON: { pending_token, code } -> access_token"""
    data = request.get_json(silent=True) or {}
    pending = (data.get("pending_token") or "").strip()
    code = (data.get("code") or "").strip()

    if not pending or not code:
        return APIResponse.error("pending_token and code are required", status_code=400)

    user = user_from_pending_2fa_token(pending)
    if not user:
        return APIResponse.error("Invalid or expired pending_token", status_code=401, error_code="invalid_pending_token")

    if not user.two_factor_enabled:
        return APIResponse.error("2FA is not enabled for this account", status_code=403)

    result = TwoFactorService.verify_2fa(user, code)
    if not result.get("success"):
        return APIResponse.error(result.get("error", "Invalid code"), status_code=401, error_code="invalid_2fa_code")

    from extensions import db

    db.session.commit()

    token = issue_access_token(user.id)
    if not token:
        return APIResponse.error("JWT support not available", status_code=503)

    try:
        from services.audit_service import log_audit_event

        log_audit_event("login_success", user_id=user.id, details={"channel": "api_jwt_2fa"})
    except Exception:
        pass

    return APIResponse.success(
        data={
            "access_token": token,
            "token_type": "Bearer",
            "expires_in_hours": 24,
            "user": _user_payload(user),
        },
        message="Login successful",
    )


@auth_api_bp.route("/me", methods=["GET"])
def api_me():
    from flask_login import current_user

    if not current_user.is_authenticated:
        return APIResponse.error("Unauthorized", status_code=401)
    return APIResponse.success(data={"user": _user_payload(current_user)})
