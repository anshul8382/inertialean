"""Unit tests for cashflow vs trade totals mismatch report helpers."""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from services.cashflow_trade_mismatch_report_service import (
    DEFAULT_TOLERANCE,
    SKIP_TRADE_DATE,
    _club_pair_for_day,
    _guess_for_day,
    _partition_day_deviations,
    apply_series_match_gate,
    build_match_explanation,
    build_resolution_guidance,
    classify_client_totals,
    materiality_threshold,
    net_cashflow_from_records,
    net_cashflow_from_trades,
    pair_day_nets,
)

pytestmark = pytest.mark.no_app


def _txn(ttype: str, amount: float, qty: float = None, price: float = None, tid: int = 1):
    return SimpleNamespace(
        id=tid,
        type=ttype,
        amount=amount,
        quantity=qty if qty is not None else 1,
        price=price if price is not None else amount,
        security_id=1,
        transaction_date=datetime(2024, 1, 15),
    )


def _cf(amount: float, cid: int = 1):
    return SimpleNamespace(
        id=cid,
        amount=amount,
        type="INFLOW" if amount < 0 else "OUTFLOW",
        description="test",
        date=datetime(2024, 1, 15),
    )


def test_trade_net_buy_sell_convention():
    trades = [_txn("BUY", 100_000), _txn("SELL", 40_000)]
    assert net_cashflow_from_trades(trades) == -60_000.0


def test_trade_net_ignores_split_bonus():
    trades = [_txn("BUY", 10_000), _txn("SPLIT", 0), _txn("BONUS", 0)]
    assert net_cashflow_from_trades(trades) == -10_000.0


def test_trade_net_falls_back_to_qty_price_when_amount_missing():
    t = SimpleNamespace(type="BUY", amount=None, quantity=10, price=150.0)
    assert net_cashflow_from_trades([t]) == -1500.0


def test_recorded_net_sum():
    assert net_cashflow_from_records([_cf(-100_000), _cf(25_000)]) == -75_000.0


def test_inflow_positive_amount_equals_negative_investment():
    pos_inflow = SimpleNamespace(amount=100_000, type="INFLOW")
    neg_inflow = SimpleNamespace(amount=-100_000, type="INFLOW")
    assert net_cashflow_from_records([pos_inflow]) == -100_000.0
    assert net_cashflow_from_records([pos_inflow]) == net_cashflow_from_records([neg_inflow])


def test_outflow_negative_amount_not_flipped_for_matching():
    """Abhinav-style auto BUY lines: negative amount mistagged OUTFLOW."""
    mistagged = SimpleNamespace(amount=-79_077, type="OUTFLOW")
    assert net_cashflow_from_records([mistagged]) == -79_077.0
    trades = net_cashflow_from_trades([_txn("BUY", 79_077)])
    assert net_cashflow_from_records([mistagged]) == trades


def test_inflow_positive_matches_buy_trade_net():
    recorded = net_cashflow_from_records([SimpleNamespace(amount=50_000, type="INFLOW")])
    trades = net_cashflow_from_trades([_txn("BUY", 50_000)])
    assert recorded == trades == -50_000.0


def test_pair_same_day_small_amount_slack():
    d = date(2026, 7, 1)
    explanations, left_cf, left_tx = pair_day_nets({d: -100_500.0}, {d: -100_000.0})
    assert explanations == []
    assert left_cf.get(d, 0) == 0
    assert left_tx.get(d, 0) == 0


def test_pair_tplus1_despite_other_activity_in_week():
    """T+1 pair must not be poisoned by another matched day in the ±14 window."""
    d_cf = date(2026, 7, 1)
    d_tx = date(2026, 7, 2)
    d_other = date(2026, 7, 10)
    explanations, left_cf, left_tx = pair_day_nets(
        {d_cf: -100_000.0, d_other: -50_000.0},
        {d_tx: -100_000.0, d_other: -50_000.0},
    )
    assert all(abs(v) < 1 for v in left_cf.values())
    assert all(abs(v) < 1 for v in left_tx.values())
    assert any("2026-07-01" in (e.get("cashflow_date") or "") for e in explanations)


def test_club_nearest_neighbour_not_window_sum():
    """₹1L CF on D vs ₹1L trade on D+1 must club even if another trade sits in ±14d."""
    d = date(2026, 7, 1)
    cf_by = {d: [_cf(-100_000, cid=1)]}
    tx_by = {
        date(2026, 7, 2): [_txn("BUY", 100_000, tid=2)],
        date(2026, 7, 10): [_txn("BUY", 50_000, tid=3)],
    }
    hit = _club_pair_for_day(d, "orphan_cashflow", cf_by, tx_by, 1000.0)
    assert hit is not None
    assert hit["trade_dates"] == ["2026-07-02"]
    assert abs(hit["trade_net"] - (-100_000)) < 1


def test_match_within_tolerance():
    recorded = net_cashflow_from_records([_cf(-100_000), _cf(5_000)])
    trades = net_cashflow_from_trades([_txn("BUY", 100_000), _txn("SELL", 5_000)])
    assert abs(recorded - trades) == 0
    assert abs(recorded - trades) <= DEFAULT_TOLERANCE


def test_mismatch_above_tolerance():
    recorded = net_cashflow_from_records([_cf(-50_000)])
    trades = net_cashflow_from_trades([_txn("BUY", 100_000)])
    assert abs(recorded - trades) == 50_000.0
    assert abs(recorded - trades) > DEFAULT_TOLERANCE


def test_skip_date_constant():
    assert SKIP_TRADE_DATE == date(2000, 1, 1)


def test_guess_orphan_cashflow():
    msg = _guess_for_day("orphan_cashflow", -5000.0, [_cf(-5000, cid=99)], [])
    assert "99" in msg
    assert "no matching BUY/SELL" in msg


def test_guess_missing_cashflow():
    msg = _guess_for_day("missing_cashflow", 10000.0, [], [_txn("BUY", 10000, tid=7)])
    assert "7" in msg
    assert "expected cashflow amount -10,000.00" in msg


def test_materiality_threshold_pct_and_floor():
    assert materiality_threshold(50_000) == 1000.0  # floor
    assert materiality_threshold(500_000) == 5000.0  # 1%


def test_classify_matched_no_opening():
    r = classify_client_totals(
        recorded_net=-100_000,
        opening_trade_net=0,
        post_cutoff_trade_net=-100_000,
        has_opening_book=False,
    )
    assert r["status"] == "matched"


def test_classify_ignore_opening_book_when_post_ok():
    r = classify_client_totals(
        recorded_net=-500_000,
        opening_trade_net=-200_000,
        post_cutoff_trade_net=-500_000,
        has_opening_book=True,
    )
    assert r["status"] == "ignore_opening_book"


def test_classify_mismatch_material():
    r = classify_client_totals(
        recorded_net=-100_000,
        opening_trade_net=0,
        post_cutoff_trade_net=-50_000,
        has_opening_book=False,
    )
    assert r["status"] == "mismatch_material"
    assert r["abs_difference"] == 50_000.0


def test_match_explanation_mentions_band_and_clubbing():
    text = build_match_explanation(
        status="matched",
        recorded_net=-731_007,
        post_cutoff_trade_net=-731_461,
        difference=454,
        materiality_threshold=7310,
        clubbed_explanations=[{"summary": "x"}],
        suspect_day_count=8,
        large_day_count=0,
        small_day_count=7,
    )
    assert "Matched on both lifetime net and series" in text
    assert "within the allowed band" in text
    assert "Clubbing" in text or "clubbing" in text.lower()


def test_series_gate_downgrades_matched_when_large_days():
    assert apply_series_match_gate("matched", 0) == "matched"
    assert apply_series_match_gate("matched", 2) == "mismatch_review"
    assert apply_series_match_gate("ignore_opening_book", 1) == "mismatch_review"
    assert apply_series_match_gate("mismatch_material", 5) == "mismatch_material"


def test_match_explanation_series_review():
    text = build_match_explanation(
        status="mismatch_review",
        recorded_net=-1_000_000,
        post_cutoff_trade_net=-1_000_000,
        difference=0,
        materiality_threshold=10_000,
        large_day_count=1,
        small_day_count=0,
        chronological={
            "matched_through_date": "2025-01-01",
            "first_break_date": "2026-01-10",
            "matched_through_cashflow_net": -100000,
            "matched_through_trade_net": -100000,
        },
    )
    assert "Series review" in text or "bad-apple" in text
    assert "2026-01-10" in text


def test_clubbing_requires_amount_not_just_nearby_dates():
    """₹10L cashflow vs ₹1L trades in ±14d is NOT clubbing."""
    d = date(2026, 7, 1)
    cf_by = {d: [_cf(-1_000_000, cid=1)]}
    tx_by = {
        date(2026, 7, 5): [_txn("BUY", 100_000, tid=2)],
    }
    assert _club_pair_for_day(d, "orphan_cashflow", cf_by, tx_by, 1000.0) is None


def test_clubbing_ok_when_window_amounts_align():
    d = date(2026, 7, 1)
    cf_by = {d: [_cf(-1_000_000, cid=1)]}
    tx_by = {
        date(2026, 7, 3): [_txn("BUY", 400_000, tid=2)],
        date(2026, 7, 8): [_txn("BUY", 600_000, tid=3)],
    }
    hit = _club_pair_for_day(d, "orphan_cashflow", cf_by, tx_by, 1000.0)
    assert hit is not None
    assert hit["trade_count"] == 2
    assert abs(hit["trade_net"] - (-1_000_000)) <= 10_000


def test_partition_large_day_vs_small_hint():
    large, small = _partition_day_deviations(
        [
            {
                "date": "2026-07-01",
                "day_difference": -900_000,
                "cashflow_net": -1_000_000,
                "trade_net": -100_000,
                "issue": "amount_mismatch",
            },
            {
                "date": "2026-07-15",
                "day_difference": -500,
                "cashflow_net": -5_000,
                "trade_net": -4_500,
                "issue": "amount_mismatch",
            },
        ],
        lifetime_threshold=7_310,
    )
    assert len(large) == 1
    assert large[0]["severity"] == "review"
    assert len(small) == 1
    assert small[0]["severity"] == "hint"


def test_resolution_guidance_missing_cashflow_explains_gap():
    guidance = build_resolution_guidance(
        client_id=127,
        status="mismatch_material",
        difference=16_000,
        materiality_threshold=3_421,
        large_days=[
            {
                "date": "2026-01-10",
                "issue": "missing_cashflow",
                "day_difference": 16_000,
                "cashflow_net": 0,
                "trade_net": -16_000,
                "transaction_ids": [52072, 52073, 52071],
                "cashflow_ids": [],
                "guess": "Trade(s) have no cashflow row",
            }
        ],
        small_days=[],
        clubbed_explanations=[],
        suspect_trades=[
            {
                "id": 52072,
                "date": "2026-01-10",
                "type": "BUY",
                "amount": 5000,
                "signed_cashflow_effect": -5000,
            }
        ],
        suspect_cashflows=[],
        nearby_cashflow_candidates=[],
    )
    assert guidance["explains_lifetime_gap"] is True
    assert guidance["source"]["code"] == "missing_advisor_cashflow"
    assert "missing advisor cashflow" in guidance["source"]["label"].lower()
    assert len(guidance["check_exactly"]) >= 3
    assert guidance["check_exactly"][0]["title"].startswith("Confirm the trades")
    titles = [o["title"] for o in guidance["fix_options"]]
    assert any("missing advisor cashflow" in t.lower() or "Add the missing" in t for t in titles)


def test_opening_book_epoch_excludes_pre_first_real_cashflows():
    """Manish-style: 2000-01-01 trades + CF in 2017 must not create orphan before first real trade."""
    from services.cashflow_trade_mismatch_report_service import (
        SKIP_TRADE_DATE,
        classify_client_totals,
        first_real_trade_date,
        split_cashflows_at_reconcile_start,
    )

    trades = [
        _txn("BUY", 5_000_000, tid=1),
        SimpleNamespace(
            id=2,
            type="BUY",
            amount=100_000,
            quantity=1,
            price=100_000,
            security_id=1,
            transaction_date=datetime(2023, 9, 4),
        ),
    ]
    # Force opening book date on first txn
    trades[0].transaction_date = datetime(SKIP_TRADE_DATE.year, 1, 1)

    fr = first_real_trade_date(trades)
    assert fr == date(2023, 9, 4)

    cfs = [
        SimpleNamespace(id=11824, amount=-740_000, date=date(2017, 5, 9)),
        SimpleNamespace(id=99, amount=-100_000, date=date(2023, 9, 4)),
    ]
    pre, post = split_cashflows_at_reconcile_start(
        cfs, reconcile_start=fr, has_opening_book=True
    )
    assert [c.id for c in pre] == [11824]
    assert [c.id for c in post] == [99]

    classified = classify_client_totals(
        recorded_net=-840_000,
        opening_trade_net=-5_000_000,
        post_cutoff_trade_net=-100_000,
        has_opening_book=True,
        post_cutoff_cashflow_net=-100_000,
        opening_epoch_cashflow_net=-740_000,
        first_real_trade_date_iso="2023-09-04",
    )
    assert classified["status"] == "ignore_opening_book"
    assert classified["difference"] == 0.0
    assert classified["reconcile_start_date"] == "2023-09-04"


def test_opening_book_resolution_mentions_epoch():
    from services.cashflow_trade_mismatch_report_service import build_resolution_guidance

    guidance = build_resolution_guidance(
        client_id=1,
        status="mismatch_material",
        difference=50_000,
        materiality_threshold=5_000,
        large_days=[
            {
                "date": "2023-10-01",
                "issue": "missing_cashflow",
                "day_difference": 50_000,
                "cashflow_net": 0,
                "trade_net": -50_000,
                "transaction_ids": [1],
                "cashflow_ids": [],
            }
        ],
        small_days=[],
        clubbed_explanations=[],
        suspect_trades=[],
        suspect_cashflows=[],
        nearby_cashflow_candidates=[],
        has_opening_book=True,
        reconcile_start_date="2023-09-04",
        opening_epoch_cashflow_net=-740_000,
    )
    assert "opening-epoch" in guidance["finding"].lower() or "Opening book" in guidance["finding"]
    titles = [c["title"] for c in guidance["check_exactly"]]
    assert any("Opening book" in t for t in titles)


def test_chrono_with_clubbing_does_not_false_break():
    """CF on day1 + matching trades within ±14d must not create first_break after clubbing."""
    from services.cashflow_trade_mismatch_report_service import (
        build_chronological_reconcile,
        _club_pair_for_day,
    )

    d_cf = date(2026, 7, 1)
    d_tx = date(2026, 7, 8)
    cf_by = {d_cf: [_cf(-100_000, cid=1)]}
    tx_by = {d_tx: [_txn("BUY", 100_000, tid=2)]}
    pair = _club_pair_for_day(d_cf, "orphan_cashflow", cf_by, tx_by, 1000.0)
    assert pair is not None

    raw = build_chronological_reconcile(cf_by, tx_by)
    assert raw["first_break_date"] is not None  # temporary skew without clubbing

    adj = build_chronological_reconcile(cf_by, tx_by, clubbed_explanations=[pair])
    assert adj["first_break_date"] is None
    assert adj["fully_matched"] is True
    assert adj.get("clubbing_applied") is True


def test_partition_bad_apples_keeps_recent_suffix_healthy():
    from services.cashflow_trade_mismatch_report_service import partition_bad_apples

    large = [
        {"date": "2025-01-01", "day_difference": -50_000},
        {"date": "2026-01-10", "day_difference": 16_000},
    ]
    # From-recent: matched from 2025-10-16 → older large days are bad apples
    healthy, bad = partition_bad_apples(large, matched_from_recent_date="2025-10-16")
    assert [r["date"] for r in healthy] == ["2026-01-10"]
    assert [r["date"] for r in bad] == ["2025-01-01"]


def test_chrono_from_recent_finds_suffix_match():
    """Recent matching days stay in band; an older mismatch should not poison the suffix."""
    from services.cashflow_trade_mismatch_report_service import build_chronological_reconcile

    d_old = date(2024, 1, 1)
    d_mid = date(2025, 6, 1)
    d_new = date(2026, 1, 1)
    # Older day: CF only (orphan) — large gap if counted from start
    # Recent two days: CF and trades cancel
    cf_by = {
        d_old: [_cf(-500_000, cid=1)],
        d_mid: [_cf(-100_000, cid=2)],
        d_new: [_cf(40_000, cid=3)],
    }
    tx_by = {
        d_mid: [_txn("BUY", 100_000, tid=10)],
        d_new: [_txn("SELL", 40_000, tid=11)],
    }
    chrono = build_chronological_reconcile(cf_by, tx_by)
    assert chrono.get("scan_direction") == "from_recent"
    assert chrono.get("matched_from_recent_date") == "2025-06-01"
    assert chrono.get("fully_matched") is False
    assert chrono.get("first_break_date") == "2024-01-01"
