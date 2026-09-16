from __future__ import annotations

from pathlib import Path

import pytest

# File-existence smoke checks only — no Flask/DB required.
pytestmark = pytest.mark.no_app


def _exists(rel: str) -> None:
    p = Path(__file__).resolve().parents[2] / rel
    assert p.exists(), f"Expected path to exist: {rel}"


def test_pack_auth_core() -> None:
    _exists("access_control.py")


def test_pack_clients_core() -> None:
    _exists("routes/clients.py")
    _exists("templates/clients/client_details.html")


def test_pack_agreements_core() -> None:
    _exists("routes/agreements.py")
    _exists("templates/agreements/view_agreement.html")


def test_pack_billing_core() -> None:
    _exists("billing_calculation_service.py")
    _exists("invoice_generation_service.py")


def test_pack_invoices_core() -> None:
    _exists("routes/invoices.py")
    _exists("templates/invoices/list.html")


def test_pack_reviews_core() -> None:
    # Reviews are implemented across a few entry points; keep this light and stable.
    _exists("review_routes.py")
    _exists("services/period_performer_return_service.py")
    _exists("api/v1/period_analysis.py")


def test_pack_transactions_core() -> None:
    _exists("models.py")


def test_pack_portfolio_core() -> None:
    _exists("services/price_service.py")


def test_pack_ops_tasks_core() -> None:
    _exists("services/task_assignment_service.py")


def test_pack_alerts_core() -> None:
    _exists("alert_service.py")


def test_pack_maintenance_tools_core() -> None:
    _exists("routes/main_sections/admin_tools.py")
    _exists("templates/maintenance/trade_data.html")

