"""Google Calendar OAuth for OpsTask deadline sync (assignee primary calendar)."""
import logging
import secrets

from flask import Blueprint, current_app, flash, redirect, request, session, url_for
from flask_login import login_required, current_user
from google_auth_oauthlib.flow import Flow

from extensions import db

logger = logging.getLogger(__name__)

google_calendar_bp = Blueprint("google_calendar", __name__)

from services.google_calendar_service import GOOGLE_USER_OAUTH_SCOPES

SCOPES = GOOGLE_USER_OAUTH_SCOPES


def _web_client_config(redirect_uri: str) -> dict:
    return {
        "web": {
            "client_id": current_app.config["GOOGLE_CALENDAR_CLIENT_ID"],
            "client_secret": current_app.config["GOOGLE_CALENDAR_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


@google_calendar_bp.route("/connect")
@login_required
def calendar_connect():
    cid = (current_app.config.get("GOOGLE_CALENDAR_CLIENT_ID") or "").strip()
    csec = (current_app.config.get("GOOGLE_CALENDAR_CLIENT_SECRET") or "").strip()
    if not cid or not csec:
        flash(
            "Google Calendar is not configured. Set GOOGLE_CALENDAR_CLIENT_ID and "
            "GOOGLE_CALENDAR_CLIENT_SECRET, and add the redirect URI in Google Cloud Console.",
            "warning",
        )
        return redirect(url_for("settings.index"))

    redirect_uri = url_for("google_calendar.calendar_oauth_callback", _external=True)
    state = secrets.token_urlsafe(32)
    session["gcal_oauth_state"] = state

    flow = Flow.from_client_config(
        _web_client_config(redirect_uri),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return redirect(authorization_url)


@google_calendar_bp.route("/oauth2callback")
@login_required
def calendar_oauth_callback():
    if request.args.get("state") != session.get("gcal_oauth_state"):
        flash("Invalid or expired OAuth state. Try connecting again.", "error")
        return redirect(url_for("settings.index"))
    session.pop("gcal_oauth_state", None)

    err = request.args.get("error")
    if err:
        flash(f"Google authorization was denied or failed: {err}", "error")
        return redirect(url_for("settings.index"))

    cid = (current_app.config.get("GOOGLE_CALENDAR_CLIENT_ID") or "").strip()
    csec = (current_app.config.get("GOOGLE_CALENDAR_CLIENT_SECRET") or "").strip()
    if not cid or not csec:
        flash("Google Calendar client is not configured.", "warning")
        return redirect(url_for("settings.index"))

    redirect_uri = url_for("google_calendar.calendar_oauth_callback", _external=True)
    try:
        flow = Flow.from_client_config(
            _web_client_config(redirect_uri),
            scopes=SCOPES,
            redirect_uri=redirect_uri,
        )
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        if not creds.refresh_token:
            flash(
                "Google did not return a refresh token. In Google Account → Security → "
                "Third-party access, remove this app and connect again (use prompt consent).",
                "warning",
            )
            return redirect(url_for("settings.index"))
        current_user.google_calendar_refresh_token = creds.refresh_token
        db.session.commit()
        flash(
            "Google connected. Ops tasks assigned to you sync to your primary Google Calendar "
            "(deadline) and to your default Google Tasks list.",
            "success",
        )
    except Exception as e:
        db.session.rollback()
        logger.exception("Google Calendar OAuth callback failed")
        flash(f"Could not complete Google connection: {e}", "error")

    return redirect(url_for("settings.index"))


@google_calendar_bp.route("/disconnect", methods=["POST"])
@login_required
def calendar_disconnect():
    try:
        from services.google_calendar_service import disconnect_user_google_calendar

        disconnect_user_google_calendar(current_user)
        flash(
            "Google disconnected; linked calendar events and Google Tasks created by Inertia "
            "were removed where possible.",
            "info",
        )
    except Exception as e:
        db.session.rollback()
        logger.exception("Google Calendar disconnect failed")
        flash(f"Could not disconnect: {e}", "error")
    return redirect(url_for("settings.index"))
