"""Unit tests for unified client integrity refresh orchestrator."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class TestClientIntegrityRefreshService(unittest.TestCase):
    def test_refresh_client_calls_core_steps(self):
        from services.client_integrity_refresh_service import refresh_client_integrity

        with patch(
            "agents.data_integrity_manager.DataIntegrityManager"
        ) as MockDIM, patch(
            "agents.recommendation_execution_monitor.RecommendationExecutionMonitor"
        ) as MockREM, patch(
            "agents.portfolio_performance_monitor.PortfolioPerformanceMonitor"
        ) as MockPPM, patch(
            "services.issue_healer_service.run_issue_healer",
            return_value={"evaluated": 2, "cleared": 1, "issues_resolved": 1, "tasks_closed": 1},
        ) as mock_heal, patch(
            "services.task_auto_close_service.close_completed_ops_tasks", return_value=0
        ), patch(
            "services.alert_task_reconcile_service.resolve_stale_client_wide_alerts_for_client",
            return_value=0,
        ), patch(
            "agents.orchestrator.ALERT_AVAILABLE", True
        ), patch(
            "agents.orchestrator.AlertOrchestrator"
        ) as MockOrch, patch(
            "services.client_data_integrity_nightly_service.run_client_data_integrity_nightly",
            return_value={"clients": 1, "generated_at": "t"},
        ) as mock_di, patch(
            "services.cashflow_trade_integrity_case_service.get_client_cashflow_trade_badge",
            return_value={"status": "matched"},
        ), patch(
            "services.client_integrity_refresh_service._merge_client_health_observations",
            return_value={"observation_count": 0},
        ), patch(
            "extensions.db.session"
        ) as mock_session:
            MockDIM.return_value.run_checks.return_value = []
            MockREM.return_value.run_checks.return_value = []
            MockPPM.return_value.run_checks.return_value = []
            MockOrch.return_value.orchestrate_alerts.return_value = "run-1"

            out = refresh_client_integrity(137, actor_user_id=1)

        self.assertTrue(out["ok"])
        MockDIM.return_value.run_checks.assert_called_once_with(137, since=None)
        MockREM.return_value.run_checks.assert_called_once_with(137, since=None)
        MockPPM.return_value.run_checks.assert_called_once_with(137)
        mock_heal.assert_called_once()
        mock_di.assert_called_once()
        self.assertEqual(mock_di.call_args.kwargs.get("client_ids"), [137])
        self.assertTrue(mock_di.call_args.kwargs.get("merge"))
        MockOrch.return_value.orchestrate_alerts.assert_called_once_with(
            client_ids=[137], user_id=1
        )
        mock_session.commit.assert_called()

    def test_refresh_integrity_scope_client_requires_id(self):
        from services.client_integrity_refresh_service import refresh_integrity_scope

        out = refresh_integrity_scope(mode="client")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "client_id required")

    def test_format_refresh_flash(self):
        from services.client_integrity_refresh_service import format_refresh_flash

        msg = format_refresh_flash(
            {"mode": "incremental", "clients_requested": 3, "clients_ok": 3, "ok": True}
        )
        self.assertIn("3/3", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
