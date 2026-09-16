"""
Sync OpsTask deadlines and Meetings to Google Calendar.

OpsTask: event on assignee primary calendar (deadline + optional second event for reminder_at).
        Calendar notifications use the API `reminders` field (popup/email, minutes before event start).
        Standalone Google "Reminders" / Keep are separate products; this app uses Calendar events only.
Meeting: one event on an organizer's calendar (creator or first participant with OAuth)
         with attendees = selected team + client/lead emails so everyone gets the invite.
         New meetings request a Google Meet (conferenceData); the join URL is copied into meeting.notes.

Google Tasks (default list) for ops tasks is implemented in google_tasks_service.py using
the same user refresh token and OAuth client.

Requires per-user OAuth (refresh token on User). Configure in .env:
  GOOGLE_CALENDAR_CLIENT_ID
  GOOGLE_CALENDAR_CLIENT_SECRET

Google Cloud Console: OAuth consent + Calendar API + Tasks API enabled; redirect URI must match
  {public_url}/settings/google-calendar/oauth2callback

Naive datetimes from forms/DB are interpreted as the app's local wall clock (Flask
config TIMEZONE, default Asia/Kolkata), then converted to UTC for Google — not as UTC already.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from flask import current_app, has_request_context, url_for
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from extensions import db
from models import Meeting, MeetingParticipant, OpsTask, User

logger = logging.getLogger(__name__)

CALENDAR_EVENTS_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
# Single OAuth flow: Calendar events + Google Tasks (assignee default task list).
GOOGLE_USER_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
]
SCOPES = GOOGLE_USER_OAUTH_SCOPES

# Google Calendar API: event.reminders.overrides use "minutes" before **event start** only
# (no "before end"). Keep the Task deadline block's start at the due moment so these match
# "N minutes before deadline".
_GCAL_SYNC_REMINDERS: Dict[str, Any] = {
    "useDefault": False,
    "overrides": [
        {"method": "popup", "minutes": 10},
        {"method": "popup", "minutes": 60},
        {"method": "email", "minutes": 24 * 60},
    ],
}

# Google Calendar palette (calendarId primary) — distinguishes Task / Meeting / Reminder visually
GCAL_COLOR_TASK = "10"  # basil / green
GCAL_COLOR_MEETING = "6"  # tangerine / orange
GCAL_COLOR_REMINDER = "5"  # banana / yellow

# OpsTask reminder_at: event starts at the chosen reminder time; notify at that instant.
_GCAL_REMINDER_ONLY_REMINDERS: Dict[str, Any] = {
    "useDefault": False,
    "overrides": [
        {"method": "popup", "minutes": 0},
        {"method": "email", "minutes": 0},
    ],
}


def is_oauth_configured() -> bool:
    if not has_request_context():
        return False
    cid = (current_app.config.get("GOOGLE_CALENDAR_CLIENT_ID") or "").strip()
    csec = (current_app.config.get("GOOGLE_CALENDAR_CLIENT_SECRET") or "").strip()
    return bool(cid and csec)


def quick_add_google_calendar_url(task: OpsTask, details_url: str) -> str:
    """Browser URL to pre-fill Google Calendar (no API); dates encoded as UTC for Google."""
    if not task or not task.deadline:
        return ""
    end = task.deadline + timedelta(minutes=30)
    start = task.deadline
    fmt = "%Y%m%dT%H%M%SZ"
    dates = f"{_fmt_google_quick_add_utc(start)}/{_fmt_google_quick_add_utc(end)}"
    title = quote(f"Task · {task.name or 'Ops task'}")
    body = quote(
        (task.notes or "").strip()[:1800]
        + ("\n\n" if task.notes else "")
        + (details_url or "")
    )
    return (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={title}&dates={dates}&details={body}"
    )


def get_google_user_oauth_access_token(user: User) -> Optional[str]:
    """Access token for Calendar + Tasks APIs (scopes in GOOGLE_USER_OAUTH_SCOPES)."""
    rt = getattr(user, "google_calendar_refresh_token", None) or None
    if not rt:
        return None
    cid = (current_app.config.get("GOOGLE_CALENDAR_CLIENT_ID") or "").strip()
    csec = (current_app.config.get("GOOGLE_CALENDAR_CLIENT_SECRET") or "").strip()
    if not cid or not csec:
        return None
    creds = Credentials(
        token=None,
        refresh_token=rt,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cid,
        client_secret=csec,
        scopes=GOOGLE_USER_OAUTH_SCOPES,
    )
    creds.refresh(Request())
    return creds.token


def _access_token(user: User) -> Optional[str]:
    return get_google_user_oauth_access_token(user)


def _default_app_tz():
    import pytz

    return pytz.timezone("Asia/Kolkata")


def _naive_as_app_local_to_utc(dt: datetime) -> datetime:
    """
    Interpret naive datetimes as app-local (TIMEZONE). Aware datetimes → UTC as-is.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    import pytz

    app_tz = _default_app_tz()
    if has_request_context():
        cfg_tz = current_app.config.get("TIMEZONE")
        if cfg_tz is not None:
            if isinstance(cfg_tz, str):
                app_tz = pytz.timezone(cfg_tz)
            else:
                app_tz = cfg_tz
    if isinstance(app_tz, str):
        app_tz = pytz.timezone(app_tz)
    return app_tz.localize(dt).astimezone(timezone.utc)


def _rfc3339_for_google(dt: datetime) -> str:
    return _naive_as_app_local_to_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_google_quick_add_utc(dt: datetime) -> str:
    return _naive_as_app_local_to_utc(dt).strftime("%Y%m%dT%H%M%SZ")


def _event_payload(task: OpsTask, task_public_url: str) -> Dict[str, Any]:
    # start = due time so Calendar reminders (minutes before start) align with the deadline.
    start = task.deadline
    end = start + timedelta(minutes=30)
    desc_parts = []
    if task.notes:
        desc_parts.append(task.notes.strip())
    if task_public_url:
        desc_parts.append(f"Open in Inertia: {task_public_url}")
    return {
        "summary": f"Task · {task.name}"[:1024],
        "description": "\n\n".join(desc_parts)[:8000] if desc_parts else "",
        "start": {"dateTime": _rfc3339_for_google(start), "timeZone": "UTC"},
        "end": {"dateTime": _rfc3339_for_google(end), "timeZone": "UTC"},
        "colorId": GCAL_COLOR_TASK,
        "extendedProperties": {"private": {"inertiaKind": "task"}},
        "reminders": dict(_GCAL_SYNC_REMINDERS),
    }


def _task_reminder_event_payload(task: OpsTask, task_public_url: str) -> Dict[str, Any]:
    """Separate short calendar entry for OpsTask reminder_at (distinct from deadline Task entry)."""
    start = task.reminder_at
    end = start + timedelta(minutes=15)
    desc_parts = ["Inertia ops task reminder (not the deadline block)."]
    if task.notes:
        desc_parts.append(task.notes.strip()[:2000])
    if task_public_url:
        desc_parts.append(f"Open in Inertia: {task_public_url}")
    return {
        "summary": f"Reminder · {task.name}"[:1024],
        "description": "\n\n".join(desc_parts)[:8000],
        "start": {"dateTime": _rfc3339_for_google(start), "timeZone": "UTC"},
        "end": {"dateTime": _rfc3339_for_google(end), "timeZone": "UTC"},
        "colorId": GCAL_COLOR_REMINDER,
        "extendedProperties": {"private": {"inertiaKind": "reminder"}},
        "reminders": dict(_GCAL_REMINDER_ONLY_REMINDERS),
    }


def _task_view_url(task_id: int) -> str:
    try:
        return url_for("tasks.view_task", task_id=task_id, _external=True)
    except Exception:
        return ""


def _delete_remote_event(
    user: User, event_id: str, *, send_updates: Optional[str] = None
) -> None:
    token = _access_token(user)
    if not token or not event_id:
        return
    params = {}
    if send_updates:
        params["sendUpdates"] = send_updates
    r = requests.delete(
        f"{CALENDAR_EVENTS_BASE}/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or None,
        timeout=30,
    )
    if r.status_code not in (200, 204, 404):
        logger.warning(
            "Google Calendar delete failed: status=%s body=%s",
            r.status_code,
            r.text[:500],
        )


def _clear_task_calendar_fields(task: OpsTask) -> None:
    task.google_calendar_event_id = None
    task.google_calendar_reminder_event_id = None
    task.google_calendar_sync_user_id = None


def disconnect_user_google_calendar(user: User) -> None:
    """Remove stored token and delete linked events from this user's primary calendar."""
    if not user:
        return
    tasks = (
        OpsTask.query.filter_by(google_calendar_sync_user_id=user.id)
        .filter(
            or_(
                OpsTask.google_calendar_event_id.isnot(None),
                OpsTask.google_calendar_reminder_event_id.isnot(None),
            )
        )
        .all()
    )
    for t in tasks:
        try:
            rid = getattr(t, "google_calendar_reminder_event_id", None)
            if rid:
                _delete_remote_event(user, rid)
            if t.google_calendar_event_id:
                _delete_remote_event(user, t.google_calendar_event_id)
        except Exception:
            logger.exception("Calendar delete failed for task %s on disconnect", t.id)
        _clear_task_calendar_fields(t)
    from services.google_tasks_service import (
        clear_ops_task_google_tasks_fields,
        delete_remote_google_task,
    )

    gtasks = (
        OpsTask.query.filter_by(google_tasks_sync_user_id=user.id)
        .filter(OpsTask.google_tasks_task_id.isnot(None))
        .all()
    )
    for t in gtasks:
        try:
            delete_remote_google_task(user, t.google_tasks_task_id)
        except Exception:
            logger.exception("Google Tasks delete failed for task %s on disconnect", t.id)
        clear_ops_task_google_tasks_fields(t)
    hosted_meetings = (
        Meeting.query.filter_by(google_calendar_organizer_user_id=user.id)
        .filter(Meeting.google_calendar_event_id.isnot(None))
        .all()
    )
    for m in hosted_meetings:
        try:
            _delete_remote_event(user, m.google_calendar_event_id, send_updates="all")
        except Exception:
            logger.exception("Calendar delete failed for meeting %s on disconnect", m.id)
        m.google_calendar_event_id = None
        m.google_calendar_organizer_user_id = None
    user.google_calendar_refresh_token = None
    db.session.commit()


def _upsert_task_reminder_event(task: OpsTask, assignee: User, headers: Dict[str, str]) -> None:
    """Create/update/delete the separate Reminder · calendar entry for reminder_at."""
    rid = getattr(task, "google_calendar_reminder_event_id", None)
    if task.status in ("completed", "cancelled") or not task.reminder_at:
        if rid:
            _delete_remote_event(assignee, rid)
        task.google_calendar_reminder_event_id = None
        return
    if _naive_as_app_local_to_utc(task.reminder_at) <= datetime.now(timezone.utc):
        if rid:
            _delete_remote_event(assignee, rid)
        task.google_calendar_reminder_event_id = None
        return

    public_url = _task_view_url(task.id)
    payload = _task_reminder_event_payload(task, public_url)

    if rid:
        r = requests.patch(
            f"{CALENDAR_EVENTS_BASE}/{rid}",
            json=payload,
            headers=headers,
            timeout=30,
        )
        if r.status_code == 404:
            task.google_calendar_reminder_event_id = None
            r = requests.post(
                CALENDAR_EVENTS_BASE,
                json=payload,
                headers=headers,
                timeout=30,
            )
            if r.status_code not in (200, 201):
                logger.warning(
                    "Google Calendar POST reminder failed task=%s status=%s body=%s",
                    task.id,
                    r.status_code,
                    r.text[:500],
                )
                return
            data = r.json()
            eid = data.get("id")
            if eid:
                task.google_calendar_reminder_event_id = eid
            return
        if r.status_code not in (200,):
            logger.warning(
                "Google Calendar PATCH reminder failed task=%s status=%s body=%s",
                task.id,
                r.status_code,
                r.text[:500],
            )
        return

    r = requests.post(
        CALENDAR_EVENTS_BASE,
        json=payload,
        headers=headers,
        timeout=30,
    )
    if r.status_code not in (200, 201):
        logger.warning(
            "Google Calendar POST reminder failed task=%s status=%s body=%s",
            task.id,
            r.status_code,
            r.text[:500],
        )
        return
    data = r.json()
    eid = data.get("id")
    if eid:
        task.google_calendar_reminder_event_id = eid


def sync_ops_task_google_calendar(task_id: int) -> None:
    """
    Create/update/delete Google Calendar event on the assignee's primary calendar.
    Safe to call after commit; logs and returns on misconfiguration or API errors.
    """
    if not has_request_context() or not is_oauth_configured():
        return

    task = OpsTask.query.get(task_id)
    if not task:
        return

    if task.status in ("completed", "cancelled"):
        if task.google_calendar_sync_user_id:
            owner = User.query.get(task.google_calendar_sync_user_id)
            if owner:
                rid = getattr(task, "google_calendar_reminder_event_id", None)
                if rid:
                    try:
                        _delete_remote_event(owner, rid)
                    except Exception:
                        logger.exception(
                            "Calendar delete reminder failed for closed task %s", task_id
                        )
                if task.google_calendar_event_id:
                    try:
                        _delete_remote_event(owner, task.google_calendar_event_id)
                    except Exception:
                        logger.exception(
                            "Calendar delete failed for closed task %s", task_id
                        )
        _clear_task_calendar_fields(task)
        db.session.commit()
        return

    if not task.assigned_to:
        if task.google_calendar_sync_user_id:
            owner = User.query.get(task.google_calendar_sync_user_id)
            if owner:
                rid = getattr(task, "google_calendar_reminder_event_id", None)
                if rid:
                    try:
                        _delete_remote_event(owner, rid)
                    except Exception:
                        logger.exception(
                            "Calendar delete reminder failed for unassigned task %s",
                            task_id,
                        )
                if task.google_calendar_event_id:
                    try:
                        _delete_remote_event(owner, task.google_calendar_event_id)
                    except Exception:
                        logger.exception(
                            "Calendar delete failed for unassigned task %s", task_id
                        )
        _clear_task_calendar_fields(task)
        db.session.commit()
        return

    assignee = User.query.get(task.assigned_to)
    if not assignee:
        return

    assignee_token = None
    if assignee.google_calendar_refresh_token:
        try:
            assignee_token = _access_token(assignee)
        except Exception:
            logger.exception(
                "Google Calendar OAuth refresh failed for user %s", assignee.id
            )
            return

    # Remove stale remote event if it lived on another user's calendar
    if (
        task.google_calendar_event_id
        and task.google_calendar_sync_user_id
        and task.google_calendar_sync_user_id != assignee.id
    ):
        prev = User.query.get(task.google_calendar_sync_user_id)
        if prev:
            try:
                rid = getattr(task, "google_calendar_reminder_event_id", None)
                if rid:
                    _delete_remote_event(prev, rid)
                if task.google_calendar_event_id:
                    _delete_remote_event(prev, task.google_calendar_event_id)
            except Exception:
                logger.exception("Calendar delete failed when reassigning task %s", task_id)
        _clear_task_calendar_fields(task)
        db.session.commit()
        task = OpsTask.query.get(task_id)

    if not assignee_token:
        if task.google_calendar_event_id and task.google_calendar_sync_user_id == assignee.id:
            _clear_task_calendar_fields(task)
            db.session.commit()
        return

    public_url = _task_view_url(task.id)
    payload = _event_payload(task, public_url)
    headers = {
        "Authorization": f"Bearer {assignee_token}",
        "Content-Type": "application/json",
    }

    try:
        if task.google_calendar_event_id and task.google_calendar_sync_user_id == assignee.id:
            r = requests.patch(
                f"{CALENDAR_EVENTS_BASE}/{task.google_calendar_event_id}",
                json=payload,
                headers=headers,
                timeout=30,
            )
            if r.status_code == 404:
                task.google_calendar_event_id = None
                task.google_calendar_sync_user_id = None
                db.session.commit()
                sync_ops_task_google_calendar(task_id)
                return
            if r.status_code not in (200,):
                logger.warning(
                    "Google Calendar PATCH failed task=%s status=%s body=%s",
                    task_id,
                    r.status_code,
                    r.text[:500],
                )
                return
        else:
            r = requests.post(
                CALENDAR_EVENTS_BASE,
                json=payload,
                headers=headers,
                timeout=30,
            )
            if r.status_code not in (200, 201):
                logger.warning(
                    "Google Calendar POST failed task=%s status=%s body=%s",
                    task_id,
                    r.status_code,
                    r.text[:500],
                )
                return
            data = r.json()
            eid = data.get("id")
            if not eid:
                logger.warning("Google Calendar POST missing id for task=%s", task_id)
                return
            task.google_calendar_event_id = eid
            task.google_calendar_sync_user_id = assignee.id
        _upsert_task_reminder_event(task, assignee, headers)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Google Calendar sync failed for task %s", task_id)


def quick_add_google_calendar_url_for_meeting(meeting: Meeting, details_url: str) -> str:
    """Browser URL to pre-fill Google Calendar (no API); dates encoded as UTC for Google."""
    if not meeting or not meeting.meeting_date:
        return ""
    dur = int(meeting.duration) if meeting.duration else 60
    if dur < 15:
        dur = 15
    start = meeting.meeting_date
    end = start + timedelta(minutes=dur)
    fmt = "%Y%m%dT%H%M%SZ"
    dates = f"{_fmt_google_quick_add_utc(start)}/{_fmt_google_quick_add_utc(end)}"
    title = quote(f"Meeting · {meeting.title or 'Meeting'}")
    parts = []
    if meeting.description:
        parts.append(meeting.description.strip())
    if meeting.notes:
        parts.append(meeting.notes.strip())
    if details_url:
        parts.append(details_url)
    body = quote("\n\n".join(parts)[:1800])
    return (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={title}&dates={dates}&details={body}"
    )


def _meeting_view_url(meeting_id: int) -> str:
    try:
        return url_for("meetings.view_meeting", id=meeting_id, _external=True)
    except Exception:
        return ""


def _event_calendar_has_meet(ev: Dict[str, Any]) -> bool:
    if (ev.get("hangoutLink") or "").strip():
        return True
    for ep in (ev.get("conferenceData") or {}).get("entryPoints") or []:
        if ep.get("entryPointType") == "video" and (ep.get("uri") or "").strip():
            return True
    return False


def _extract_meet_url_from_event(ev: Dict[str, Any]) -> Optional[str]:
    link = (ev.get("hangoutLink") or "").strip()
    if link:
        return link
    for ep in (ev.get("conferenceData") or {}).get("entryPoints") or []:
        if ep.get("entryPointType") == "video":
            uri = (ep.get("uri") or "").strip()
            if uri:
                return uri
    return None


def _merge_google_meet_into_notes(notes: Optional[str], meet_url: str) -> str:
    """Replace prior Meet lines / meet.google.com URLs, then append a single Google Meet line."""
    meet_url = (meet_url or "").strip()
    if not meet_url:
        return (notes or "").strip()
    lines = (notes or "").splitlines()
    kept: List[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("google meet:"):
            continue
        if "meet.google.com" in low:
            continue
        kept.append(ln.rstrip())
    body = "\n".join(kept).rstrip()
    block = f"Google Meet: {meet_url}"
    if body:
        return f"{body}\n\n{block}"
    return block


def _collect_meeting_attendee_emails(meeting: Meeting, organizer: User) -> List[Dict[str, str]]:
    """Google Calendar attendees (excluding organizer duplicate)."""
    emails_lower: set = set()
    org_email = (organizer.email or "").strip().lower()
    for mp in meeting.participants_assoc:
        if mp.user and mp.user.email:
            e = mp.user.email.strip().lower()
            if e:
                emails_lower.add(e)
    if meeting.client and meeting.client.email:
        e = meeting.client.email.strip().lower()
        if e:
            emails_lower.add(e)
    if meeting.lead and meeting.lead.email:
        e = meeting.lead.email.strip().lower()
        if e:
            emails_lower.add(e)
    out: List[Dict[str, str]] = []
    for e in emails_lower:
        if e and e != org_email:
            out.append({"email": e})
    return out[:200]


def _meeting_event_payload(
    meeting: Meeting, organizer: User, *, request_meet: bool = False
) -> Dict[str, Any]:
    dur = int(meeting.duration) if meeting.duration else 60
    if dur < 15:
        dur = 15
    start = meeting.meeting_date
    end = start + timedelta(minutes=dur)
    desc_parts: List[str] = []
    if meeting.description:
        desc_parts.append(meeting.description.strip())
    if meeting.notes:
        desc_parts.append("Notes:\n" + meeting.notes.strip())
    view_url = _meeting_view_url(meeting.id)
    if view_url:
        desc_parts.append(f"Open in Inertia: {view_url}")
    attendees = _collect_meeting_attendee_emails(meeting, organizer)
    body: Dict[str, Any] = {
        "summary": f"Meeting · {meeting.title}"[:1024],
        "description": "\n\n".join(desc_parts)[:8000] if desc_parts else "",
        "start": {"dateTime": _rfc3339_for_google(start), "timeZone": "UTC"},
        "end": {"dateTime": _rfc3339_for_google(end), "timeZone": "UTC"},
        "attendees": attendees,
        "guestsCanModify": False,
        "guestsCanInviteOthers": False,
        "colorId": GCAL_COLOR_MEETING,
        "extendedProperties": {"private": {"inertiaKind": "meeting"}},
        "reminders": dict(_GCAL_SYNC_REMINDERS),
    }
    if request_meet:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": f"inertia-mtg-{meeting.id}-{uuid.uuid4().hex[:16]}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
    return body


def _meeting_calendar_organizer(meeting: Meeting) -> Optional[User]:
    if meeting.google_calendar_organizer_user_id:
        u = User.query.get(meeting.google_calendar_organizer_user_id)
        if u and getattr(u, "google_calendar_refresh_token", None):
            return u
    if meeting.created_by:
        u = User.query.get(meeting.created_by)
        if u and getattr(u, "google_calendar_refresh_token", None):
            return u
    for mp in meeting.participants_assoc:
        if mp.user and getattr(mp.user, "google_calendar_refresh_token", None):
            return mp.user
    return None


def remove_meeting_google_calendar_event(meeting_id: int) -> None:
    """Delete remote calendar event when a meeting row is removed."""
    if not has_request_context() or not is_oauth_configured():
        return
    m = Meeting.query.get(meeting_id)
    if not m:
        return
    if m.google_calendar_event_id and m.google_calendar_organizer_user_id:
        org = User.query.get(m.google_calendar_organizer_user_id)
        if org:
            try:
                _delete_remote_event(
                    org, m.google_calendar_event_id, send_updates="all"
                )
            except Exception:
                logger.exception(
                    "Google Calendar delete failed for deleted meeting %s", meeting_id
                )


def sync_meeting_google_calendar(meeting_id: int) -> None:
    """
    Create/update/delete a single Google Calendar event with attendees.
    Organizer is meeting host (created_by) if they have OAuth, else first participant with OAuth.
    """
    if not has_request_context() or not is_oauth_configured():
        return

    meeting = (
        Meeting.query.options(
            selectinload(Meeting.participants_assoc).selectinload(MeetingParticipant.user),
            selectinload(Meeting.client),
            selectinload(Meeting.lead),
        )
        .filter(Meeting.id == meeting_id)
        .first()
    )
    if not meeting:
        return

    if meeting.status in ("cancelled", "completed"):
        if meeting.google_calendar_event_id and meeting.google_calendar_organizer_user_id:
            org = User.query.get(meeting.google_calendar_organizer_user_id)
            if org:
                try:
                    _delete_remote_event(
                        org, meeting.google_calendar_event_id, send_updates="all"
                    )
                except Exception:
                    logger.exception(
                        "Google Calendar delete failed for closed meeting %s", meeting_id
                    )
        meeting.google_calendar_event_id = None
        meeting.google_calendar_organizer_user_id = None
        db.session.commit()
        return

    organizer = _meeting_calendar_organizer(meeting)
    if not organizer:
        if meeting.google_calendar_event_id and meeting.google_calendar_organizer_user_id:
            prev = User.query.get(meeting.google_calendar_organizer_user_id)
            if prev:
                try:
                    _delete_remote_event(
                        prev, meeting.google_calendar_event_id, send_updates="all"
                    )
                except Exception:
                    logger.exception(
                        "Google Calendar delete failed clearing meeting %s", meeting_id
                    )
            meeting.google_calendar_event_id = None
            meeting.google_calendar_organizer_user_id = None
            db.session.commit()
        return

    if (
        meeting.google_calendar_event_id
        and meeting.google_calendar_organizer_user_id
        and meeting.google_calendar_organizer_user_id != organizer.id
    ):
        prev = User.query.get(meeting.google_calendar_organizer_user_id)
        if prev:
            try:
                _delete_remote_event(
                    prev, meeting.google_calendar_event_id, send_updates="all"
                )
            except Exception:
                logger.exception(
                    "Google Calendar delete failed when changing host meeting %s",
                    meeting_id,
                )
        meeting.google_calendar_event_id = None
        meeting.google_calendar_organizer_user_id = organizer.id
        db.session.commit()
        meeting = Meeting.query.get(meeting_id)

    if not meeting.google_calendar_organizer_user_id:
        meeting.google_calendar_organizer_user_id = organizer.id
        db.session.commit()

    try:
        token = _access_token(organizer)
    except Exception:
        logger.exception(
            "Google Calendar OAuth refresh failed for meeting %s", meeting_id
        )
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        request_meet = not meeting.google_calendar_event_id
        if meeting.google_calendar_event_id:
            gr = requests.get(
                f"{CALENDAR_EVENTS_BASE}/{meeting.google_calendar_event_id}",
                headers=headers,
                timeout=30,
            )
            if gr.status_code == 200 and not _event_calendar_has_meet(gr.json()):
                request_meet = True

        payload = _meeting_event_payload(meeting, organizer, request_meet=request_meet)
        params: Dict[str, Any] = {"sendUpdates": "all"}
        if request_meet:
            params["conferenceDataVersion"] = 1

        had_remote_event = bool(meeting.google_calendar_event_id)
        if had_remote_event:
            r = requests.patch(
                f"{CALENDAR_EVENTS_BASE}/{meeting.google_calendar_event_id}",
                json=payload,
                headers=headers,
                params=params,
                timeout=30,
            )
            if r.status_code == 404:
                meeting.google_calendar_event_id = None
                db.session.commit()
                sync_meeting_google_calendar(meeting_id)
                return
            if r.status_code not in (200,):
                logger.warning(
                    "Google Calendar PATCH failed meeting=%s status=%s body=%s",
                    meeting_id,
                    r.status_code,
                    r.text[:500],
                )
                return
        else:
            r = requests.post(
                CALENDAR_EVENTS_BASE,
                json=payload,
                headers=headers,
                params=params,
                timeout=30,
            )
            if r.status_code not in (200, 201):
                logger.warning(
                    "Google Calendar POST failed meeting=%s status=%s body=%s",
                    meeting_id,
                    r.status_code,
                    r.text[:500],
                )
                return

        data = r.json()
        if not had_remote_event:
            eid = data.get("id")
            if not eid:
                logger.warning("Google Calendar POST missing id for meeting=%s", meeting_id)
                return
            meeting.google_calendar_event_id = eid
            meeting.google_calendar_organizer_user_id = organizer.id
        meet_url = _extract_meet_url_from_event(data)
        if meet_url:
            new_notes = _merge_google_meet_into_notes(meeting.notes, meet_url)
            if new_notes != (meeting.notes or "").strip():
                meeting.notes = new_notes
                payload2 = _meeting_event_payload(meeting, organizer, request_meet=False)
                r2 = requests.patch(
                    f"{CALENDAR_EVENTS_BASE}/{meeting.google_calendar_event_id}",
                    json=payload2,
                    headers=headers,
                    params={"sendUpdates": "all"},
                    timeout=30,
                )
                if r2.status_code not in (200,):
                    logger.warning(
                        "Google Calendar PATCH (notes after Meet) failed meeting=%s status=%s body=%s",
                        meeting_id,
                        r2.status_code,
                        r2.text[:500],
                    )

        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Google Calendar sync failed for meeting %s", meeting_id)
