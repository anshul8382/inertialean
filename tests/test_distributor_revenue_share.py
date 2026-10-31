"""Pure math / semantics for distributor revenue share (no DB)."""
from decimal import Decimal
from types import SimpleNamespace
from datetime import date, datetime

def test_indian_fy_helpers():
    from services.distributor_service import (
        indian_fy_bounds,
        indian_fy_label,
        indian_fy_start_year,
    )

    assert indian_fy_start_year(date(2026, 7, 15)) == 2026
    assert indian_fy_start_year(date(2026, 3, 31)) == 2025
    assert indian_fy_bounds(2026) == (date(2026, 4, 1), date(2027, 3, 31))
    assert indian_fy_label(2026) == "FY 2026-27"


def _payout(amount_paid, percent) -> Decimal:
    amount = Decimal(str(amount_paid)).quantize(Decimal("0.01"))
    pct = Decimal(str(percent)).quantize(Decimal("0.01"))
    return (amount * pct / Decimal("100")).quantize(Decimal("0.01"))


def test_payout_percent_of_amount_paid():
    assert _payout("100000.00", "20") == Decimal("20000.00")
    assert _payout("18142.50", "10.5") == Decimal("1904.96")


def test_zero_percent_means_no_payout():
    assert _payout("50000", "0") == Decimal("0.00")


def test_proportional_clawback_partial_refund():
    from services.distributor_service import apply_proportional_clawback

    attr = SimpleNamespace(
        id=1,
        status="active",
        invoice_amount_paid=Decimal("100000.00"),
        payout_amount=Decimal("20000.00"),
        clawback_amount=Decimal("0.00"),
        notes="",
        reversed_at=None,
        payout_status="released",
    )
    # 25% refund → claw back 25% of payout
    clawed = apply_proportional_clawback(attr, Decimal("25000.00"), reason="partial refund")
    assert clawed == Decimal("5000.00")
    assert attr.payout_amount == Decimal("15000.00")
    assert attr.invoice_amount_paid == Decimal("75000.00")
    assert attr.clawback_amount == Decimal("5000.00")
    assert attr.status == "active"
    assert attr.payout_status == "released"


def test_proportional_clawback_full_reverse():
    from services.distributor_service import apply_proportional_clawback

    attr = SimpleNamespace(
        id=2,
        status="active",
        invoice_amount_paid=Decimal("10000.00"),
        payout_amount=Decimal("1500.00"),
        clawback_amount=Decimal("0.00"),
        notes="",
        reversed_at=None,
    )
    clawed = apply_proportional_clawback(attr, Decimal("10000.00"), reason="cancelled")
    assert clawed == Decimal("1500.00")
    assert attr.payout_amount == Decimal("0.00")
    assert attr.invoice_amount_paid == Decimal("0.00")
    assert attr.status == "reversed"
    assert isinstance(attr.reversed_at, datetime)


def test_held_identity_still_independent_of_distributor():
    """Sanity: pathway-style residual is unrelated to revenue %."""
    adj_qty, start_p, end_p = 87.0, 861.45, 965.0
    end_qty, net_cf = 90.0, 1801.35
    start_value = adj_qty * start_p
    end_value = end_qty * end_p
    base = adj_qty * (end_p - start_p)
    pc = end_value - (adj_qty * end_p) - net_cf
    assert abs((base + pc + net_cf) - (end_value - start_value)) < 0.02
