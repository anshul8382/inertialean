"""Unit tests for issue root-cause evaluator."""

import os
import sys
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class TestIssueRootCauseEvaluator(unittest.TestCase):
    def test_priority_score_includes_severity_and_aging(self):
        from services.issue_root_cause_evaluator import compute_issue_priority_score

        now = datetime(2026, 4, 5, 0, 0, 0)
        issue = SimpleNamespace(severity="critical", detected_at=now - timedelta(days=10))
        out = compute_issue_priority_score(issue, as_of=now)
        self.assertEqual(out["severity_weight"], 100)
        self.assertEqual(out["days_open"], 10)
        self.assertEqual(out["aging_weight"], 20)
        self.assertEqual(out["score"], 120)

    def test_evaluate_issue_root_cause_for_non_open_issue_returns_cleared(self):
        from services.issue_root_cause_evaluator import evaluate_issue_root_cause

        issue = SimpleNamespace(status="resolved", check_category="PORTFOLIO_PERFORMANCE")
        out = evaluate_issue_root_cause(issue)
        self.assertTrue(out["is_cleared"])
        self.assertEqual(out["evidence"]["status"], "resolved")

    def test_evaluate_issue_root_cause_dispatches_rec_exec(self):
        from services.issue_root_cause_evaluator import evaluate_issue_root_cause

        issue = SimpleNamespace(status="open", check_category="RECOMMENDATION_EXECUTION")
        with patch(
            "services.issue_root_cause_evaluator._evaluate_recommendation_execution",
            return_value={"is_cleared": True, "reason": "ok", "evidence": {}},
        ) as mock_eval:
            out = evaluate_issue_root_cause(issue)
        self.assertTrue(out["is_cleared"])
        mock_eval.assert_called_once_with(issue)

    def test_evaluate_issue_root_cause_dispatches_duplicates(self):
        from services.issue_root_cause_evaluator import evaluate_issue_root_cause

        issue = SimpleNamespace(status="open", check_category="DUPLICATES")
        with patch(
            "services.issue_root_cause_evaluator._evaluate_duplicates",
            return_value={"is_cleared": True, "reason": "gone", "evidence": {}},
        ) as mock_eval:
            out = evaluate_issue_root_cause(issue)
        self.assertTrue(out["is_cleared"])
        mock_eval.assert_called_once_with(issue)

    def test_portfolio_evaluator_marks_cleared_when_no_underperformance(self):
        from services.issue_root_cause_evaluator import _evaluate_portfolio_performance

        issue = SimpleNamespace(client_id=123)
        with patch(
            "agents.portfolio_performance_monitor.compute_underperformance_snapshot",
            return_value={"underperformance_pct": -0.5},
        ):
            out = _evaluate_portfolio_performance(issue)
        self.assertTrue(out["is_cleared"])
        self.assertIn("above benchmark", out["reason"])

    def test_duplicates_cleared_when_fewer_than_two_matches(self):
        from services.issue_root_cause_evaluator import _evaluate_duplicates

        issue = SimpleNamespace(
            client_id=137,
            status="open",
            check_category="DUPLICATES",
            check_name="duplicate_transaction",
            security_id=1398,
            transaction_id=56899,
            reference_date=date(2026, 7, 13),
            details={
                "type": "BUY",
                "quantity": 15.0,
                "price": 1349.8,
                "amount": 20247.0,
                "security_id": 1398,
                "date": "2026-07-13T00:00:00",
            },
        )
        leftover = SimpleNamespace(id=56899)
        with patch(
            "services.issue_root_cause_evaluator.find_matching_duplicate_transactions",
            return_value=[leftover],
        ) as mock_find:
            out = _evaluate_duplicates(issue)
        self.assertTrue(out["is_cleared"])
        self.assertEqual(out["evidence"]["match_count"], 1)
        mock_find.assert_called_once()
        kwargs = mock_find.call_args.kwargs
        self.assertEqual(kwargs["client_id"], 137)
        self.assertEqual(kwargs["security_id"], 1398)
        self.assertEqual(kwargs["txn_type"], "BUY")
        self.assertEqual(kwargs["trade_date"], date(2026, 7, 13))

    def test_duplicates_not_cleared_when_two_matches_remain(self):
        from services.issue_root_cause_evaluator import _evaluate_duplicates

        issue = SimpleNamespace(
            client_id=1,
            status="open",
            check_category="DUPLICATES",
            check_name="duplicate_transaction",
            security_id=10,
            transaction_id=2,
            reference_date=date(2026, 1, 2),
            details={
                "type": "BUY",
                "quantity": 1.0,
                "price": 100.0,
                "amount": 100.0,
            },
        )
        with patch(
            "services.issue_root_cause_evaluator.find_matching_duplicate_transactions",
            return_value=[SimpleNamespace(id=1), SimpleNamespace(id=2)],
        ):
            out = _evaluate_duplicates(issue)
        self.assertFalse(out["is_cleared"])
        self.assertEqual(out["evidence"]["match_count"], 2)


class TestIssueHealerDuplicatesCategory(unittest.TestCase):
    def test_default_categories_include_duplicates(self):
        from services.issue_healer_service import DEFAULT_CATEGORIES

        self.assertIn("DUPLICATES", DEFAULT_CATEGORIES)

    def test_healer_resolves_cleared_duplicate_issue(self):
        from services.issue_healer_service import run_issue_healer

        issue = SimpleNamespace(
            id=2559,
            client_id=137,
            check_category="DUPLICATES",
            check_name="duplicate_transaction",
            severity="critical",
            detected_at=datetime(2026, 8, 2),
            status="open",
        )
        mock_q = MagicMock()
        mock_q.filter.return_value = mock_q
        mock_q.all.return_value = [issue]

        with patch("services.issue_healer_service.DataIntegrityIssue") as MockIssue, patch(
            "services.issue_healer_service.evaluate_issue_root_cause",
            return_value={
                "is_cleared": True,
                "reason": "Exact duplicate trade no longer present",
                "evidence": {},
            },
        ), patch(
            "services.issue_healer_service.resolve_issue_and_sync",
            return_value={
                "issue_resolved": 1,
                "linked_tasks_closed": 0,
                "linked_alerts_resolved": 0,
                "client_stale_alerts_resolved": 0,
            },
        ) as mock_resolve:
            MockIssue.query = mock_q
            stats = run_issue_healer(dry_run=False, categories=["DUPLICATES"])

        self.assertEqual(stats["cleared"], 1)
        self.assertEqual(stats["issues_resolved"], 1)
        mock_resolve.assert_called_once()
        self.assertEqual(mock_resolve.call_args.kwargs["resolution_type"], "auto_cleared")


if __name__ == "__main__":
    unittest.main(verbosity=2)
