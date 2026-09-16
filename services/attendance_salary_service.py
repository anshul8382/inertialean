"""
Attendance-based monthly salary breakdown for admin payroll views.
total = base_stipend + approved_claims + sales_incentive + internal_incentive
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import extract

from extensions import db
from models import Attendance, MonthlySalary, UserClaim, UserStipend


def _money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip().replace(",", "") or "0").quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """Inclusive start, exclusive end."""
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start, end


def get_or_default_stipend(user_id: int) -> UserStipend:
    config = UserStipend.query.filter_by(user_id=user_id).first()
    if config:
        return config
    return UserStipend(
        user_id=user_id,
        full_day_stipend=Decimal("1000.00"),
        half_day_stipend=Decimal("500.00"),
        leave_stipend=Decimal("0.00"),
    )


def build_month_breakdown(
    user_id: int,
    year: int,
    month: int,
    *,
    sales_incentive: Optional[Any] = None,
    internal_incentive: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Build a detailed payroll breakdown for one user/month.
    If incentive args are None, reuse values from an existing MonthlySalary row.
    """
    start, end = month_bounds(year, month)
    stipend = get_or_default_stipend(user_id)

    attendance_records = (
        Attendance.query.filter(
            Attendance.user_id == user_id,
            Attendance.date >= start,
            Attendance.date < end,
        )
        .order_by(Attendance.date.asc())
        .all()
    )

    full_days = [r for r in attendance_records if r.attendance_type == "full_day"]
    half_days = [r for r in attendance_records if r.attendance_type == "half_day"]
    leave_days = [r for r in attendance_records if r.attendance_type == "leave"]

    full_rate = _money(stipend.full_day_stipend)
    half_rate = _money(stipend.half_day_stipend)
    leave_rate = _money(stipend.leave_stipend)

    full_pay = (full_rate * len(full_days)).quantize(Decimal("0.01"))
    half_pay = (half_rate * len(half_days)).quantize(Decimal("0.01"))
    leave_pay = (leave_rate * len(leave_days)).quantize(Decimal("0.01"))
    base_stipend = (full_pay + half_pay + leave_pay).quantize(Decimal("0.01"))

    approved_claims = (
        UserClaim.query.filter(
            UserClaim.user_id == user_id,
            UserClaim.status == "approved",
            extract("year", UserClaim.claim_date) == year,
            extract("month", UserClaim.claim_date) == month,
        )
        .order_by(UserClaim.claim_date.asc())
        .all()
    )
    claims_amount = sum((_money(c.amount) for c in approved_claims), Decimal("0.00")).quantize(
        Decimal("0.01")
    )

    existing = MonthlySalary.query.filter_by(user_id=user_id, year=year, month=month).first()
    if sales_incentive is None:
        sales_incentive = getattr(existing, "sales_incentive", None) if existing else None
    if internal_incentive is None:
        internal_incentive = getattr(existing, "internal_incentive", None) if existing else None

    sales_inc = _money(sales_incentive)
    internal_inc = _money(internal_incentive)
    total_salary = (base_stipend + claims_amount + sales_inc + internal_inc).quantize(
        Decimal("0.01")
    )

    day_rows: List[Dict[str, Any]] = []
    for record in attendance_records:
        day_rows.append(
            {
                "date": record.date,
                "weekday": record.date.strftime("%A"),
                "attendance_type": record.attendance_type,
                "stipend_amount": _money(record.stipend_amount),
                "notes": record.notes or "",
            }
        )

    claim_rows = [
        {
            "id": c.id,
            "claim_date": c.claim_date,
            "amount": _money(c.amount),
            "description": c.description,
            "admin_notes": c.admin_notes or "",
        }
        for c in approved_claims
    ]

    return {
        "year": year,
        "month": month,
        "month_name": date(year, month, 1).strftime("%B %Y"),
        "days_in_month": monthrange(year, month)[1],
        "stipend_config": {
            "full_day_stipend": full_rate,
            "half_day_stipend": half_rate,
            "leave_stipend": leave_rate,
            "persisted": bool(getattr(stipend, "id", None)),
        },
        "counts": {
            "total_days": len(attendance_records),
            "full_days": len(full_days),
            "half_days": len(half_days),
            "leave_days": len(leave_days),
        },
        "lines": {
            "full_days": {
                "count": len(full_days),
                "rate": full_rate,
                "amount": full_pay,
                "label": f"{len(full_days)} full day(s) × ₹{full_rate}",
            },
            "half_days": {
                "count": len(half_days),
                "rate": half_rate,
                "amount": half_pay,
                "label": f"{len(half_days)} half day(s) × ₹{half_rate}",
            },
            "leave_days": {
                "count": len(leave_days),
                "rate": leave_rate,
                "amount": leave_pay,
                "label": f"{len(leave_days)} leave day(s) × ₹{leave_rate}",
            },
        },
        "base_stipend": base_stipend,
        "approved_claims_amount": claims_amount,
        "sales_incentive": sales_inc,
        "internal_incentive": internal_inc,
        "total_salary": total_salary,
        "day_rows": day_rows,
        "claim_rows": claim_rows,
        "existing_salary": existing,
        "status": existing.status if existing else None,
    }


def upsert_monthly_salary(
    user_id: int,
    year: int,
    month: int,
    *,
    sales_incentive: Optional[Any] = None,
    internal_incentive: Optional[Any] = None,
    admin_notes: Optional[str] = None,
) -> MonthlySalary:
    """Compute breakdown and create/update MonthlySalary. Caller commits."""
    breakdown = build_month_breakdown(
        user_id,
        year,
        month,
        sales_incentive=sales_incentive,
        internal_incentive=internal_incentive,
    )

    monthly = MonthlySalary.query.filter_by(user_id=user_id, year=year, month=month).first()
    if not monthly:
        monthly = MonthlySalary(user_id=user_id, year=year, month=month)
        db.session.add(monthly)

    monthly.total_days = breakdown["counts"]["total_days"]
    monthly.full_days = breakdown["counts"]["full_days"]
    monthly.half_days = breakdown["counts"]["half_days"]
    monthly.leave_days = breakdown["counts"]["leave_days"]
    monthly.base_stipend = breakdown["base_stipend"]
    monthly.total_stipend = breakdown["base_stipend"]
    monthly.approved_claims = breakdown["approved_claims_amount"]
    monthly.sales_incentive = breakdown["sales_incentive"]
    monthly.internal_incentive = breakdown["internal_incentive"]
    monthly.total_salary = breakdown["total_salary"]
    if monthly.status not in ("paid", "closed"):
        monthly.status = "calculated"
    if admin_notes is not None:
        monthly.admin_notes = admin_notes

    return monthly
