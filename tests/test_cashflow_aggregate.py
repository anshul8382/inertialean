"""Unit tests for CashflowService.aggregate_cashflows (date/month/year netting)."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from services.cashflow_service import CashflowService


def _cf(client_id, date_s, amount, name=None, cf_type=None):
    d = datetime.strptime(date_s, "%Y-%m-%d")
    if cf_type is None:
        cf_type = "INFLOW" if amount < 0 else "OUTFLOW"
    client = SimpleNamespace(name=name or f"Client {client_id}")
    return SimpleNamespace(
        client_id=client_id,
        date=d,
        amount=amount,
        type=cf_type,
        client=client,
    )


class TestAggregateCashflows:
    def test_month_nets_same_month_same_client(self):
        rows = [
            _cf(1, "2024-01-05", -100000, "A"),
            _cf(1, "2024-01-20", 30000, "A"),
            _cf(1, "2024-02-01", -50000, "A"),
        ]
        out = CashflowService.aggregate_cashflows(rows, "month", group_by_client=True)
        assert len(out) == 2
        by_key = {r["period_key"]: r for r in out}
        assert by_key["2024-01"]["net_amount"] == -70000.0
        assert by_key["2024-01"]["type"] == "INFLOW"
        assert by_key["2024-01"]["row_count"] == 2
        assert by_key["2024-01"]["period_label"] == "Jan 2024"
        assert by_key["2024-02"]["net_amount"] == -50000.0

    def test_all_clients_nets_across_clients(self):
        rows = [
            _cf(1, "2024-03-01", -100000, "A"),
            _cf(2, "2024-03-15", 40000, "B"),
            _cf(2, "2024-04-01", -10000, "B"),
        ]
        out = CashflowService.aggregate_cashflows(rows, "month", group_by_client=False)
        assert len(out) == 2
        by_key = {r["period_key"]: r for r in out}
        assert by_key["2024-03"]["net_amount"] == -60000.0
        assert by_key["2024-03"]["client_id"] is None
        assert by_key["2024-03"]["client_count"] == 2
        assert "All clients" in by_key["2024-03"]["client_name"]
        assert by_key["2024-03"]["row_count"] == 2

    def test_date_aggregates_same_day(self):
        rows = [
            _cf(1, "2024-05-10", -20000, "A"),
            _cf(1, "2024-05-10", 5000, "A"),
            _cf(1, "2024-05-11", -1000, "A"),
        ]
        out = CashflowService.aggregate_cashflows(rows, "date", group_by_client=True)
        by_key = {r["period_key"]: r for r in out}
        assert by_key["2024-05-10"]["net_amount"] == -15000.0
        assert by_key["2024-05-10"]["row_count"] == 2
        assert by_key["2024-05-11"]["net_amount"] == -1000.0

    def test_year_nets_and_omits_zero(self):
        rows = [
            _cf(1, "2023-01-01", -10000, "A"),
            _cf(1, "2023-06-01", 10000, "A"),
            _cf(1, "2024-01-01", -5000, "A"),
        ]
        out = CashflowService.aggregate_cashflows(rows, "year", group_by_client=True)
        assert len(out) == 1
        assert out[0]["period_key"] == "2024"
        assert out[0]["net_amount"] == -5000.0

    def test_invalid_period_raises(self):
        with pytest.raises(ValueError):
            CashflowService.aggregate_cashflows([], "week", group_by_client=True)

    def test_group_by_client_keeps_clients_separate(self):
        rows = [
            _cf(1, "2024-01-01", -10000, "A"),
            _cf(2, "2024-01-15", -20000, "B"),
        ]
        out = CashflowService.aggregate_cashflows(rows, "month", group_by_client=True)
        assert len(out) == 2
        names = {r["client_name"] for r in out}
        assert names == {"A", "B"}
