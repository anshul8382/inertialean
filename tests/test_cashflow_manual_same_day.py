"""Manual cashflow create must allow multiple rows on the same calendar day."""

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.cashflow_service import CashflowService

pytestmark = pytest.mark.no_app


def test_create_manual_cashflow_allows_same_day_when_row_exists():
    """Regression: UI used to skip when any cashflow already existed for that date."""
    existing = SimpleNamespace(id=99, client_id=1, date=datetime(2024, 5, 10))

    with patch(
        "services.investment_cycle.validate_amount_for_schedule",
        return_value=True,
    ), patch("models.MonthlyInvestmentSchedule") as Sched, patch(
        "services.cashflow_service.Cashflow"
    ) as Cf, patch("services.cashflow_service.db") as db:
        Sched.query.filter_by.return_value.first.return_value = None
        # Old bug path queried first(); even if something returns existing, we must create.
        Cf.query.filter_by.return_value.first.return_value = existing
        instance = MagicMock()
        instance.id = 100
        Cf.return_value = instance

        result = CashflowService.create_manual_cashflow(
            client_id=1,
            date_obj=date(2024, 5, 10),
            amount=-50000,
            description="Second note same day",
            created_by=7,
            cashflow_type="INFLOW",
        )

        assert result["success"] is True
        assert result["action"] == "created"
        assert result.get("reason") != "Cashflow already exists for this date"
        Cf.assert_called_once()
        kwargs = Cf.call_args.kwargs
        assert kwargs["client_id"] == 1
        assert kwargs["amount"] == -50000
        assert kwargs["date"] == datetime(2024, 5, 10, 0, 0)
        db.session.add.assert_called_once_with(instance)


def test_create_manual_cashflow_rejects_bad_date():
    with patch(
        "services.investment_cycle.validate_amount_for_schedule",
        return_value=True,
    ), patch("models.MonthlyInvestmentSchedule") as Sched:
        Sched.query.filter_by.return_value.first.return_value = None
        result = CashflowService.create_manual_cashflow(
            client_id=1,
            date_obj="not-a-date",
            amount=-1000,
            description="x",
            created_by=1,
        )
        assert result["success"] is False
        assert "Invalid cashflow date" in result["error"]


def test_update_cashflow_normalizes_date_and_signed_amount():
    cf = SimpleNamespace(
        id=5,
        client_id=1,
        date=datetime(2024, 5, 10, 12, 30),
        amount=-1000,
        type="INFLOW",
        description="old",
    )

    with patch("services.cashflow_service.Cashflow") as Cf:
        Cf.query.get.return_value = cf
        result = CashflowService.update_cashflow(
            5,
            {
                "date": date(2024, 6, 1),
                "amount": -25000,
                "description": "Adjusted after trade edit",
                "cashflow_type": "INFLOW",
            },
        )

    assert result["success"] is True
    assert cf.date == datetime(2024, 6, 1, 0, 0)
    assert cf.amount == -25000
    assert cf.type == "INFLOW"
    assert cf.description == "Adjusted after trade edit"


def test_update_cashflow_rejects_zero_amount():
    cf = SimpleNamespace(id=5, client_id=1, amount=-10, type="INFLOW", description="x", date=None)
    with patch("services.cashflow_service.Cashflow") as Cf:
        Cf.query.get.return_value = cf
        result = CashflowService.update_cashflow(5, {"amount": 0})
    assert result["success"] is False
    assert "non-zero" in result["error"]
