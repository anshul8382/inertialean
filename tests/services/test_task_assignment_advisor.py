"""Unit tests for issue assignee → client advisor policy."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class TestGetAssigneeForIssueAdvisor(unittest.TestCase):
    def test_integrity_issue_uses_client_advisor(self):
        from services.task_assignment_service import get_assignee_for_issue

        issue = SimpleNamespace(
            check_category="DUPLICATES",
            details={},
            client_id=137,
            client=None,
        )
        client = SimpleNamespace(id=137, advisor_id=42)
        with patch("services.task_assignment_service._get_rules", return_value=[]), patch(
            "services.task_assignment_service.Client"
        ) as MockClient:
            MockClient.query.get.return_value = client
            uid = get_assignee_for_issue(issue)
        self.assertEqual(uid, 42)

    def test_advisor_preferred_over_category_rule(self):
        from services.task_assignment_service import get_assignee_for_issue

        issue = SimpleNamespace(
            check_category="DUPLICATES",
            details={},
            client_id=10,
            client=SimpleNamespace(id=10, advisor_id=7),
        )
        rule = SimpleNamespace(
            rule_type="issue_category",
            match_value="DUPLICATES",
            assigned_user_id=99,
        )
        with patch("services.task_assignment_service._get_rules", return_value=[rule]):
            uid = get_assignee_for_issue(issue)
        self.assertEqual(uid, 7)

    def test_rec_exec_uses_workflow_stage_when_present(self):
        from services.task_assignment_service import get_assignee_for_issue

        issue = SimpleNamespace(
            check_category="RECOMMENDATION_EXECUTION",
            details={"workflow_id": 55},
            client_id=10,
            client=SimpleNamespace(id=10, advisor_id=7),
        )
        workflow = SimpleNamespace(current_stage="FUNDS")
        with patch("services.task_assignment_service.Workflow") as MockWf, patch(
            "services.task_assignment_service.get_assignee_for_workflow_stage",
            return_value=88,
        ) as mock_stage:
            MockWf.query.get.return_value = workflow
            uid = get_assignee_for_issue(issue)
        self.assertEqual(uid, 88)
        mock_stage.assert_called_once_with(workflow)


class TestGetAssigneeForReviewAdvisor(unittest.TestCase):
    def test_review_uses_client_advisor_over_status_rule(self):
        from services.task_assignment_service import get_assignee_for_review_status

        rw = SimpleNamespace(
            status="initiated",
            client_id=10,
            client=SimpleNamespace(id=10, advisor_id=7),
        )
        rule = SimpleNamespace(
            rule_type="review_status",
            match_value="initiated",
            assigned_user_id=99,
        )
        with patch("services.task_assignment_service._get_rules", return_value=[rule]):
            uid = get_assignee_for_review_status(rw)
        self.assertEqual(uid, 7)

    def test_review_falls_back_to_status_rule_without_advisor(self):
        from services.task_assignment_service import get_assignee_for_review_status

        rw = SimpleNamespace(
            status="sent",
            client_id=10,
            client=SimpleNamespace(id=10, advisor_id=None),
        )
        rule = SimpleNamespace(
            rule_type="review_status",
            match_value="sent",
            assigned_user_id=55,
        )
        with patch("services.task_assignment_service._get_rules", return_value=[rule]):
            uid = get_assignee_for_review_status(rw)
        self.assertEqual(uid, 55)


class TestReassignIssue(unittest.TestCase):
    def test_manual_flag_set_and_tasks_updated(self):
        from services.task_assignment_service import (
            issue_has_manual_assignment,
            reassign_issue,
        )

        issue = MagicMock()
        issue.status = "open"
        issue.assigned_to = 1
        issue.details = {"foo": "bar"}
        issue.id = 99

        assignee = MagicMock()
        assignee.is_active = True

        task = MagicMock()
        task.assigned_to = 1

        with patch("services.task_assignment_service.User") as MockUser, patch(
            "services.task_assignment_service.OpsTask"
        ) as MockOps, patch(
            "services.task_assignment_service.create_ops_task_from_issue"
        ) as mock_create:
            MockUser.query.get.return_value = assignee
            MockOps.query.filter.return_value.all.return_value = [task]
            MockOps.query.filter.return_value.first.return_value = task
            ok = reassign_issue(issue, 42, by_user_id=5)

        self.assertTrue(ok)
        self.assertEqual(issue.assigned_to, 42)
        self.assertTrue(issue_has_manual_assignment(issue))
        self.assertEqual(issue.details.get("assigned_by"), 5)
        self.assertEqual(task.assigned_to, 42)
        mock_create.assert_not_called()

    def test_rejects_inactive_assignee(self):
        from services.task_assignment_service import reassign_issue

        issue = MagicMock()
        issue.status = "open"
        issue.assigned_to = None
        issue.details = {}

        assignee = MagicMock()
        assignee.is_active = False
        with patch("services.task_assignment_service.User") as MockUser:
            MockUser.query.get.return_value = assignee
            with self.assertRaises(ValueError):
                reassign_issue(issue, 9, by_user_id=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
