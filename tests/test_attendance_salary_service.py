"""Unit tests for attendance salary breakdown (no DB required for pure math helpers)."""
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from services.attendance_salary_service import _money, month_bounds


def test_money_parses():
    assert _money("1,250.5") == Decimal("1250.50")
    assert _money(None) == Decimal("0.00")
    assert _money(Decimal("10")) == Decimal("10")


def test_month_bounds():
    start, end = month_bounds(2026, 8)
    assert start == date(2026, 8, 1)
    assert end == date(2026, 9, 1)
    start, end = month_bounds(2025, 12)
    assert start == date(2025, 12, 1)
    assert end == date(2026, 1, 1)


class _CmpCol:
    """Stand-in so filter expressions like Model.col >= date do not raise."""

    def __ge__(self, other):
        return True

    def __gt__(self, other):
        return True

    def __lt__(self, other):
        return True

    def __le__(self, other):
        return True

    def __eq__(self, other):
        return True

    def asc(self):
        return self

    def desc(self):
        return self


@patch("services.attendance_salary_service.MonthlySalary")
@patch("services.attendance_salary_service.UserClaim")
@patch("services.attendance_salary_service.Attendance")
@patch("services.attendance_salary_service.UserStipend")
def test_build_month_breakdown_formula(mock_stipend_cls, mock_att_cls, mock_claim_cls, mock_sal_cls):
    from services.attendance_salary_service import build_month_breakdown

    stipend = MagicMock()
    stipend.id = 1
    stipend.full_day_stipend = Decimal("1000")
    stipend.half_day_stipend = Decimal("500")
    stipend.leave_stipend = Decimal("0")
    mock_stipend_cls.query.filter_by.return_value.first.return_value = stipend

    mock_att_cls.user_id = _CmpCol()
    mock_att_cls.date = _CmpCol()

    def _rec(d, typ, amt):
        r = MagicMock()
        r.date = d
        r.attendance_type = typ
        r.stipend_amount = amt
        r.notes = ""
        return r

    records = [
        _rec(date(2026, 8, 3), "full_day", Decimal("1000")),
        _rec(date(2026, 8, 4), "full_day", Decimal("1000")),
        _rec(date(2026, 8, 5), "half_day", Decimal("500")),
        _rec(date(2026, 8, 6), "leave", Decimal("0")),
    ]
    mock_att_cls.query.filter.return_value.order_by.return_value.all.return_value = records

    claim = MagicMock()
    claim.id = 1
    claim.claim_date = date(2026, 8, 10)
    claim.amount = Decimal("200")
    claim.description = "Travel"
    claim.admin_notes = ""
    mock_claim_cls.query.filter.return_value.order_by.return_value.all.return_value = [claim]
    mock_claim_cls.user_id = _CmpCol()
    mock_claim_cls.status = _CmpCol()
    mock_claim_cls.claim_date = _CmpCol()

    mock_sal_cls.query.filter_by.return_value.first.return_value = None

    bd = build_month_breakdown(
        1, 2026, 8, sales_incentive="100", internal_incentive="50"
    )

    assert bd["counts"]["full_days"] == 2
    assert bd["counts"]["half_days"] == 1
    assert bd["counts"]["leave_days"] == 1
    assert bd["base_stipend"] == Decimal("2500.00")
    assert bd["approved_claims_amount"] == Decimal("200.00")
    assert bd["sales_incentive"] == Decimal("100.00")
    assert bd["internal_incentive"] == Decimal("50.00")
    assert bd["total_salary"] == Decimal("2850.00")
    assert len(bd["day_rows"]) == 4
    assert bd["day_rows"][0]["weekday"] == "Monday"
