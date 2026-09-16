"""Unit tests for issue lifecycle service commands."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class TestIssueLifecycleService(unittest.TestCase):
    def test_complete_task_and_sync_calls_issue_resolution_for_issue_backed_task(self):
        from services.issue_lifecycle_service import complete_task_and_sync

        issue = SimpleNamespace(id=10, status="open")
        task = SimpleNamespace(
            id=5,
            status="pending",
            data_integrity_issue_id=10,
            data_integrity_issue=issue,
            completed_at=None,
            completed_by=None,
            reminder_at=None,
            reminder_sent=False,
            updated_at=None,
        )

        with patch("services.issue_lifecycle_service.OpsTask") as MockOpsTask, patch(
            "services.issue_lifecycle_service.resolve_issue_and_sync",
            return_value={
                "issue_resolved": 1,
                "linked_tasks_closed": 2,
                "linked_alerts_resolved": 1,
                "client_stale_alerts_resolved": 0,
            },
        ) as mock_resolve, patch(
            "services.issue_lifecycle_service.db.session.commit"
        ):
            MockOpsTask.query.get.return_value = task
            out = complete_task_and_sync(task.id, actor_user_id=99, commit=True)

        self.assertEqual(out["task_closed"], 1)
        self.assertEqual(out["issue_resolved"], 1)
        self.assertEqual(out["linked_tasks_closed"], 2)
        self.assertEqual(out["linked_alerts_resolved"], 1)
        mock_resolve.assert_called_once()

    def test_complete_task_and_sync_is_idempotent_for_already_closed_task(self):
        from services.issue_lifecycle_service import complete_task_and_sync

        task = SimpleNamespace(
            id=7,
            status="completed",
            data_integrity_issue_id=None,
            data_integrity_issue=None,
            completed_at=None,
            completed_by=None,
            reminder_at=None,
            reminder_sent=False,
            updated_at=None,
        )

        with patch("services.issue_lifecycle_service.OpsTask") as MockOpsTask, patch(
            "services.issue_lifecycle_service.db.session.commit"
        ):
            MockOpsTask.query.get.return_value = task
            out = complete_task_and_sync(task.id, actor_user_id=99, commit=True)

        self.assertEqual(out["task_closed"], 0)
        self.assertEqual(out["issue_resolved"], 0)

    def test_resolve_issue_and_sync_marks_issue_and_merges_alert_stats(self):
        from services.issue_lifecycle_service import resolve_issue_and_sync

        issue = SimpleNamespace(
            id=11,
            status="open",
            resolution_type=None,
            resolved_at=None,
            resolved_by=None,
            resolution_notes=None,
            client_id=1,
            alert_id=2,
        )

        with patch(
            "services.issue_lifecycle_service.close_linked_open_tasks", return_value=3
        ), patch(
            "services.issue_lifecycle_service.sync_alert_lifecycle_for_issue",
            return_value={"linked_alerts_resolved": 1, "client_stale_alerts_resolved": 2},
        ), patch("services.issue_lifecycle_service.db.session.commit"):
            out = resolve_issue_and_sync(
                issue,
                resolution_type="fixed",
                actor_user_id=42,
                notes="done",
                source="unit_test",
                commit=True,
            )

        self.assertEqual(issue.status, "resolved")
        self.assertEqual(issue.resolution_type, "fixed")
        self.assertEqual(issue.resolved_by, 42)
        self.assertIn("unit_test", issue.resolution_notes)
        self.assertEqual(out["issue_resolved"], 1)
        self.assertEqual(out["linked_tasks_closed"], 3)
        self.assertEqual(out["linked_alerts_resolved"], 1)
        self.assertEqual(out["client_stale_alerts_resolved"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
