#!/usr/bin/env python3
"""
Manual SLA Check Script
Run this script via cron job to check for SLA violations and create alerts
"""

import sys
import os
from datetime import datetime

# Add the app directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from flask import has_app_context

from alert_service import AlertService

_worker_flask_app = None


def get_flask_app_for_worker():
    """One Flask app per process (Airflow workers reuse processes; avoids duplicate ORM registration)."""
    global _worker_flask_app
    if _worker_flask_app is None:
        from main import create_app

        _worker_flask_app = create_app()
    return _worker_flask_app


def run_manual_sla_check():
    """Run SLA checks manually (cron, CLI, or Airflow).

    If the caller already pushed an app context (e.g. ``monitoring_dag`` used to call
    ``create_app()`` then this function), do **not** call ``create_app()`` again — a second
    init registers Flask-Session's ``sessions`` model twice and raises
    ``InvalidRequestError: Table 'sessions' is already defined``.
    """
    try:
        print(f"Starting manual SLA check at {datetime.now()}")

        def _run():
            workflow_alerts = AlertService.check_workflow_sla()
            print(f"Workflow SLA check completed. Alerts created: {len(workflow_alerts) if workflow_alerts else 0}")

            recommendation_alerts = AlertService.check_recommendation_sla()
            print(f"Recommendation SLA check completed. Alerts created: {len(recommendation_alerts) if recommendation_alerts else 0}")

            AlertService.check_meeting_reminders()
            print("Meeting reminder check completed")

            AlertService.check_meeting_cadence_sla()
            print("Meeting cadence check completed")

            AlertService.check_invoice_payment_sla()
            print("Invoice payment SLA check completed")

            AlertService.check_review_sla()
            print("Review SLA check completed")

            AlertService.check_ticket_sla()
            print("Ticket SLA check completed")

            print(f"SLA check completed successfully at {datetime.now()}")

        if has_app_context():
            _run()
        else:
            with get_flask_app_for_worker().app_context():
                _run()

    except Exception as e:
        print(f"Error during SLA check: {str(e)}")
        import traceback
        traceback.print_exc()
        raise


def main():
    """Entry point for Airflow/cron: run the full SLA check."""
    run_manual_sla_check()


if __name__ == "__main__":
    main()
