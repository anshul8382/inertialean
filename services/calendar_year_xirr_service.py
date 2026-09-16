"""
Calendar-year portfolio XIRR compared with Nifty.

This follows the app's signed cashflow convention:
- opening portfolio value and buys/investments are negative cashflows,
- sells/withdrawals and ending portfolio value are positive cashflows.
"""
from __future__ import annotations

import logging
from bisect import bisect_right
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from models import Benchmark, BenchmarkData, Cashflow, Transaction
from services.cashflow_service import DUMMY_DATES
from services.forward_holding_calculation_service import get_client_portfolio_by_date

logger = logging.getLogger(__name__)


class CalendarYearXirrService:
    """Calculate calendar-year portfolio XIRR rows for a client."""

    @staticmethod
    def _as_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if hasattr(value, "date"):
            return value.date()
        try:
            return datetime.fromisoformat(str(value)).date()
        except Exception:
            return None

    @classmethod
    def valid_activity_dates(
        cls,
        cashflows: Iterable[Any],
        transactions: Iterable[Any],
    ) -> Tuple[List[date], Dict[str, int]]:
        dates: List[date] = []
        skipped_cashflows = 0
        skipped_transactions = 0

        for cf in cashflows:
            cf_date = cls._as_date(getattr(cf, "date", None))
            if not cf_date:
                continue
            if cf_date in DUMMY_DATES:
                skipped_cashflows += 1
                continue
            dates.append(cf_date)

        for txn in transactions:
            txn_date = cls._as_date(getattr(txn, "transaction_date", None))
            if not txn_date:
                continue
            if txn_date in DUMMY_DATES:
                skipped_transactions += 1
                continue
            dates.append(txn_date)

        return sorted(dates), {
            "dummy_cashflows_excluded": skipped_cashflows,
            "dummy_transactions_excluded": skipped_transactions,
        }

    @staticmethod
    def calendar_year_ranges(first_activity_date: date, as_of_date: date) -> List[Tuple[int, date, date]]:
        ranges: List[Tuple[int, date, date]] = []
        for year in range(first_activity_date.year, as_of_date.year + 1):
            start_date = date(year, 1, 1)
            end_date = min(date(year, 12, 31), as_of_date)
            if end_date >= first_activity_date or year > first_activity_date.year:
                ranges.append((year, start_date, end_date))
        return ranges

    @classmethod
    def _real_cashflows_in_period(
        cls,
        cashflows: Sequence[Cashflow],
        start_date: date,
        end_date: date,
    ) -> List[Cashflow]:
        out: List[Cashflow] = []
        for cf in cashflows:
            cf_date = cls._as_date(cf.date)
            if not cf_date or cf_date in DUMMY_DATES:
                continue
            if start_date <= cf_date <= end_date:
                out.append(cf)
        return out

    @staticmethod
    def _portfolio_value(client_id: int, target_date: date) -> float:
        try:
            portfolio = get_client_portfolio_by_date(client_id, target_date)
            return float(portfolio.get("total_value", 0.0) or 0.0)
        except Exception as exc:
            logger.warning(
                "Calendar XIRR: portfolio valuation failed for client=%s date=%s: %s",
                client_id,
                target_date,
                exc,
            )
            return 0.0

    @classmethod
    def _load_benchmark_prices(
        cls,
        benchmark_id: int,
        start_date: date,
        end_date: date,
    ) -> Tuple[Optional[Benchmark], List[date], List[float]]:
        benchmark = Benchmark.query.filter_by(id=benchmark_id).first()
        if not benchmark:
            return None, [], []

        rows = (
            BenchmarkData.query.filter_by(benchmark_id=benchmark_id)
            .filter(BenchmarkData.date >= start_date)
            .filter(BenchmarkData.date <= end_date)
            .order_by(BenchmarkData.date)
            .all()
        )

        dates: List[date] = []
        prices: List[float] = []
        for row in rows:
            row_date = cls._as_date(row.date)
            if not row_date or row.price is None:
                continue
            try:
                price = float(row.price)
            except Exception:
                continue
            if price <= 0:
                continue
            dates.append(row_date)
            prices.append(price)

        return benchmark, dates, prices

    @staticmethod
    def _price_on_or_before(
        target_date: date,
        dates: Sequence[date],
        prices: Sequence[float],
    ) -> Tuple[Optional[float], Optional[date]]:
        idx = bisect_right(dates, target_date) - 1
        if idx >= 0:
            return prices[idx], dates[idx]
        return None, None

    @classmethod
    def _can_calculate_xirr(cls, flows: Sequence[Tuple[Any, float]], terminal_value: float) -> bool:
        amounts = [float(amount) for _, amount in flows]
        if terminal_value:
            amounts.append(float(terminal_value))
        return any(amount < 0 for amount in amounts) and any(amount > 0 for amount in amounts)

    @classmethod
    def _simulate_benchmark(
        cls,
        flows: Sequence[Tuple[Any, float, str]],
        end_date: date,
        dates: Sequence[date],
        prices: Sequence[float],
    ) -> Dict[str, Any]:
        units = 0.0
        skipped = 0
        step_by_index: Dict[int, Dict[str, Any]] = {}

        for index, (flow_date_raw, amount_raw, label) in enumerate(flows):
            flow_date = cls._as_date(flow_date_raw)
            if not flow_date:
                skipped += 1
                continue
            amount = float(amount_raw)
            price, price_date = cls._price_on_or_before(flow_date, dates, prices)
            if not price or price <= 0:
                skipped += 1
                step_by_index[index] = {
                    "benchmark_price": None,
                    "benchmark_price_date": None,
                    "benchmark_units_change": 0.0,
                    "benchmark_units_after": units,
                    "benchmark_note": "No Nifty price on or before this date.",
                }
                continue

            if amount < 0:
                units_change = abs(amount) / price
                units += units_change
                status = "buy_units"
            elif amount > 0 and units > 0:
                units_change = -min(amount / price, units)
                units += units_change
                status = "sell_units"
            else:
                units_change = 0.0
                status = "ignored_no_units"

            step_by_index[index] = {
                "benchmark_price": price,
                "benchmark_price_date": price_date.isoformat() if price_date else None,
                "benchmark_units_change": units_change,
                "benchmark_units_after": units,
                "benchmark_status": status,
                "benchmark_note": label,
            }

        end_price, end_price_date = cls._price_on_or_before(end_date, dates, prices)
        if not end_price or end_price <= 0:
            return {
                "success": False,
                "benchmark_value": 0.0,
                "benchmark_units": units,
                "benchmark_end_price": None,
                "benchmark_end_price_date": None,
                "skipped_cashflows": skipped,
                "step_by_index": step_by_index,
                "warning": "No Nifty price available on or before period end.",
            }

        return {
            "success": True,
            "benchmark_value": units * end_price,
            "benchmark_units": units,
            "benchmark_end_price": end_price,
            "benchmark_end_price_date": end_price_date.isoformat() if end_price_date else None,
            "skipped_cashflows": skipped,
            "step_by_index": step_by_index,
        }

    @staticmethod
    def _flow_row(flow_date: Any, amount: float, label: str, source: str) -> Tuple[Any, float, str, str]:
        return flow_date, float(amount), label, source

    @classmethod
    def calculate(
        cls,
        client_id: int,
        benchmark_id: int = 1,
        as_of_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        as_of = as_of_date or date.today()
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        transactions = Transaction.query.filter_by(client_id=client_id).order_by(Transaction.transaction_date).all()

        activity_dates, exclusion_counts = cls.valid_activity_dates(cashflows, transactions)
        if not activity_dates:
            return {
                "success": False,
                "rows": [],
                "benchmark_id": benchmark_id,
                "message": "No real activity dates found for calendar-year XIRR.",
                **exclusion_counts,
            }

        first_activity_date = min(activity_dates)
        ranges = cls.calendar_year_ranges(first_activity_date, as_of)
        benchmark, benchmark_dates, benchmark_prices = cls._load_benchmark_prices(
            benchmark_id,
            ranges[0][1] - timedelta(days=30),
            as_of,
        )

        from api.v1.performance import calculate_xirr

        rows: List[Dict[str, Any]] = []
        for year, start_date, end_date in ranges:
            start_value = cls._portfolio_value(client_id, start_date)
            end_value = cls._portfolio_value(client_id, end_date)
            xirr_opening_value = 0.0 if start_date <= first_activity_date else start_value
            period_cashflows = cls._real_cashflows_in_period(cashflows, start_date, end_date)

            flow_rows: List[Tuple[Any, float, str, str]] = []
            if xirr_opening_value > 0:
                flow_rows.append(
                    cls._flow_row(
                        datetime.combine(start_date, datetime.min.time()),
                        -xirr_opening_value,
                        "Opening portfolio value",
                        "opening_value",
                    )
                )
            for cf in period_cashflows:
                amount = float(cf.amount)
                label = "Buy / investment" if amount < 0 else "Sell / withdrawal"
                flow_rows.append(cls._flow_row(cf.date, amount, label, "cashflow"))

            xirr_flows = [(d, amount) for d, amount, _, _ in flow_rows]
            if cls._can_calculate_xirr(xirr_flows, end_value):
                portfolio_xirr, total_invested, total_withdrawn, net_investment, absolute_return = calculate_xirr(
                    xirr_flows,
                    end_value,
                    end_date=end_date,
                )
            else:
                portfolio_xirr = 0.0
                total_invested = sum(abs(amount) for _, amount in xirr_flows if amount < 0)
                total_withdrawn = sum(amount for _, amount in xirr_flows if amount > 0)
                net_investment = total_invested - total_withdrawn
                absolute_return = ((end_value - net_investment) / net_investment * 100) if net_investment > 0 else 0.0

            benchmark_flows = [(d, amount, label) for d, amount, label, _ in flow_rows]
            benchmark_result = cls._simulate_benchmark(benchmark_flows, end_date, benchmark_dates, benchmark_prices)
            benchmark_value = float(benchmark_result.get("benchmark_value", 0.0) or 0.0)
            if benchmark_result.get("success") and cls._can_calculate_xirr(xirr_flows, benchmark_value):
                xirr_end_date = benchmark_result.get("benchmark_end_price_date") or end_date
                nifty_xirr, _, _, _, _ = calculate_xirr(
                    xirr_flows,
                    benchmark_value,
                    end_date=xirr_end_date,
                )
            else:
                nifty_xirr = 0.0

            verification_rows: List[Dict[str, Any]] = []
            step_by_index = benchmark_result.get("step_by_index", {})
            for index, (flow_date, amount, label, source) in enumerate(flow_rows):
                row_date = cls._as_date(flow_date)
                benchmark_step = step_by_index.get(index, {})
                verification_rows.append(
                    {
                        "date": row_date.isoformat() if row_date else str(flow_date),
                        "description": label,
                        "source": source,
                        "signed_amount": amount,
                        "cashflow_direction": "inflow_to_portfolio" if amount < 0 else "outflow_from_portfolio",
                        "xirr_treatment": "negative" if amount < 0 else "positive",
                        **benchmark_step,
                    }
                )
            verification_rows.append(
                {
                    "date": end_date.isoformat(),
                    "description": "Ending portfolio value",
                    "source": "ending_value",
                    "signed_amount": float(end_value),
                    "cashflow_direction": "outflow_from_portfolio",
                    "xirr_treatment": "positive",
                    "benchmark_price": benchmark_result.get("benchmark_end_price"),
                    "benchmark_price_date": benchmark_result.get("benchmark_end_price_date"),
                    "benchmark_units_change": 0.0,
                    "benchmark_units_after": benchmark_result.get("benchmark_units"),
                    "benchmark_terminal_value": benchmark_value,
                }
            )

            outperformance = (portfolio_xirr - nifty_xirr) * 100
            rows.append(
                {
                    "year": year,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "is_ytd": year == as_of.year,
                    "portfolio_xirr": portfolio_xirr,
                    "portfolio_xirr_percent": portfolio_xirr * 100,
                    "nifty_xirr": nifty_xirr,
                    "nifty_xirr_percent": nifty_xirr * 100,
                    "outperformance": outperformance,
                    "start_value": start_value,
                    "xirr_opening_portfolio_value": xirr_opening_value,
                    "end_value": end_value,
                    "nifty_value": benchmark_value,
                    "nifty_units": float(benchmark_result.get("benchmark_units", 0.0) or 0.0),
                    "nifty_end_price": benchmark_result.get("benchmark_end_price"),
                    "nifty_end_price_date": benchmark_result.get("benchmark_end_price_date"),
                    "period_cashflows_count": len(period_cashflows),
                    "cashflow_schedule_count": len(flow_rows),
                    "benchmark_cashflow_count": len(flow_rows),
                    "benchmark_skipped_cashflows": int(benchmark_result.get("skipped_cashflows", 0) or 0),
                    "total_invested": total_invested,
                    "total_withdrawn": total_withdrawn,
                    "net_investment": net_investment,
                    "absolute_return": absolute_return,
                    "has_sufficient_xirr_flows": cls._can_calculate_xirr(xirr_flows, end_value),
                    "verification_rows": verification_rows,
                }
            )

        return {
            "success": True,
            "benchmark_id": benchmark_id,
            "benchmark_name": benchmark.name if benchmark else "NIFTY 50",
            "as_of_date": as_of.isoformat(),
            "first_activity_date": first_activity_date.isoformat(),
            "rows": rows,
            "years_count": len(rows),
            "cashflow_convention": {
                "negative": "Opening portfolio value and buys/investments: money entering the portfolio",
                "positive": "Sells/withdrawals and ending value: money leaving the portfolio",
            },
            **exclusion_counts,
        }
