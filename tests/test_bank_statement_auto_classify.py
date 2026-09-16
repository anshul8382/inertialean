"""Unit tests for bank statement auto-classify / FD heads / suggestions."""

from collections import Counter
from types import SimpleNamespace

import pytest

from services.bank_statement_auto_classify_service import (
    DEFAULT_ACCOUNT_HEADS,
    _build_exact_and_patterns,
    _head_flow,
    _majority_with_margin,
    _normalize_text,
    _resolve_head_id,
    _tokenize,
    _txn_flow,
)

pytestmark = pytest.mark.no_app


def _txn(**kwargs):
    defaults = {
        "id": 1,
        "deposit_amount": 0,
        "withdrawal_amount": 0,
        "description": "",
        "description_sanitized": "",
        "income_expense_head_id": None,
        "income_expense_head": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _head(hid, name, head_type):
    return SimpleNamespace(id=hid, name=name, head_type=head_type, is_active=True)


def test_default_account_heads_include_fd_and_cloud():
    names = {(n, t) for n, t, _ in DEFAULT_ACCOUNT_HEADS}
    assert ("Withdrawal from FD", "credit") in names
    assert ("Investment in FD", "debit") in names
    assert ("Cloud Expenses", "debit") in names


def test_txn_flow_credit_and_debit():
    assert _txn_flow(_txn(deposit_amount=1000)) == "credit"
    assert _txn_flow(_txn(withdrawal_amount=500)) == "debit"
    assert _txn_flow(_txn(deposit_amount=100, withdrawal_amount=50)) is None


def test_exact_match_suggests_previous_head():
    head = _head(10, "Investment in FD", "debit")
    training = [
        _txn(
            id=1,
            withdrawal_amount=50000,
            description_sanitized="fd booking hdfc 123",
            income_expense_head_id=10,
            income_expense_head=head,
        ),
        _txn(
            id=2,
            withdrawal_amount=25000,
            description_sanitized="fd booking hdfc 123",
            income_expense_head_id=10,
            income_expense_head=head,
        ),
    ]
    exact_counts, patterns = _build_exact_and_patterns(training)
    by_flow = {"debit": [p for p in patterns if p["flow"] == "debit"]}
    active = {10: "debit"}

    candidate = _txn(
        id=99,
        withdrawal_amount=40000,
        description_sanitized="fd booking hdfc 123",
    )
    head_id, reason = _resolve_head_id(candidate, exact_counts, by_flow, active)
    assert reason == ""
    assert head_id == 10


def test_majority_requires_margin():
    assert _majority_with_margin(Counter({1: 2, 2: 2}), 1) is None
    assert _majority_with_margin(Counter({1: 3, 2: 1}), 1) == 1


def test_normalize_and_tokenize():
    assert _normalize_text("  FD Booking  ") == "fd booking"
    toks = _tokenize("fd booking hdfc bank")
    assert "fd" in toks
    assert "booking" in toks
    assert "the" not in _tokenize("the fd from bank")


def test_head_flow_aliases():
    assert _head_flow("income") == "credit"
    assert _head_flow("expense") == "debit"
    assert _head_flow("credit") == "credit"
