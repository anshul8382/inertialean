from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from models import Meeting, Lead, Client, User, MeetingParticipant
from extensions import db
from datetime import datetime
from functools import wraps
from sqlalchemy.orm import selectinload
import logging

logger = logging.getLogger(__name__)
meetings = Blueprint('meetings', __name__)


def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            flash(f'An error occurred: {str(e)}', 'error')
            return redirect(url_for('main.dashboard'))
    return decorated_function


def _team_users():
    return User.query.filter_by(is_active=True).order_by(User.username).all()


def _parse_participant_ids():
    out = []
    for x in request.form.getlist('participant_ids'):
        try:
            v = int(x)
            if v > 0:
                out.append(v)
        except (TypeError, ValueError):
            pass
    seen = set()
    deduped = []
    for v in out:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped


def _set_meeting_participants(meeting, user_ids):
    MeetingParticipant.query.filter_by(meeting_id=meeting.id).delete(synchronize_session=False)
    for uid in user_ids:
        if User.query.get(uid):
            db.session.add(MeetingParticipant(meeting_id=meeting.id, user_id=uid))


def _sync_meeting_calendar(meeting_id):
    try:
        from services.google_calendar_service import sync_meeting_google_calendar

        sync_meeting_google_calendar(meeting_id)
    except Exception:
        logger.exception("Google Calendar sync failed for meeting %s", meeting_id)


@meetings.route('/meetings')
@login_required
@handle_errors
def list_meetings():
    meetings_list = Meeting.query.order_by(Meeting.meeting_date.desc()).all()
    return render_template('meetings/list.html', meetings=meetings_list)


@meetings.route('/meetings/new', methods=['GET', 'POST'])
@login_required
@handle_errors
def new_meeting():
    team_users = _team_users()
    leads = Lead.query.all()
    from access_control import get_accessible_clients_ordered
    clients = get_accessible_clients_ordered()

    lead_id = request.args.get('lead_id')
    client_id = request.args.get('client_id')
    selected_lead = Lead.query.get(int(lead_id)) if lead_id and str(lead_id).isdigit() else None
    selected_client = Client.query.get(int(client_id)) if client_id and str(client_id).isdigit() else None

    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        description = request.form.get('description')
        meeting_date_raw = request.form.get('meeting_date')
        duration_raw = request.form.get('duration')
        status = request.form.get('status', 'scheduled')
        notes = request.form.get('notes')
        meeting_type = request.form.get('meeting_type', 'client')
        lead_id_raw = (request.form.get('lead_id') or '').strip()
        client_id_raw = (request.form.get('client_id') or '').strip()

        lead_id_val = int(lead_id_raw) if lead_id_raw.isdigit() else None
        client_id_val = int(client_id_raw) if client_id_raw.isdigit() else None

        if meeting_type == 'lead' and lead_id_val:
            lead = Lead.query.get(lead_id_val)
            if not lead:
                flash('Invalid lead.', 'error')
                return render_template(
                    'meetings/new.html',
                    leads=leads,
                    clients=clients,
                    selected_lead=selected_lead,
                    selected_client=selected_client,
                    team_users=team_users,
                    selected_participant_ids=_parse_participant_ids() or [current_user.id],
                )
            if lead.client_id:
                client_id_val = lead.client_id
            else:
                client_id_val = None
        elif meeting_type == 'client':
            lead_id_val = None
            if not client_id_val:
                flash('Please select a client.', 'error')
                return render_template(
                    'meetings/new.html',
                    leads=leads,
                    clients=clients,
                    selected_lead=selected_lead,
                    selected_client=selected_client,
                    team_users=team_users,
                    selected_participant_ids=_parse_participant_ids() or [current_user.id],
                )
        else:
            flash('Please choose meeting with a client or a lead.', 'error')
            return render_template(
                'meetings/new.html',
                leads=leads,
                clients=clients,
                selected_lead=selected_lead,
                selected_client=selected_client,
                team_users=team_users,
                selected_participant_ids=_parse_participant_ids() or [current_user.id],
            )

        if not title or not meeting_date_raw:
            flash('Title and date/time are required.', 'error')
            return render_template(
                'meetings/new.html',
                leads=leads,
                clients=clients,
                selected_lead=selected_lead,
                selected_client=selected_client,
                team_users=team_users,
                selected_participant_ids=_parse_participant_ids() or [current_user.id],
            )

        try:
            meeting_date = datetime.strptime(meeting_date_raw, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash('Invalid date/time format.', 'error')
            return render_template(
                'meetings/new.html',
                leads=leads,
                clients=clients,
                selected_lead=selected_lead,
                selected_client=selected_client,
                team_users=team_users,
                selected_participant_ids=_parse_participant_ids() or [current_user.id],
            )

        try:
            duration = int(duration_raw) if duration_raw else 60
        except (TypeError, ValueError):
            duration = 60
        if duration < 15:
            duration = 15

        participant_ids = _parse_participant_ids()
        if not participant_ids:
            participant_ids = [current_user.id]

        meeting = Meeting(
            title=title,
            description=description,
            meeting_date=meeting_date,
            duration=duration,
            status=status,
            notes=notes,
            lead_id=lead_id_val,
            client_id=client_id_val,
            created_at=datetime.utcnow(),
            created_by=current_user.id,
        )

        db.session.add(meeting)
        db.session.flush()
        _set_meeting_participants(meeting, participant_ids)
        db.session.commit()
        _sync_meeting_calendar(meeting.id)

        flash('Meeting created successfully.', 'success')
        if lead_id_val:
            return redirect(url_for('leads.view_lead', lead_id=lead_id_val))
        if client_id_val:
            return redirect(url_for('clients.client_details', client_id=client_id_val))
        return redirect(url_for('meetings.list_meetings'))

    default_participants = [current_user.id]
    if selected_client and getattr(selected_client, 'advisor_id', None):
        if selected_client.advisor_id not in default_participants:
            default_participants.insert(0, selected_client.advisor_id)

    return render_template(
        'meetings/new.html',
        leads=leads,
        clients=clients,
        selected_lead=selected_lead,
        selected_client=selected_client,
        team_users=team_users,
        selected_participant_ids=default_participants,
    )


@meetings.route('/meetings/<int:id>')
@login_required
@handle_errors
def view_meeting(id):
    meeting = (
        Meeting.query.options(
            selectinload(Meeting.participants_assoc).selectinload(MeetingParticipant.user),
            selectinload(Meeting.client),
            selectinload(Meeting.lead),
        )
        .filter(Meeting.id == id)
        .first_or_404()
    )
    from services.google_calendar_service import (
        is_oauth_configured,
        quick_add_google_calendar_url_for_meeting,
    )

    public = url_for('meetings.view_meeting', id=meeting.id, _external=True)
    gcal_template_url = quick_add_google_calendar_url_for_meeting(meeting, public)
    return render_template(
        'meetings/view.html',
        meeting=meeting,
        gcal_template_url=gcal_template_url,
        gcal_oauth_configured=is_oauth_configured(),
    )


@meetings.route('/meetings/<int:id>/edit', methods=['GET', 'POST'])
@login_required
@handle_errors
def edit_meeting(id):
    meeting = (
        Meeting.query.options(
            selectinload(Meeting.participants_assoc).selectinload(MeetingParticipant.user)
        ).get_or_404(id)
    )
    team_users = _team_users()

    if request.method == 'POST':
        meeting.title = (request.form.get('title') or '').strip()
        meeting.description = request.form.get('description')
        meeting_date_raw = request.form.get('meeting_date')
        duration_raw = request.form.get('duration')
        meeting.status = request.form.get('status')
        meeting.notes = request.form.get('notes')

        if meeting_date_raw:
            try:
                meeting.meeting_date = datetime.strptime(meeting_date_raw, '%Y-%m-%dT%H:%M')
            except ValueError:
                flash('Invalid date/time format.', 'error')
                selected = [mp.user_id for mp in meeting.participants_assoc]
                return render_template(
                    'meetings/edit.html',
                    meeting=meeting,
                    team_users=team_users,
                    selected_participant_ids=selected,
                )

        try:
            meeting.duration = int(duration_raw) if duration_raw else 60
        except (TypeError, ValueError):
            meeting.duration = 60
        if meeting.duration < 15:
            meeting.duration = 15

        participant_ids = _parse_participant_ids()
        if not participant_ids:
            participant_ids = [current_user.id]
        _set_meeting_participants(meeting, participant_ids)

        db.session.commit()
        _sync_meeting_calendar(meeting.id)
        flash('Meeting updated successfully.', 'success')
        return redirect(url_for('meetings.view_meeting', id=meeting.id))

    selected = [mp.user_id for mp in meeting.participants_assoc]
    if not selected and meeting.created_by:
        selected = [meeting.created_by]
    return render_template(
        'meetings/edit.html',
        meeting=meeting,
        team_users=team_users,
        selected_participant_ids=selected,
    )


@meetings.route('/meetings/<int:id>/delete', methods=['POST'])
@login_required
@handle_errors
def delete_meeting(id):
    meeting = Meeting.query.get_or_404(id)
    mid = meeting.id
    try:
        from services.google_calendar_service import remove_meeting_google_calendar_event

        remove_meeting_google_calendar_event(mid)
    except Exception:
        logger.exception("Calendar cleanup before meeting delete failed for %s", mid)
    db.session.delete(meeting)
    db.session.commit()
    flash('Meeting deleted successfully.', 'success')
    return redirect(url_for('meetings.list_meetings'))
