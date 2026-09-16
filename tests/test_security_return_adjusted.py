import unittest
from datetime import date
from unittest.mock import patch


class DummyPeriodData:
    """Minimal valid data collector stub for PeriodEnhancedAnalyticsCalculator tests."""

    is_valid = True

    def __init__(self):
        self.client_id = 1
        self.start_date = date(2025, 1, 1)
        self.end_date = date(2025, 7, 1)

        # Simulate a 5:1 split between start and end:
        # - Unadjusted historical prices: 1000 -> 200 (looks like -80%)
        # - Adjusted prices should normalize the series (e.g., 200 -> 200 => 0%)
        self.start_portfolio = {
            "holdings": [
                {
                    "security_id": 101,
                    "symbol": "THYROCARE",
                    "quantity": 10.0,
                    "average_price": 900.0,
                    "current_price": 1000.0,  # unadjusted start price (pre-split basis)
                    "current_value": 10000.0,
                    "total_cost": 9000.0,
                }
            ]
        }
        self.end_portfolio = {
            "holdings": [
                {
                    "security_id": 101,
                    "symbol": "THYROCARE",
                    "quantity": 50.0,  # post-split quantity
                    "average_price": 180.0,  # post-split avg cost per share
                    "current_price": 200.0,  # unadjusted end price (post-split basis)
                    "current_value": 10000.0,
                    "total_cost": 9000.0,
                }
            ]
        }


class _FakePriceData:
    def __init__(self, price: float):
        self.price = price
        self.is_valid = True


class TestSecurityReturnAdjusted(unittest.TestCase):
    def test_holdings_comparison_includes_adjusted_price_return(self):
        # Import inside the test to avoid side-effects at module import time
        from api.v2.period_enhanced_analytics import PeriodEnhancedAnalyticsCalculator

        data = DummyPeriodData()
        calc = PeriodEnhancedAnalyticsCalculator(data)

        # Force adjusted price series to be normalized (no change across the split)
        def fake_get_price(security_id, target_date, use_adjusted=False, allow_fallback=True):
            self.assertTrue(use_adjusted)  # the new code should request adjusted prices
            self.assertEqual(security_id, 101)
            return _FakePriceData(200.0)

        with patch("services.price_service.PriceService.get_price", side_effect=fake_get_price):
            rows = calc._calculate_portfolio_holdings_comparison(warnings_list=[])

        self.assertEqual(len(rows), 1)
        row = rows[0]

        # Unadjusted return is distorted by the split: 1000 -> 200 = -80%
        self.assertAlmostEqual(row["price_return_percent_unadjusted"], -80.0, places=6)
        self.assertAlmostEqual(row["change"]["price_return_percent_unadjusted"], -80.0, places=6)

        # Adjusted return should be normalized: 200 -> 200 = 0%
        self.assertAlmostEqual(row["price_return_percent_adjusted"], 0.0, places=6)
        self.assertAlmostEqual(row["change"]["price_return_percent_adjusted"], 0.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)





