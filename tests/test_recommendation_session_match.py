"""Unit tests for session-first recommendation ↔ trade matching (no app/DB)."""

from datetime import datetime, timedelta

import pytest

from services.recommendation_session_match_service import (
    RecoSnap,
    TradeSnap,
    assign_unclaimed_trades_to_sent_sessions,
    claim_trades_for_executed,
    month_key_for_sent,
    pick_recorded_session_same_month,
    score_reco_trade,
    session_is_fully_unrecorded,
    superseded_session_message,
)

pytestmark = pytest.mark.no_app

JAN = datetime(2026, 1, 20, 10, 0, 0)
FEB = datetime(2026, 2, 5, 10, 0, 0)
WINDOW = 21


def _rec(**kwargs) -> RecoSnap:
    defaults = dict(
        id=1,
        session_id=10,
        security_id=1,
        symbol="RELIANCE",
        action="buy",
        quantity=100.0,
        target_price=1400.0,
        actual_price=None,
        status="sent",
        sent_at=JAN,
        executed_at=None,
        is_hold=False,
        session_name="Jan batch",
    )
    defaults.update(kwargs)
    return RecoSnap(**defaults)


def _trade(**kwargs) -> TradeSnap:
    defaults = dict(
        id=100,
        security_id=1,
        txn_type="BUY",
        quantity=50.0,
        price=1450.0,
        transaction_date=FEB,
    )
    defaults.update(kwargs)
    return TradeSnap(**defaults)


def test_later_fill_prefers_closer_qty_session_not_previous_leftover():
    leftover = _rec(id=1, session_id=10, quantity=100.0, sent_at=JAN, status="sent")
    current = _rec(id=2, session_id=20, quantity=50.0, sent_at=FEB, status="sent")
    fill = _trade(id=100, quantity=50.0, transaction_date=FEB + timedelta(days=1))
    assert score_reco_trade(leftover, fill, WINDOW) > 0
    assert score_reco_trade(current, fill, WINDOW) > score_reco_trade(leftover, fill, WINDOW)

    assigned = assign_unclaimed_trades_to_sent_sessions(
        [leftover, current], [fill], WINDOW, claimed_trade_ids=set()
    )
    assert assigned == {100: 20}


def test_executed_reco_claims_fill_so_previous_sent_line_cannot_g6():
    leftover = _rec(id=1, session_id=10, quantity=100.0, sent_at=JAN, status="sent")
    executed = _rec(
        id=2,
        session_id=20,
        quantity=50.0,
        sent_at=FEB,
        status="executed",
        executed_at=FEB + timedelta(days=1),
        actual_price=1450.0,
    )
    fill = _trade(id=100, quantity=50.0, transaction_date=FEB + timedelta(days=1))

    claimed = claim_trades_for_executed([executed], [fill], WINDOW)
    assert claimed == {100: 20}

    assigned = assign_unclaimed_trades_to_sent_sessions(
        [leftover], [fill], WINDOW, claimed_trade_ids=set(claimed)
    )
    assert assigned == {}


def test_hold_line_does_not_score_against_buy_trade():
    hold = _rec(id=3, action="hold", is_hold=True, quantity=10.0)
    fill = _trade()
    assert score_reco_trade(hold, fill, WINDOW) == 0.0


def test_fully_unrecorded_session_plus_recorded_peer_is_superseded():
    unused = [
        _rec(id=1, session_id=10, sent_at=JAN),
        _rec(id=2, session_id=10, security_id=2, symbol="TCS", sent_at=JAN),
    ]
    assert session_is_fully_unrecorded(unused, assigned_trade_ids_for_session=set())
    mk = month_key_for_sent(unused)
    executed = [
        _rec(
            id=9,
            session_id=20,
            sent_at=JAN + timedelta(days=8),
            status="executed",
            executed_at=JAN + timedelta(days=10),
            quantity=50.0,
        )
    ]
    peer = pick_recorded_session_same_month(10, mk, executed, claimed={})
    assert peer == 20
    assert mk == "2026-01"


def test_partial_session_is_not_fully_unrecorded():
    recs = [_rec(id=1, session_id=10), _rec(id=2, session_id=10, security_id=2, symbol="TCS")]
    assert not session_is_fully_unrecorded(recs, assigned_trade_ids_for_session={100})


def test_no_peer_session_same_month_is_not_superseded():
    unused = [_rec(id=1, session_id=10, sent_at=JAN)]
    mk = month_key_for_sent(unused)
    later = [
        _rec(
            id=9,
            session_id=20,
            sent_at=FEB,
            status="executed",
            executed_at=FEB + timedelta(days=1),
        )
    ]
    assert pick_recorded_session_same_month(10, mk, later, claimed={}) is None


def test_superseded_message_names_both_sessions():
    msg = superseded_session_message(
        "2026-01-20T10:00:00", "2026-01", "2026-01-28T10:00:00", 10, 20
    )
    assert "session 10 (sent 2026-01-20)" in msg
    assert "session 20 (sent 2026-01-28)" in msg
    assert "Confirm Session A was unused" in msg
