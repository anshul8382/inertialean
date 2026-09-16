"""
Period Analysis Data Collection & Validation Module

This module is responsible for collecting and validating all data needed
for period analysis before any calculations begin.
"""
import logging
from datetime import datetime, date
from models import Client, Transaction, Cashflow
from services.forward_holding_calculation_service import get_client_portfolio_by_date
from api.core.exceptions import ValidationError
from api.v2.transactions_query import build_transactions_v2_query

logger = logging.getLogger(__name__)


class PeriodAnalysisDataCollector:
    """
    Responsible for collecting and validating all data needed for period analysis
    
    Approach:
    1. Collect all data (portfolios, transactions, cashflows, holdings) - ONCE
    2. Validate all data before proceeding
    3. Build helper structures (maps) for efficient lookup
    4. Return validated data object
    """
    
    def __init__(self, client_id, start_date, end_date):
        """
        Initialize data collector
        
        Args:
            client_id: Client ID
            start_date: Period start date
            end_date: Period end date
        """
        self.client_id = client_id
        self.start_date = start_date
        self.end_date = end_date
        
        # Data storage
        self.client = None
        self.current_holdings = None
        self.start_portfolio = None
        self.end_portfolio = None
        self.start_map = None
        self.end_map = None
        self.txns_in_period = None
        self.buy_txns = None
        self.sell_txns = None
        self.period_cashflows = None
        self.all_cashflows = None
        self.is_valid = False
        self.validation_errors = []
    
    def collect_and_validate(self):
        """
        Collect all data and validate it
        
        Returns:
            tuple: (success: bool, errors: list)
        """
        try:
            # Step 1: Validate client exists
            self._validate_client()
            
            # Step 2: Collect portfolio snapshots
            self._collect_portfolio_snapshots()
            self._validate_portfolio_data()
            
            # Step 3: Collect transaction data
            self._collect_transaction_data()
            self._validate_transaction_data()
            
            # Step 4: Collect cashflow data
            self._collect_cashflow_data()
            self._validate_cashflow_data()
            
            # Step 5: Period-end holdings (forward reconstruction, not live DB)
            self._collect_period_end_holdings()
            
            # Step 6: Build helper maps
            self._build_portfolio_maps()

            # Step 7: Cache portfolios for performance-email snapshot reuse
            self._cache_period_portfolios_for_snapshot()
            
            self.is_valid = True
            logger.info(f"Data collection and validation completed successfully for client {self.client_id}")
            return True, []
            
        except ValidationError as e:
            self.validation_errors.append(str(e))
            logger.error(f"Validation error in data collection: {str(e)}")
            return False, self.validation_errors
        except Exception as e:
            error_msg = f"Error collecting data: {str(e)}"
            self.validation_errors.append(error_msg)
            logger.error(error_msg, exc_info=True)
            return False, self.validation_errors
    
    def _validate_client(self):
        """Validate client exists"""
        self.client = Client.query.get(self.client_id)
        if not self.client:
            raise ValidationError(f"Client {self.client_id} not found")
        logger.info(f"Validated client: {self.client_id} - {self.client.name}")
    
    def _collect_portfolio_snapshots(self):
        """Collect portfolio snapshots for start and end dates - ONCE"""
        logger.info(f"Collecting portfolio snapshots for client {self.client_id}: {self.start_date} to {self.end_date}")
        
        # Fetch portfolios ONCE
        self.start_portfolio = get_client_portfolio_by_date(self.client_id, self.start_date)
        self.end_portfolio = get_client_portfolio_by_date(self.client_id, self.end_date)
        
        logger.info(f"Portfolio snapshots collected - Start: {len(self.start_portfolio.get('holdings', []))} holdings, "
                   f"End: {len(self.end_portfolio.get('holdings', []))} holdings")
    
    def _validate_portfolio_data(self):
        """Validate portfolio snapshot data"""
        if not self.start_portfolio:
            raise ValidationError("Failed to retrieve start portfolio snapshot")
        
        if not self.end_portfolio:
            raise ValidationError("Failed to retrieve end portfolio snapshot")
        
        if 'holdings' not in self.start_portfolio:
            raise ValidationError("Start portfolio missing 'holdings' key")
        
        if 'holdings' not in self.end_portfolio:
            raise ValidationError("End portfolio missing 'holdings' key")
        
        if 'total_value' not in self.start_portfolio:
            raise ValidationError("Start portfolio missing 'total_value' key")
        
        if 'total_value' not in self.end_portfolio:
            raise ValidationError("End portfolio missing 'total_value' key")
        
        logger.info(f"Portfolio data validated - Start value: ₹{self.start_portfolio.get('total_value', 0):,.2f}, "
                   f"End value: ₹{self.end_portfolio.get('total_value', 0):,.2f}")
    
    def _collect_transaction_data(self):
        """Collect all transaction data needed for analysis - ONCE"""
        logger.info(f"Collecting transaction data for period: {self.start_date} to {self.end_date}")

        # Use V2 Transactions query semantics (inclusive date bounds over whole days).
        self.txns_in_period = (
            build_transactions_v2_query(
                client_id=self.client_id,
                date_from=self.start_date,
                date_to=self.end_date,
            )
            .order_by(Transaction.transaction_date.asc(), Transaction.id.asc())
            .all()
        )
        
        # Separate BUY and SELL transactions
        self.buy_txns = [t for t in self.txns_in_period if t.type == 'BUY']
        self.sell_txns = [t for t in self.txns_in_period if t.type == 'SELL']
        
        logger.info(f"Transaction data collected - Total: {len(self.txns_in_period)}, "
                   f"BUY: {len(self.buy_txns)}, SELL: {len(self.sell_txns)}")
    
    def _validate_transaction_data(self):
        """Validate transaction data"""
        # Transactions can be empty for some periods, so we just validate structure
        if not isinstance(self.txns_in_period, list):
            raise ValidationError("Transaction data is not a list")
        
        # Validate transaction structure if transactions exist
        if self.txns_in_period:
            sample_txn = self.txns_in_period[0]
            required_attrs = ['type', 'quantity', 'price', 'transaction_date', 'security_id']
            for attr in required_attrs:
                if not hasattr(sample_txn, attr):
                    raise ValidationError(f"Transaction missing required attribute: {attr}")
        
        logger.info(f"Transaction data validated - {len(self.txns_in_period)} transactions")
    
    def _collect_cashflow_data(self):
        """Collect all cashflow data needed for analysis - ONCE"""
        logger.info(f"Collecting cashflow data for period: {self.start_date} to {self.end_date}")
        
        start_datetime = datetime.combine(self.start_date, datetime.min.time())
        end_datetime = datetime.combine(self.end_date, datetime.max.time())
        
        # Fetch period cashflows
        self.period_cashflows = Cashflow.query.filter(
            Cashflow.client_id == self.client_id,
            Cashflow.date >= start_datetime,
            Cashflow.date <= end_datetime
        ).order_by(Cashflow.date).all()
        
        # Fetch ALL cashflows up to end_date (for net investment calculation)
        self.all_cashflows = Cashflow.query.filter(
            Cashflow.client_id == self.client_id,
            Cashflow.date <= end_datetime
        ).order_by(Cashflow.date).all()
        
        logger.info(f"Cashflow data collected - Period: {len(self.period_cashflows)}, "
                   f"Total up to end: {len(self.all_cashflows)}")
    
    def _validate_cashflow_data(self):
        """Validate cashflow data"""
        if not isinstance(self.period_cashflows, list):
            raise ValidationError("Period cashflow data is not a list")
        
        if not isinstance(self.all_cashflows, list):
            raise ValidationError("All cashflow data is not a list")
        
        # Cashflows can be empty, so we just validate structure if they exist
        if self.period_cashflows:
            sample_cf = self.period_cashflows[0]
            required_attrs = ['date', 'amount']
            for attr in required_attrs:
                if not hasattr(sample_cf, attr):
                    raise ValidationError(f"Cashflow missing required attribute: {attr}")
        
        logger.info(f"Cashflow data validated - Period: {len(self.period_cashflows)}, Total: {len(self.all_cashflows)}")
    
    def _collect_period_end_holdings(self):
        """
        Holdings as of period end_date from forward reconstruction (B4).

        v1 analytics (sector performance, risk, trade analytics) expect Holding-like
        objects: security_id, quantity, average_price, security.current_price,
        security.symbol, security.meta_data (optional).
        """
        from types import SimpleNamespace

        from models import Security

        logger.info(
            "Building period-end holding proxies for client %s at %s",
            self.client_id,
            self.end_date,
        )
        proxies = []
        for h in self.end_portfolio.get("holdings", []) or []:
            sec_id = h.get("security_id")
            if not sec_id:
                continue
            sec = Security.query.get(sec_id)
            symbol = h.get("symbol") or (sec.symbol if sec else "Unknown")
            name = h.get("name") or (sec.name if sec else symbol)
            period_price = float(h.get("current_price") or 0.0)
            avg_price = float(h.get("average_price") or 0.0)
            sector = (h.get("sector") or "Unknown").strip() or "Unknown"
            sec_proxy = SimpleNamespace(
                id=sec_id,
                symbol=symbol,
                name=name,
                current_price=period_price,
                sector=sector,
                industry=getattr(sec, "industry", None) if sec else None,
                meta_data=getattr(sec, "meta_data", None) if sec else None,
            )
            proxies.append(
                SimpleNamespace(
                    security_id=sec_id,
                    quantity=float(h.get("quantity") or 0.0),
                    average_price=avg_price,
                    security=sec_proxy,
                )
            )
        self.current_holdings = proxies
        logger.info(f"Period-end holding proxies - {len(self.current_holdings)} positions")

    def _cache_period_portfolios_for_snapshot(self):
        try:
            from services.period_portfolio_cache import store_period_portfolios

            store_period_portfolios(
                self.client_id,
                self.start_date,
                self.end_date,
                self.start_portfolio,
                self.end_portfolio,
            )
        except Exception as e:
            logger.debug("Period portfolio cache store skipped: %s", e)
    
    def _build_portfolio_maps(self):
        """Build holdings maps for efficient lookup"""
        logger.info("Building portfolio maps for efficient lookup")
        
        # Import build_holdings_map from v1 (will be available after we create the function)
        # For now, use simple implementation
        self.start_map = {h['security_id']: h for h in self.start_portfolio.get('holdings', [])}
        self.end_map = {h['security_id']: h for h in self.end_portfolio.get('holdings', [])}
        
        logger.info(f"Portfolio maps built - Start: {len(self.start_map)} securities, "
                   f"End: {len(self.end_map)} securities")
    
    def get_data_summary(self):
        """Get summary of collected data for logging/audit"""
        return {
            'client_id': self.client_id,
            'client_name': self.client.name if self.client else None,
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'start_portfolio': {
                'holdings_count': len(self.start_portfolio.get('holdings', [])) if self.start_portfolio else 0,
                'total_value': self.start_portfolio.get('total_value', 0) if self.start_portfolio else 0
            },
            'end_portfolio': {
                'holdings_count': len(self.end_portfolio.get('holdings', [])) if self.end_portfolio else 0,
                'total_value': self.end_portfolio.get('total_value', 0) if self.end_portfolio else 0
            },
            'transactions': {
                'total': len(self.txns_in_period) if self.txns_in_period else 0,
                'buy': len(self.buy_txns) if self.buy_txns else 0,
                'sell': len(self.sell_txns) if self.sell_txns else 0
            },
            'cashflows': {
                'period': len(self.period_cashflows) if self.period_cashflows else 0,
                'total': len(self.all_cashflows) if self.all_cashflows else 0
            },
            'current_holdings_count': len(self.current_holdings) if self.current_holdings else 0,
            'is_valid': self.is_valid,
            'validation_errors': self.validation_errors
        }

