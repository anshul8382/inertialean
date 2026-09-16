import unittest
from datetime import date
from types import SimpleNamespace

import pytest

from services.period_performer_return_service import (
    compute_holder_period_price_return,
    filter_period_buy_txns_for_holder_return,
    should_use_exit_date_for_end_price,
    vwap_from_transactions_ca_adjusted,
)


@pytest.mark.no_app
class TestComputeHolderPeriodPriceReturn(unittest.TestCase):
    def test_held_throughout_uses_period_start_market(self):
        result = compute_holder_period_price_return(
            period_start_market=100.0,
            period_end_market=90.0,
            start_qty=100.0,
            period_buy_qty=0.0,
            period_buy_vwap=0.0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["return_basis"], "period_start_market")
        self.assertEqual(result["start_price"], 100.0)
        self.assertAlmostEqual(result["price_percent_change"], -10.0)

    def test_new_position_uses_period_buy_vwap(self):
        """Stock fell before purchase: market 100→90 but buy at 80 → +12.5% on holding."""
        result = compute_holder_period_price_return(
            period_start_market=100.0,
            period_end_market=90.0,
            start_qty=0.0,
            period_buy_qty=10.0,
            period_buy_vwap=80.0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["return_basis"], "period_purchase_vwap")
        self.assertEqual(result["start_price"], 80.0)
        self.assertAlmostEqual(result["price_percent_change"], 12.5)

    def test_increased_position_uses_blended_start(self):
        result = compute_holder_period_price_return(
            period_start_market=100.0,
            period_end_market=90.0,
            start_qty=100.0,
            period_buy_qty=50.0,
            period_buy_vwap=80.0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["return_basis"], "blended")
        expected_start = (100 * 100 + 50 * 80) / 150
        self.assertAlmostEqual(result["start_price"], expected_start)
        self.assertAlmostEqual(
            result["price_percent_change"],
            ((90 - expected_start) / expected_start) * 100,
        )

    def test_missing_end_price_returns_none(self):
        self.assertIsNone(
            compute_holder_period_price_return(
                period_start_market=100.0,
                period_end_market=0.0,
                start_qty=10.0,
                period_buy_qty=0.0,
                period_buy_vwap=0.0,
            )
        )

    def test_exit_end_price_when_flat_at_period_end(self):
        self.assertTrue(should_use_exit_date_for_end_price(0.0, [object()]))
        self.assertFalse(should_use_exit_date_for_end_price(10.0, [object()]))
        self.assertFalse(should_use_exit_date_for_end_price(0.0, []))

    def test_exited_return_uses_exit_market_not_period_end(self):
        """Held at start, sold mid-period: return uses exit market, not period-end market."""
        result = compute_holder_period_price_return(
            period_start_market=100.0,
            period_end_market=95.0,  # exit-date adjusted price
            start_qty=50.0,
            period_buy_qty=0.0,
            period_buy_vwap=0.0,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["end_price"], 95.0)
        self.assertAlmostEqual(result["price_percent_change"], -5.0)


@pytest.mark.no_app
class TestCaAdjustedBuyVwap(unittest.TestCase):
    def test_filter_excludes_start_date_buys_when_held_at_start(self):
        buys = [
            SimpleNamespace(transaction_date=date(2025, 4, 9), quantity=35, price=2051.55),
            SimpleNamespace(transaction_date=date(2025, 5, 14), quantity=20, price=2086.40),
            SimpleNamespace(transaction_date=date(2025, 6, 10), quantity=15, price=2154.00),
        ]
        filtered = filter_period_buy_txns_for_holder_return(
            buys, date(2025, 4, 9), start_qty=35.0
        )
        self.assertEqual(len(filtered), 2)
        self.assertEqual([t.quantity for t in filtered], [20, 15])

    def test_filter_keeps_all_buys_when_new_position(self):
        buys = [
            SimpleNamespace(transaction_date=date(2025, 4, 9), quantity=35, price=2051.55),
            SimpleNamespace(transaction_date=date(2025, 5, 14), quantity=20, price=2086.40),
        ]
        filtered = filter_period_buy_txns_for_holder_return(
            buys, date(2025, 4, 9), start_qty=0.0
        )
        self.assertEqual(len(filtered), 2)

    def test_vwap_restates_pre_split_buys_for_5_to_1_split(self):
        """Kotak-style: pre-split buys must be ÷5 before blending with post-split end."""
        buys = [
            SimpleNamespace(transaction_date=date(2025, 5, 14), quantity=20, price=2086.40),
            SimpleNamespace(transaction_date=date(2025, 6, 10), quantity=15, price=2154.00),
        ]
        actions = [
            SimpleNamespace(
                action_type="SPLIT",
                ratio=5.0,
                action_date=date(2026, 1, 14),
            )
        ]
        qty, vwap = vwap_from_transactions_ca_adjusted(
            buys,
            security_id=1,
            through_date=date(2026, 7, 15),
            corporate_actions=actions,
        )
        self.assertEqual(qty, 35.0)
        raw_vwap = (20 * 2086.40 + 15 * 2154.00) / 35
        self.assertAlmostEqual(vwap, raw_vwap / 5.0, places=4)

    def test_blended_return_with_ca_adjusted_vwap_not_catastrophic(self):
        """After CA restatement, Kotak-like blend should be mild loss, not ~-75%."""
        start_market = 2052.70 / 5.0  # restated
        buy_vwap = ((20 * 2086.40 + 15 * 2154.00) / 35) / 5.0
        result = compute_holder_period_price_return(
            period_start_market=start_market,
            period_end_market=378.0,
            start_qty=35.0,
            period_buy_qty=35.0,
            period_buy_vwap=buy_vwap,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["return_basis"], "blended")
        # ~416 start vs 378 end → roughly -9%, nowhere near -75%
        self.assertGreater(result["price_percent_change"], -20.0)
        self.assertLess(result["price_percent_change"], 0.0)
        # Must not reproduce the old ~1526 artefact
        self.assertLess(result["start_price"], 500.0)


if __name__ == "__main__":
    unittest.main()
