"""Task reminder service - sends in-app alerts and emails for task reminders."""
from datetime import datetime

from flask import current_app, has_app_context, render_template, url_for
from flask_mail import Message
from sqlalchemy import func
from werkzeug.routing import BuildError

from extensions import db, mail
import logging

logger = logging.getLogger(__name__)


def _task_view_external_url(task_id):
    """Build task URL outside a request (Airflow, cron)."""
    try:
        return url_for("tasks.view_task", task_id=task_id, _external=True)
    except (RuntimeError, BuildError):
        cfg = current_app.config
        scheme = cfg.get("PREFERRED_URL_SCHEME", "https")
        host = cfg.get("SERVER_NAME", "localhost")
        root = (cfg.get("APPLICATION_ROOT") or "").rstrip("/")
        base = f"{scheme}://{host}".rstrip("/")
        prefix = f"{base}{root}" if root else base
        return f"{prefix}/tasks/{task_id}"


def _run_process_task_reminders():
    """Must run inside Flask app_context."""
    from models import OpsTask
    from alert_system_models import Alert

    now = datetime.utcnow()
    tasks = (
        OpsTask.query.filter(
            OpsTask.reminder_at.isnot(None),
            OpsTask.reminder_at <= now,
            OpsTask.reminder_sent.is_(False),
            func.lower(OpsTask.status).in_(("pending", "in_progress")),
        )
        .all()
    )

    if not tasks:
        return 0

    count = 0
    for task in tasks:
        try:
            recipient_user = task.assignee or task.creator
            if not recipient_user or not recipient_user.email:
                logger.warning(
                    "Task %s has no assignee/creator with email, skipping reminder",
                    task.id,
                )
                task.reminder_sent = True
                db.session.commit()
                continue

            task_url = _task_view_external_url(task.id)
            alert = Alert(
                alert_type="ops_task",
                alert_subtype="reminder",
                severity="warning" if task.priority == "high" else "info",
                status="active",
                user_id=recipient_user.id,
                title=f"Task Reminder: {task.name}",
                description=(
                    f"Deadline: {task.deadline.strftime('%Y-%m-%d %H:%M')}. "
                    f"{task.notes or ''} View: {task_url}"
                ),
                sla_timeline=None,
                current_delay=None,
            )
            db.session.add(alert)

            html = render_template(
                "email/task_reminder.html",
                task_name=task.name,
                deadline=task.deadline.strftime("%Y-%m-%d %H:%M"),
                notes=task.notes or "",
                task_url=task_url,
            )
            msg = Message(
                subject=f"Task Reminder: {task.name}",
                recipients=[recipient_user.email],
                html=html,
                sender=current_app.config.get(
                    "MAIL_DEFAULT_SENDER", "noreply@inertia.com"
                ),
            )
            mail.send(msg)

            task.reminder_sent = True
            task.updated_at = now
            db.session.commit()
            count += 1
            logger.info("Sent reminder for task %s to %s", task.id, recipient_user.email)

        except Exception as e:
            logger.exception("Failed to send reminder for task %s: %s", task.id, e)
            db.session.rollback()
            continue

    return count


def process_task_reminders(app=None):
    """
    Process due task reminders: create in-app alerts and send emails.
    Uses existing app context when present (e.g. Airflow); otherwise ``app`` or ``create_app()``.
    """
    from main import create_app

    try:
        if has_app_context():
            return _run_process_task_reminders()
        if app is not None:
            with app.app_context():
                return _run_process_task_reminders()
        with create_app().app_context():
            return _run_process_task_reminders()
    except Exception as e:
        logger.exception("Task reminder processing failed: %s", e)
        return 0
