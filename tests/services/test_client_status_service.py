"""Unit tests for ClientStatusService rolling-window scoring."""

import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

# Ensure repo root is on sys.path so `client_status_service` can be imported
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class FakeAlert:
    def __init__(
        self,
        *,
        id=1,
        alert_type='workflow_sla',
        alert_subtype='FUNDS_delay',
        severity='warning',
        status='active',
        title='t',
        description='d',
        created_at=None,
        resolved_at=None,
        acknowledged_at=None,
        sla_timeline=None,
        current_delay=None,
    ):
        self.id = id
        self.alert_type = alert_type
        self.alert_subtype = alert_subtype
        self.severity = severity
        self.status = status
        self.title = title
        self.description = description
        self.created_at = created_at
        self.resolved_at = resolved_at
        self.acknowledged_at = acknowledged_at
        self.sla_timeline = sla_timeline
        self.current_delay = current_delay


class TestClientStatusService(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2025, 12, 1, 0, 0, 0)

    def _run_status(self, alerts):
        from client_status_service import ClientStatusService

        with patch('client_status_service.current_app', True), \
            patch('client_status_service.Client') as MockClient, \
            patch('client_status_service.Alert') as MockAlert:

            MockClient.query.get.return_value = SimpleNamespace(id=1, name='Test Client')
            MockAlert.query.filter_by.return_value.all.return_value = alerts

            return ClientStatusService.calculate_client_status(1, as_of=self.now)

    def test_no_alerts_is_excellent_and_100(self):
        status = self._run_status([])
        self.assertEqual(status['status'], 'excellent')
        self.assertEqual(status['score'], 100)
        self.assertEqual(status['details']['total_active_alerts'], 0)

    def test_resolved_alert_older_than_6_month_window_does_not_affect_score(self):
        old_resolved = FakeAlert(
            id=1,
            alert_type='workflow_sla',
            alert_subtype='FUNDS_delay',
            severity='warning',
            status='resolved',
            created_at=self.now - timedelta(days=410),
            resolved_at=self.now - timedelta(days=390),
            sla_timeline=48,
        )
        status = self._run_status([old_resolved])
        self.assertEqual(status['status'], 'excellent')
        self.assertEqual(status['score'], 100)

    def test_active_alert_age_penalty_is_capped_by_domain_window(self):
        very_old_active = FakeAlert(
            id=1,
            alert_type='workflow_sla',
            alert_subtype='EXEC_delay',
            severity='critical',
            status='active',
            created_at=self.now - timedelta(days=1000),
            sla_timeline=72,
        )
        status = self._run_status([very_old_active])

        # investment_ops window is ~6 months => 183 days
        expected_cap_hours = 183 * 24
        self.assertEqual(status['details']['avg_alert_age_hours'], expected_cap_hours)
        self.assertEqual(status['details']['oldest_alert_hours'], expected_cap_hours)

        # Severe + long-running active SLA breaches can drive score down to 0
        self.assertEqual(status['score'], 0)

    def test_reviews_use_two_year_window_for_resolution_rate(self):
        resolved_review = FakeAlert(
            id=1,
            alert_type='review_overdue',
            alert_subtype='overdue',
            severity='warning',
            status='resolved',
            created_at=self.now - timedelta(days=100),
            resolved_at=self.now - timedelta(days=90),
            sla_timeline=24,
        )
        active_review = FakeAlert(
            id=2,
            alert_type='review_overdue',
            alert_subtype='overdue',
            severity='warning',
            status='active',
            created_at=self.now - timedelta(days=50),
            sla_timeline=24,
        )

        status = self._run_status([resolved_review, active_review])

        # 2 created review alerts in-window, 1 resolved in-window => 0.5
        self.assertEqual(status['details']['domain_windows_days']['reviews'], 730)
        self.assertEqual(status['details']['resolution_rate_by_domain']['reviews'], 0.5)


if __name__ == '__main__':
    unittest.main(verbosity=2)



