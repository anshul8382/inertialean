"""HTTP UAT: reassign open data-integrity issues and keep manual lock across backfill."""

import pytest


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["2fa_verified"] = True


@pytest.fixture
def staff_user(app):
    from models import User

    user = User.query.filter_by(username="anshul").first()
    if user is None:
        user = User.query.filter_by(is_active=True).order_by(User.id.asc()).first()
    if user is None:
        pytest.skip("No active user in the database")
    return user


def test_client_issues_page_has_reassign_controls(client, app, staff_user):
    from models import Client, DataIntegrityIssue

    with app.app_context():
        issue = (
            DataIntegrityIssue.query.filter(
                DataIntegrityIssue.status.in_(["open", "baseline"])
            )
            .order_by(DataIntegrityIssue.id.desc())
            .first()
        )
        if issue is None:
            pytest.skip("No open issues in DB")
        client_id = issue.client_id
        assert Client.query.get(client_id) is not None

    app.config["WTF_CSRF_ENABLED"] = False
    _login(client, staff_user)
    resp = client.get(f"/data-integrity/client/{client_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "reassignOne" in html
    assert "Reassign Selected" in html
    assert "bulkAssignee" in html


def test_reassign_route_updates_issue_task_and_survives_backfill(client, app, staff_user):
    from agents.task_assignment_agent import TaskAssignmentAgent
    from extensions import db
    from models import Client, DataIntegrityIssue, OpsTask, User
    from services.task_assignment_service import reassign_issue

    app.config["WTF_CSRF_ENABLED"] = False
    _login(client, staff_user)

    with app.app_context():
        issue = (
            DataIntegrityIssue.query.filter(
                DataIntegrityIssue.status.in_(["open", "baseline"]),
                DataIntegrityIssue.client_id.isnot(None),
            )
            .order_by(DataIntegrityIssue.id.desc())
            .first()
        )
        if issue is None:
            pytest.skip("No open issues in DB")

        other = (
            User.query.filter(User.is_active.is_(True), User.id != issue.assigned_to)
            .order_by(User.id.asc())
            .first()
        )
        if other is None:
            pytest.skip("Need a second active user")

        issue_id = issue.id
        client_row = Client.query.get(issue.client_id)
        restore_to = (
            client_row.advisor_id
            if client_row and client_row.advisor_id
            else issue.assigned_to
        )

    resp = client.post(
        "/data-integrity/reassign",
        data={"issue_ids[]": str(issue_id), "assigned_to": str(other.id)},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Reassigned" in resp.data

    with app.app_context():
        issue = DataIntegrityIssue.query.get(issue_id)
        assert issue.assigned_to == other.id
        assert (issue.details or {}).get("manual_assignment") is True
        tasks = OpsTask.query.filter(
            OpsTask.data_integrity_issue_id == issue_id,
            OpsTask.status.in_(["pending", "in_progress", "snoozed"]),
        ).all()
        # OpsTask auto-create is gated (DI_CREATE_OPS_TASKS default off). When tasks
        # already exist they must follow the new assignee; when none exist, skip.
        if tasks:
            assert all(t.assigned_to == other.id for t in tasks)

        TaskAssignmentAgent().run_backfill()
        db.session.expire_all()
        issue = DataIntegrityIssue.query.get(issue_id)
        assert issue.assigned_to == other.id
        assert (issue.details or {}).get("manual_assignment") is True

        if restore_to:
            reassign_issue(issue, restore_to, by_user_id=staff_user.id)
            db.session.commit()
