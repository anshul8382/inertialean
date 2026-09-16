"""Tests for guided chat routing and cashflow-trade classifier helpers."""

import pytest

from services.cashflow_trade_mismatch_report_service import classify_client_totals
from services.guided_case_chat_service import route_module

pytestmark = pytest.mark.no_app


def test_route_auto_cashflow_keywords():
    mid, clarify = route_module(module_id="auto", message="Why do cashflows and trades mismatch?")
    assert clarify is None
    assert mid == "cashflow_trade_integrity"


def test_route_ambiguous_asks_clarify():
    mid, clarify = route_module(module_id="auto", message="hello")
    assert mid is None
    assert clarify is not None
    assert clarify["primary_action"]["type"] == "clarify_module"


def test_route_explicit_module():
    mid, clarify = route_module(module_id="cashflow_trade_integrity", message="anything")
    assert clarify is None
    assert mid == "cashflow_trade_integrity"


def test_opening_book_classify():
    r = classify_client_totals(
        recorded_net=-1_000_000,
        opening_trade_net=-400_000,
        post_cutoff_trade_net=-1_000_000,
        has_opening_book=True,
    )
    assert r["status"] == "ignore_opening_book"
