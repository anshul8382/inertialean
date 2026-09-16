import unittest
from datetime import date, datetime
from types import SimpleNamespace

from services.calendar_year_xirr_service import CalendarYearXirrService


class TestCalendarYearXirrService(unittest.TestCase):
    def test_valid_activity_dates_excludes_dummy_cashflows_and_transactions(self):
        cashflows = [
            SimpleNamespace(date=datetime(2000, 1, 1), amount=-1000),
            SimpleNamespace(date=datetime(2023, 5, 10), amount=-1000),
        ]
        transactions = [
            SimpleNamespace(transaction_date=datetime(2000, 1, 2), amount=1000),
            SimpleNamespace(transaction_date=datetime(2024, 2, 15), amount=1000),
        ]

        dates, counts = CalendarYearXirrService.valid_activity_dates(cashflows, transactions)

        self.assertEqual(dates, [date(2023, 5, 10), date(2024, 2, 15)])
        self.assertEqual(counts["dummy_cashflows_excluded"], 1)
        self.assertEqual(counts["dummy_transactions_excluded"], 1)

    def test_calendar_year_ranges_include_all_years_through_as_of(self):
        ranges = CalendarYearXirrService.calendar_year_ranges(
            first_activity_date=date(2022, 7, 1),
            as_of_date=date(2024, 4, 25),
        )

        self.assertEqual(
            ranges,
            [
                (2022, date(2022, 1, 1), date(2022, 12, 31)),
                (2023, date(2023, 1, 1), date(2023, 12, 31)),
                (2024, date(2024, 1, 1), date(2024, 4, 25)),
            ],
        )

    def test_benchmark_simulation_uses_same_cashflow_schedule(self):
        flows = [
            (datetime(2023, 1, 1), -1000.0, "Opening portfolio value"),
            (datetime(2023, 6, 1), -500.0, "Buy / investment"),
            (datetime(2023, 9, 1), 200.0, "Sell / withdrawal"),
        ]
        price_dates = [date(2023, 1, 1), date(2023, 6, 1), date(2023, 9, 1), date(2023, 12, 31)]
        prices = [100.0, 125.0, 100.0, 150.0]

        result = CalendarYearXirrService._simulate_benchmark(
            flows,
            end_date=date(2023, 12, 31),
            dates=price_dates,
            prices=prices,
        )

        self.assertTrue(result["success"])
        self.assertEqual(len(result["step_by_index"]), len(flows))
        self.assertEqual(result["skipped_cashflows"], 0)
        self.assertGreater(result["benchmark_value"], 0)


if __name__ == "__main__":
    unittest.main()
