"""FR-WF-RECO-01: multi-asset editor helpers (no app/DB)."""

import pytest

from services.recommendation_multi_asset_service import (
    asset_classes_from_record_payload,
    default_action_for_class_change,
    future_weight_within_class,
    keep_security_ids_from_payload,
    merge_section1_recommendations,
    parse_selected_asset_classes,
    signed_section1_sum,
    weight_within_class,
)
from utils.asset_allocation import action_from_required_change

pytestmark = pytest.mark.no_app


def test_weight_within_class_not_portfolio():
    # 10 of 50 in Equity = 20% of Equity, not 10/200 = 5% of portfolio
    assert weight_within_class(10, 50) == pytest.approx(20.0)
    assert weight_within_class(10, 0) == 0.0
    assert weight_within_class(0, 50) == 0.0


def test_future_weight_uses_class_denominator():
    # current 10, buy 10, class 50 + 20 investment → 20/70
    assert future_weight_within_class(10, 10, 50, 20) == pytest.approx(20 / 70 * 100)


def test_merge_add_security_keeps_existing_rows():
    existing = [
        {"security_id": 1, "asset_class": "Equity", "amount": 1000},
        {"security_id": 2, "asset_class": "Fixed Income", "amount": -500},
    ]
    incoming = [{"security_id": 3, "asset_class": "Equity", "amount": 200}]
    merged = merge_section1_recommendations(existing, incoming, selected_classes=["Equity"])
    ids = {r["security_id"] for r in merged}
    assert ids == {1, 2, 3}
    fi = next(r for r in merged if r["security_id"] == 2)
    assert fi["amount"] == -500


def test_merge_does_not_drop_unchecked_when_not_replacing():
    existing = [
        {"security_id": 1, "asset_class": "Equity", "amount": 1000},
        {"security_id": 2, "asset_class": "Equity", "amount": 400},
    ]
    incoming = [{"security_id": 3, "asset_class": "Equity", "amount": 50}]
    merged = merge_section1_recommendations(
        existing, incoming, selected_classes=["Equity"], replace_selected=False
    )
    assert {r["security_id"] for r in merged} == {1, 2, 3}


def test_merge_replace_selected_keeps_other_classes():
    existing = [
        {"security_id": 1, "asset_class": "Equity", "amount": 1000},
        {"security_id": 2, "asset_class": "Fixed Income", "amount": -500},
    ]
    incoming = [{"security_id": 9, "asset_class": "Equity", "amount": 100}]
    merged = merge_section1_recommendations(
        existing, incoming, selected_classes=["Equity"], replace_selected=True
    )
    ids = {r["security_id"] for r in merged}
    assert ids == {9, 2}


def test_merge_replace_selected_drops_deselected_in_multi_class():
    """Save/Record checked-only: drop unchecked Equity + FI, keep untouched Gold."""
    existing = [
        {"security_id": 1, "asset_class": "Equity", "amount": 1000},
        {"security_id": 2, "asset_class": "Equity", "amount": 400},
        {"security_id": 3, "asset_class": "Fixed Income", "amount": -500},
        {"security_id": 4, "asset_class": "Gold", "amount": 200},
    ]
    incoming = [
        {"security_id": 1, "asset_class": "Equity", "amount": 1000},
        # 2 deselected Equity; 3 deselected FI
    ]
    merged = merge_section1_recommendations(
        existing,
        incoming,
        selected_classes=["Equity", "Fixed Income"],
        replace_selected=True,
    )
    assert {r["security_id"] for r in merged} == {1, 4}


def test_asset_classes_from_record_payload_includes_row_classes():
    # Top-level asset_class is only Equity (legacy UI), but payload has FI rows too
    recs = [
        {"security_id": 1, "asset_class": "Equity"},
        {"security_id": 2, "asset_class": "Debt"},
    ]
    classes = asset_classes_from_record_payload(recs, "Equity")
    assert classes == ["Equity", "Fixed Income"]
    assert keep_security_ids_from_payload(recs) == {1, 2}
    assert keep_security_ids_from_payload([{"security_id": "x"}]) == set()


def test_parse_selected_asset_classes_aliases():
    assert parse_selected_asset_classes("Equity,Debt") == ["Equity", "Fixed Income"]
    assert parse_selected_asset_classes(["Gold"], "Gold,REITs") == ["Gold", "REITs"]


def test_signed_sum_and_hold_action():
    rows = [
        {"amount": 1000},
        {"amount": -250},
    ]
    assert signed_section1_sum(rows) == 750
    assert default_action_for_class_change(0) == "HOLD"
    assert action_from_required_change(0) == "HOLD"
    assert default_action_for_class_change(500) == "BUY"
