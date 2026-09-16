"""
Benchmark Service
Centralized service for benchmark data operations
Handles Nifty 50 and other benchmark indices
"""

from models import db, Benchmark, BenchmarkData, Cashflow
from typing import Dict, List, Any, Optional
from datetime import date, datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class BenchmarkService:
    """
    Centralized service for benchmark operations
    Handles Nifty 50 and other benchmark indices
    """
    
    @staticmethod
    def get_benchmark_by_id(benchmark_id: int) -> Optional[Benchmark]:
        """Get benchmark by ID"""
        return Benchmark.query.get(benchmark_id)
    
    @staticmethod
    def get_nifty_benchmark() -> Optional[Benchmark]:
        """Get Nifty 50 benchmark (ID=1)"""
        return Benchmark.query.filter_by(id=1).first()
    
    @staticmethod
    def get_latest_price(benchmark_id: int) -> Dict[str, Any]:
        """
        Get latest benchmark price
        
        Returns:
            Dictionary with price, date, and age info
        """
        latest = BenchmarkData.query.filter_by(benchmark_id=benchmark_id)\
            .order_by(BenchmarkData.date.desc()).first()
        
        if not latest:
            return {
                'success': False,
                'error': 'No price data found'
            }
        
        # Check if data is stale (older than 1 day)
        today = date.today()
        age_days = (today - latest.date).days
        is_stale = age_days > 1
        
        return {
            'success': True,
            'benchmark_id': benchmark_id,
            'price': float(latest.price),
            'date': latest.date,
            'age_days': age_days,
            'is_stale': is_stale,
            'updated_at': latest.updated_at
        }
    
    @staticmethod
    def get_price_on_date(benchmark_id: int, target_date: date) -> Optional[float]:
        """
        Get benchmark price on or before a specific date
        
        Args:
            benchmark_id: Benchmark ID
            target_date: Target date
            
        Returns:
            Price or None if not found
        """
        benchmark_data = BenchmarkData.query.filter_by(benchmark_id=benchmark_id)\
            .filter(BenchmarkData.date <= target_date)\
            .order_by(BenchmarkData.date.desc()).first()
        
        return float(benchmark_data.price) if benchmark_data else None
    
    @staticmethod
    def get_price_range(
        benchmark_id: int,
        start_date: date,
        end_date: date
    ) -> List[Dict[str, Any]]:
        """
        Get benchmark prices for a date range
        
        Returns:
            List of dictionaries with date and price
        """
        data = BenchmarkData.query.filter_by(benchmark_id=benchmark_id)\
            .filter(BenchmarkData.date >= start_date)\
            .filter(BenchmarkData.date <= end_date)\
            .order_by(BenchmarkData.date).all()
        
        return [
            {
                'date': bd.date,
                'price': float(bd.price)
            }
            for bd in data
        ]
    
    @staticmethod
    def create_or_update_price(
        benchmark_id: int,
        price_date: date,
        price: float,
        overwrite: bool = True
    ) -> Dict[str, Any]:
        """
        Create or update benchmark price for a date
        
        Args:
            benchmark_id: Benchmark ID
            price_date: Date for the price
            price: Price value
            overwrite: If True, overwrite existing data. If False, skip if exists.
            
        Returns:
            Dictionary with operation result
        """
        try:
            existing = BenchmarkData.query.filter_by(
                benchmark_id=benchmark_id,
                date=price_date
            ).first()
            
            if existing:
                if not overwrite:
                    action = 'skipped'
                    logger.info(f"Skipped benchmark {benchmark_id} price for {price_date} (already exists)")
                else:
                    old_price = existing.price
                    existing.price = price
                    existing.updated_at = datetime.utcnow()
                    action = 'updated'
                    logger.info(f"Updated benchmark {benchmark_id} price for {price_date}: ₹{old_price} -> ₹{price}")
            else:
                new_data = BenchmarkData(
                    benchmark_id=benchmark_id,
                    date=price_date,
                    price=price,
                    created_at=datetime.utcnow()
                )
                db.session.add(new_data)
                action = 'created'
                logger.info(f"Created benchmark {benchmark_id} price for {price_date}: ₹{price}")
            
            return {
                'success': True,
                'action': action,
                'benchmark_id': benchmark_id,
                'date': price_date,
                'price': price
            }
        
        except Exception as e:
            logger.error(f"Error creating/updating benchmark price: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def clear_year_data(benchmark_id: int, year: int) -> Dict[str, Any]:
        """
        Clear all benchmark data for a specific year
        Used before uploading fresh data from official source
        
        Args:
            benchmark_id: Benchmark ID
            year: Year to clear
            
        Returns:
            Dictionary with operation result
        """
        try:
            year_start = date(year, 1, 1)
            year_end = date(year, 12, 31)
            
            deleted_count = BenchmarkData.query.filter(
                BenchmarkData.benchmark_id == benchmark_id,
                BenchmarkData.date >= year_start,
                BenchmarkData.date <= year_end
            ).delete()
            
            logger.info(f"Cleared {deleted_count} records for benchmark {benchmark_id}, year {year}")
            
            return {
                'success': True,
                'benchmark_id': benchmark_id,
                'year': year,
                'deleted_count': deleted_count
            }
        
        except Exception as e:
            logger.error(f"Error clearing year data: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def get_year_data_status(benchmark_id: int, year: int) -> Dict[str, Any]:
        """
        Get data status for a specific year
        
        Returns:
            Dictionary with has_data flag and record count
        """
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        
        count = BenchmarkData.query.filter(
            BenchmarkData.benchmark_id == benchmark_id,
            BenchmarkData.date >= year_start,
            BenchmarkData.date <= year_end
        ).count()
        
        return {
            'year': year,
            'has_data': count > 0,
            'record_count': count,
            'start_date': year_start,
            'end_date': year_end
        }
    
    @staticmethod
    def get_all_years_status(benchmark_id: int, start_year: int = 2000, end_year: int = None) -> Dict[int, Dict[str, Any]]:
        """
        Get data status for multiple years
        
        Returns:
            Dictionary mapping year to status data
        """
        if end_year is None:
            end_year = datetime.now().year
        
        status = {}
        for year in range(start_year, end_year + 1):
            status[year] = BenchmarkService.get_year_data_status(benchmark_id, year)
        
        return status
    
    @staticmethod
    def calculate_benchmark_xirr(client_id: int, benchmark_id: int = 1) -> Dict[str, Any]:
        """
        Calculate XIRR assuming client cashflows were invested in benchmark.
        This is the main method used in client details page.
        
        Args:
            client_id: Client ID to calculate benchmark XIRR for
            benchmark_id: Benchmark ID (default: 1 for NIFTY 50)
            
        Returns:
            Dictionary with benchmark XIRR results
        """
        try:
            # Get client cashflows
            cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
            if not cashflows:
                return {
                    'success': False,
                    'error': 'No cashflows found for client',
                    'nifty_xirr': 0.0,
                    'nifty_current_value': 0.0,
                    'nifty_absolute_return': 0.0
                }
            
            # Get benchmark
            benchmark = Benchmark.query.filter_by(id=benchmark_id).first()
            if not benchmark:
                return {
                    'success': False,
                    'error': f'Benchmark {benchmark_id} not found',
                    'nifty_xirr': 0.0,
                    'nifty_current_value': 0.0,
                    'nifty_absolute_return': 0.0
                }
            
            # Get date range
            # Convert all dates to date objects before comparison to avoid datetime/date comparison errors
            date_list = [cf.date.date() if isinstance(cf.date, datetime) else cf.date for cf in cashflows]
            min_date = min(date_list)
            max_date = max(date_list)
            
            # Convert to date objects if they're not already
            from datetime import date as date_type
            if isinstance(min_date, datetime):
                min_date = min_date.date()
            if isinstance(max_date, datetime):
                max_date = max_date.date()
            
            # Get benchmark data for a wider range to ensure we have enough data points
            # Include data before min_date to handle weekends/holidays
            extended_start_date = min_date - timedelta(days=30)
            benchmark_data = BenchmarkData.query.filter_by(benchmark_id=benchmark_id)\
                .filter(BenchmarkData.date >= extended_start_date)\
                .filter(BenchmarkData.date <= max_date)\
                .order_by(BenchmarkData.date).all()
            
            if not benchmark_data:
                return {
                    'success': False,
                    'error': 'No benchmark data found for the date range',
                    'nifty_xirr': 0.0,
                    'nifty_current_value': 0.0,
                    'nifty_absolute_return': 0.0
                }
            
            # Create price lookup dictionary
            price_lookup = {bd.date: float(bd.price) for bd in benchmark_data}
            
            # Get latest benchmark price
            latest_benchmark = BenchmarkData.query.filter_by(benchmark_id=benchmark_id)\
                .order_by(BenchmarkData.date.desc()).first()
            
            if not latest_benchmark:
                return {
                    'success': False,
                    'error': 'No latest benchmark price found',
                    'nifty_xirr': 0.0,
                    'nifty_current_value': 0.0,
                    'nifty_absolute_return': 0.0
                }
            
            latest_price = float(latest_benchmark.price)
            
            # Simulate benchmark investment
            simulation_result = BenchmarkService._simulate_benchmark_investment(
                cashflows, price_lookup, latest_price
            )
            
            # XIRR: same sign convention as Cashflow rows (negative = investment, positive = withdrawal)
            from api.v1.performance import calculate_xirr

            cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
            nifty_xirr, _, _, _, _ = calculate_xirr(
                cashflow_data, simulation_result['current_value']
            )
            
            return {
                'success': True,
                'nifty_xirr': nifty_xirr,
                'nifty_current_value': simulation_result['current_value'],
                'nifty_absolute_return': simulation_result['absolute_return'],
                'benchmark_id': benchmark_id,
                'benchmark_name': benchmark.name,
                'simulation_details': simulation_result,
                'latest_benchmark_date': latest_benchmark.date.isoformat(),
                'latest_benchmark_price': latest_price,
                'extended_start_date': extended_start_date.isoformat(),
                'cashflow_min_date': min_date.isoformat(),
                'cashflow_max_date': max_date.isoformat(),
                'benchmark_price_points_in_range': len(benchmark_data),
            }
            
        except Exception as e:
            logger.error(f"Error calculating benchmark XIRR for client {client_id}: {e}")
            return {
                'success': False,
                'error': str(e),
                'nifty_xirr': 0.0,
                'nifty_current_value': 0.0,
                'nifty_absolute_return': 0.0
            }
    
    @staticmethod
    def _simulate_benchmark_investment(
        cashflows: List[Cashflow],
        price_lookup: Dict[date, float],
        latest_price: float,
    ) -> Dict[str, Any]:
        """
        Simulate what would happen if client cashflows were invested in benchmark.

        Returns aggregate metrics plus ``cashflow_steps`` (chronological audit for UI).
        """
        benchmark_units = 0.0
        total_invested = 0.0
        total_withdrawn = 0.0
        weighted_total_cost = 0.0
        cashflow_steps: List[Dict[str, Any]] = []

        for cf in cashflows:
            cf_amount = float(cf.amount)
            cf_date_raw = cf.date
            cf_date = cf_date_raw.date() if hasattr(cf_date_raw, "date") else cf_date_raw

            price_for_date = None
            price_source_date: Optional[date] = None
            price_source = "none"

            if cf_date in price_lookup:
                price_for_date = price_lookup[cf_date]
                price_source_date = cf_date
                price_source = "exact_date"
            else:
                available_dates = [d for d in price_lookup.keys() if d <= cf_date]
                if available_dates:
                    price_source_date = max(available_dates)
                    price_for_date = price_lookup[price_source_date]
                    price_source = "prior_close"

            if price_for_date is None or price_for_date <= 0:
                cashflow_steps.append(
                    {
                        "cashflow_date": cf_date.isoformat() if hasattr(cf_date, "isoformat") else str(cf_date),
                        "amount": cf_amount,
                        "status": "skipped_no_price",
                        "note": "No benchmark price on or before this date.",
                        "benchmark_units_after": round(benchmark_units, 6),
                    }
                )
                continue

            # negative = invest (buy index), positive = withdraw (sell)
            if cf_amount < 0:
                units_bought = abs(cf_amount) / price_for_date
                benchmark_units += units_bought
                total_invested += abs(cf_amount)
                weighted_total_cost += abs(cf_amount)
                cashflow_steps.append(
                    {
                        "cashflow_date": cf_date.isoformat(),
                        "amount": cf_amount,
                        "status": "invest",
                        "benchmark_price": round(price_for_date, 4),
                        "price_source": price_source,
                        "price_as_of_date": price_source_date.isoformat() if price_source_date else None,
                        "units_change": round(units_bought, 6),
                        "units_change_label": "buy",
                        "benchmark_units_after": round(benchmark_units, 6),
                    }
                )
            else:
                if benchmark_units <= 0:
                    cashflow_steps.append(
                        {
                            "cashflow_date": cf_date.isoformat(),
                            "amount": cf_amount,
                            "status": "withdraw_ignored",
                            "benchmark_price": round(price_for_date, 4),
                            "price_source": price_source,
                            "price_as_of_date": price_source_date.isoformat() if price_source_date else None,
                            "note": "Withdrawal ignored (no benchmark units held).",
                            "units_change": 0.0,
                            "units_change_label": "—",
                            "benchmark_units_after": round(benchmark_units, 6),
                        }
                    )
                    continue

                units_sold = min(cf_amount / price_for_date, benchmark_units)
                benchmark_units -= units_sold
                total_withdrawn += cf_amount
                if benchmark_units > 0:
                    reduction_ratio = units_sold / (benchmark_units + units_sold)
                    weighted_total_cost -= weighted_total_cost * reduction_ratio

                cashflow_steps.append(
                    {
                        "cashflow_date": cf_date.isoformat(),
                        "amount": cf_amount,
                        "status": "withdraw",
                        "benchmark_price": round(price_for_date, 4),
                        "price_source": price_source,
                        "price_as_of_date": price_source_date.isoformat() if price_source_date else None,
                        "units_change": round(-units_sold, 6),
                        "units_change_label": "sell",
                        "benchmark_units_after": round(benchmark_units, 6),
                    }
                )

        current_value = benchmark_units * latest_price
        weighted_avg_price = weighted_total_cost / benchmark_units if benchmark_units > 0 else 0.0
        net_investment = total_invested - total_withdrawn

        # % change of index vs average cost basis per unit still held (informational; not "total return")
        if weighted_avg_price > 0 and benchmark_units > 0:
            unit_cost_implied_return_pct = (
                (latest_price - weighted_avg_price) / weighted_avg_price * 100.0
            )
        else:
            unit_cost_implied_return_pct = 0.0

        # Same idea as portfolio "absolute return": (terminal value − net capital from flows) / net capital
        if net_investment > 1e-6:
            absolute_return = ((current_value - net_investment) / net_investment) * 100.0
        else:
            absolute_return = 0.0

        return {
            "current_value": current_value,
            "absolute_return": absolute_return,
            "unit_cost_implied_return_pct": unit_cost_implied_return_pct,
            "benchmark_units": benchmark_units,
            "weighted_avg_price": weighted_avg_price,
            "total_invested": total_invested,
            "total_withdrawn": total_withdrawn,
            "net_investment": net_investment,
            "cashflow_steps": cashflow_steps,
        }

