import unittest
from datetime import datetime, date
from unittest.mock import MagicMock, patch

from api.v2.period_analysis import validate_and_parse_request


class _DummyRequest:
    def __init__(self, args):
        self.args = args


class TestPeriodAnalysisV2LifetimeBaseline(unittest.TestCase):
    def test_lifetime_start_date_is_shifted_back_one_day(self):
        """
        Lifetime periods omit start_date; we derive it.

        If we start exactly on the first activity date, the first cashflow/BUY can be reflected in both:
        - start portfolio value, and
        - net cashflow (period cashflows),
        which causes double counting for accumulated profit metrics (End − Start − NetCashflow).

        So we shift lifetime start_date back by 1 day to create a true opening baseline (start value ~ 0).
        """
        req = _DummyRequest(
            {
                "end_date": "2025-12-31",
                "analysis_type": "ALL",
                # no start_date
            }
        )

        earliest_cf = MagicMock()
        earliest_cf.date = datetime(2025, 1, 10, 10, 0, 0)

        earliest_txn = MagicMock()
        earliest_txn.transaction_date = datetime(2025, 1, 15, 10, 0, 0)

        with patch("api.v2.period_analysis.Cashflow") as Cashflow, patch("api.v2.period_analysis.Transaction") as Transaction:
            # Cashflow.query.filter_by(...).order_by(...).first() -> earliest_cf
            Cashflow.query.filter_by.return_value.order_by.return_value.first.return_value = earliest_cf
            # Transaction.query.filter_by(...).order_by(...).first() -> earliest_txn
            Transaction.query.filter_by.return_value.order_by.return_value.first.return_value = earliest_txn

            start_date, end_date, basis, matching, analysis_type, prev_period_start_date = validate_and_parse_request(1, req)

        self.assertEqual(start_date, date(2025, 1, 9))
        self.assertEqual(end_date, date(2025, 12, 31))
        self.assertEqual(analysis_type, "ALL")
        self.assertIsNone(prev_period_start_date)






