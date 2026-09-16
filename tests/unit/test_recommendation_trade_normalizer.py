"""Unit tests for services/recommendation_trade_normalizer.py"""
import pytest

from services.recommendation_trade_normalizer import (
    apply_section1_signed_display,
    normalize_payload_rec,
    resolve_trade_action_and_quantity,
)

pytestmark = pytest.mark.no_app


def test_sell_row_gets_negative_quantity_for_display():
    row = apply_section1_signed_display({
        'action': 'SELL',
        'quantity': 100,
        'amount': -50000,
        'current_price': 500.0,
        'current_quantity': 200,
    })
    assert row['action'] == 'SELL'
    assert row['quantity'] == -100
    assert row['amount'] == -50000.0


def test_buy_row_stays_positive():
    row = apply_section1_signed_display({
        'action': 'BUY',
        'quantity': 50,
        'amount': 25000,
        'current_price': 500.0,
    })
    assert row['action'] == 'BUY'
    assert row['quantity'] == 50
    assert row['amount'] == 25000.0


def test_magnitude_quantity_with_negative_amount_is_sell():
    action, qty = resolve_trade_action_and_quantity(
        quantity=100,
        amount=-50000,
        target_price=500.0,
        action="SELL",
    )
    assert action == "SELL"
    assert qty == 100


def test_normalize_payload_rec_sell_with_positive_quantity():
    out = normalize_payload_rec({
        "action": "SELL",
        "quantity": 100,
        "amount": -50000,
        "target_price": 500.0,
        "current_quantity": 200,
        "new_quantity": 100,
    })
    assert out["action"] == "sell"
    assert out["quantity"] == 100


def test_normalize_payload_rec_buy_unchanged():
    out = normalize_payload_rec({
        "action": "BUY",
        "quantity": 50,
        "amount": 25000,
        "target_price": 500.0,
    })
    assert out["action"] == "buy"
    assert out["quantity"] == 50
