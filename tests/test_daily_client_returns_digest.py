"""Unit tests for daily client returns digest helpers."""
from datetime import date

from services.daily_client_returns_digest_service import (
    _CF,
    _pct,
    _period_net_invested,
    _window_cashflows,
)


def test_period_net_invested_and_absolute():
    v0 = 100_000.0
    window = [
        _CF(date=date(2026, 2, 1), amount=-20_000.0),  # invest
        _CF(date=date(2026, 3, 1), amount=5_000.0),  # withdraw
    ]
    pni = _period_net_invested(v0, window)
    assert pni == 115_000.0  # 100k + 20k - 5k
    vend = 130_000.0
    assert abs(_pct(vend, pni) - (130_000 - 115_000) / 115_000 * 100) < 1e-9


def test_window_cashflows_exclusive_start():
    cfs = [
        _CF(date=date(2026, 1, 1), amount=-10_000),
        _CF(date=date(2026, 1, 15), amount=-5_000),
        _CF(date=date(2026, 2, 1), amount=1_000),
    ]
    got = _window_cashflows(cfs, date(2026, 1, 1), date(2026, 2, 1))
    assert [c.date for c in got] == [date(2026, 1, 15), date(2026, 2, 1)]


def test_pct_zero_denominator():
    assert _pct(100.0, 0.0) is None
    assert _pct(100.0, -1.0) is None
