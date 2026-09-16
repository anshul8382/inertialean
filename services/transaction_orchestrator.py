"""
V2 Transaction Orchestrator Service
Handles both incremental and forward calculation approaches for transaction management

Key Features:
- Incremental O(1) processing for single transactions
- Forward calculation O(n) processing for bulk operations
- Proper handling of corporate actions
- Integration with holdings and cashflow services
"""
from datetime import date, datetime
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class TransactionOrchestrator:
    """
    V2 Transaction Orchestrator Service
    Central service that coordinates transactions, holdings, and cashflows
    """
    
    def __init__(self, db_session):
        self.db = db_session
    
    # Single Transaction Operations (INCREMENTAL - O(1))
    def create_single_transaction(self, transaction_data):
        """Create one transaction and update holdings/cashflows incrementally"""
        try:
            from models import Transaction, Holding, Cashflow
            
            # Validate transaction type (only BUY/SELL allowed)
            if transaction_data.get('type') not in ['BUY', 'SELL']:
                return {'success': False, 'message': 'Only BUY/SELL transactions allowed in V2'}
            
            # Convert date to datetime if needed
            # Handle string dates from JSON/API (e.g., "2024-01-15" or "2024-01-15T00:00:00")
            transaction_date = transaction_data.get('transaction_date', date.today())
            if isinstance(transaction_date, str):
                # Parse ISO format strings (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
                try:
                    if 'T' in transaction_date:
                        parsed_date = datetime.fromisoformat(transaction_date.replace('Z', '+00:00')).date()
                    else:
                        parsed_date = datetime.strptime(transaction_date, '%Y-%m-%d').date()
                    transaction_date = datetime.combine(parsed_date, datetime.min.time())
                except (ValueError, TypeError) as e:
                    logger.error(f"Error parsing transaction_date '{transaction_date}': {e}")
                    raise ValueError(f"Invalid transaction_date format: {transaction_date}. Expected YYYY-MM-DD or datetime")
            elif isinstance(transaction_date, date):
                transaction_date = datetime.combine(transaction_date, datetime.min.time())
            elif isinstance(transaction_date, datetime):
                # Already a datetime, ensure it has time component
                if transaction_date.time() == datetime.min.time():
                    pass  # Already at midnight
                else:
                    transaction_date = datetime.combine(transaction_date.date(), datetime.min.time())
            else:
                logger.error(f"Unexpected transaction_date type: {type(transaction_date)}, value: {transaction_date}")
                raise ValueError(f"transaction_date must be a date, datetime, or ISO format string, got {type(transaction_date)}")
            
            # Normalize numeric types to Decimal for precision
            from decimal import Decimal
            qty = transaction_data['quantity']
            prc = transaction_data['price']
            if not isinstance(qty, Decimal):
                qty = Decimal(str(qty))
            if not isinstance(prc, Decimal):
                prc = Decimal(str(prc))

            # Create transaction
            transaction = Transaction(
                client_id=transaction_data['client_id'],
                security_id=transaction_data['security_id'],
                type=transaction_data['type'],
                quantity=qty,
                price=prc,
                amount=qty * prc,
                transaction_date=transaction_date
            )
            self.db.add(transaction)
            self.db.flush()  # Get transaction ID
            
            # Update holdings incrementally (O(1))
            self._update_holding_incremental(transaction)

            # New trades still write the matching cashflow line. Edits do not
            # (no transaction_id on cashflow — heuristic updates were unsafe).
            self._update_cashflow_incremental(transaction)
            logger.info(
                "Created transaction %s for client %s — holdings and cashflow updated",
                transaction.id,
                transaction.client_id,
            )

            return {'success': True, 'transaction_id': transaction.id}
            
        except Exception as e:
            logger.error(f"Error creating single transaction: {str(e)}")
            return {'success': False, 'message': str(e)}
    
    def update_single_transaction(self, transaction_id, transaction_data):
        """Update one transaction and adjust holdings only (cashflows are manual)."""
        try:
            from models import Transaction
            
            # Get old transaction for comparison
            old_transaction = Transaction.query.get(transaction_id)
            if not old_transaction:
                return {'success': False, 'message': 'Transaction not found'}
            
            # Revert old holding impact only — do not mutate cashflow rows
            self._revert_holding_impact(old_transaction)
            
            # Update transaction fields
            if 'client_id' in transaction_data:
                old_transaction.client_id = int(transaction_data['client_id'])
            if 'security_id' in transaction_data:
                old_transaction.security_id = transaction_data['security_id']
            if 'type' in transaction_data:
                old_transaction.type = transaction_data['type']
            from decimal import Decimal
            if 'quantity' in transaction_data:
                qv = transaction_data['quantity']
                old_transaction.quantity = qv if isinstance(qv, Decimal) else Decimal(str(qv))
            if 'price' in transaction_data:
                pv = transaction_data['price']
                old_transaction.price = pv if isinstance(pv, Decimal) else Decimal(str(pv))
            if 'transaction_date' in transaction_data:
                old_transaction.transaction_date = transaction_data['transaction_date']

            if old_transaction.type in ('BUY', 'SELL'):
                old_transaction.amount = old_transaction.quantity * old_transaction.price
            
            # Apply new holding impact only
            self._update_holding_incremental(old_transaction)
            logger.info(
                "Updated transaction %s — holdings adjusted; cashflow unchanged (manual review if needed)",
                old_transaction.id,
            )
            
            return {'success': True, 'transaction_id': old_transaction.id, 'cashflow_unchanged': True}
            
        except Exception as e:
            logger.error(f"Error updating single transaction: {str(e)}")
            return {'success': False, 'message': str(e)}
    
    def delete_single_transaction(self, transaction_id):
        """
        Delete single transaction using the same smart logic as bulk delete.
        Checks if security has CA and adjusts accordingly.
        """
        try:
            from models import Transaction, CorporateAction
            
            # Get transaction before deletion
            transaction = Transaction.query.get(transaction_id)
            if not transaction:
                return {'success': False, 'message': 'Transaction not found'}
            
            # Store transaction details
            from decimal import Decimal
            transaction_data = {
                'id': transaction.id,
                'client_id': transaction.client_id,
                'security_id': transaction.security_id,
                'type': transaction.type,
                'quantity': Decimal(str(transaction.quantity)) if transaction.quantity is not None else Decimal('0'),
                'price': Decimal(str(transaction.price)) if transaction.price is not None else Decimal('0'),
                'amount': Decimal(str(transaction.amount)) if transaction.amount is not None else Decimal('0'),
                'transaction_date': transaction.transaction_date
            }
            
            # Check if security has CA history
            has_ca = CorporateAction.query.filter_by(
                security_id=transaction.security_id,
                is_active=True
            ).first() is not None
            
            # Delete transaction
            self.db.delete(transaction)
            
            # Cashflows are advisor-managed; single delete does not auto-adjust cashflow.
            logger.info(
                "Deleted transaction %s for client %s — cashflow unchanged (manual review if needed)",
                transaction_data['id'],
                transaction_data['client_id'],
            )
            
            # Adjust holdings based on CA presence
            if has_ca:
                # Use forward calculation for securities with CA
                # Use self.db session to ensure consistency after transaction deletion
                from services.forward_holding_calculation_service import get_client_portfolio_by_date
                
                portfolio = get_client_portfolio_by_date(transaction.client_id, date.today(), db_session=self.db)
                self._update_all_holdings_from_forward_calculation(transaction.client_id, portfolio)
            else:
                # Simple adjustment for securities without CA
                self._adjust_holdings_simple(transaction_data)
            
            return {'success': True}
            
        except Exception as e:
            logger.error(f"Error deleting single transaction: {str(e)}")
            return {'success': False, 'message': str(e)}
    
    # Bulk Operations (FORWARD CALCULATION - O(n))
    def process_bulk_transactions(self, data):
        """Create multiple transactions and recalculate all holdings/cashflows"""
        try:
            from models import Transaction, Cashflow
            
            # Handle both dict with 'transactions' key and direct list
            if isinstance(data, dict) and 'transactions' in data:
                transactions_data = data['transactions']
                client_id = data['client_id']
            else:
                transactions_data = data
                client_id = transactions_data[0]['client_id'] if transactions_data else None
            
            if not transactions_data:
                return {'success': False, 'message': 'No transactions provided'}
            
            # Step 1: Store all transactions
            created_transactions = []
            for transaction_data in transactions_data:
                # Validate transaction type
                if transaction_data.get('type') not in ['BUY', 'SELL']:
                    return {'success': False, 'message': 'Only BUY/SELL transactions allowed in V2'}
                
                # Convert date to datetime if needed
                # Handle string dates from JSON/API (e.g., "2024-01-15" or "2024-01-15T00:00:00")
                transaction_date = transaction_data.get('transaction_date', date.today())
                if isinstance(transaction_date, str):
                    # Parse ISO format strings (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
                    try:
                        if 'T' in transaction_date:
                            parsed_date = datetime.fromisoformat(transaction_date.replace('Z', '+00:00')).date()
                        else:
                            parsed_date = datetime.strptime(transaction_date, '%Y-%m-%d').date()
                        transaction_date = datetime.combine(parsed_date, datetime.min.time())
                    except (ValueError, TypeError) as e:
                        logger.error(f"Error parsing transaction_date '{transaction_date}': {e}")
                        raise ValueError(f"Invalid transaction_date format: {transaction_date}. Expected YYYY-MM-DD or datetime")
                elif isinstance(transaction_date, date):
                    transaction_date = datetime.combine(transaction_date, datetime.min.time())
                elif isinstance(transaction_date, datetime):
                    # Already a datetime, ensure it has time component
                    if transaction_date.time() == datetime.min.time():
                        pass  # Already at midnight
                    else:
                        transaction_date = datetime.combine(transaction_date.date(), datetime.min.time())
                else:
                    logger.error(f"Unexpected transaction_date type: {type(transaction_date)}, value: {transaction_date}")
                    raise ValueError(f"transaction_date must be a date, datetime, or ISO format string, got {type(transaction_date)}")
                
                # Normalize numeric types to Decimal
                from decimal import Decimal
                qty = transaction_data['quantity']
                prc = transaction_data['price']
                if not isinstance(qty, Decimal):
                    qty = Decimal(str(qty))
                if not isinstance(prc, Decimal):
                    prc = Decimal(str(prc))

                transaction = Transaction(
                    client_id=client_id,  # Use the client_id from the main data structure
                    security_id=transaction_data['security_id'],
                    type=transaction_data['type'],
                    quantity=qty,
                    price=prc,
                    amount=qty * prc,
                    transaction_date=transaction_date
                )
                self.db.add(transaction)
                created_transactions.append(transaction)
            
            self.db.flush()  # Get transaction IDs
            
            # Step 2: Aggregate cashflows by date (to avoid multiple entries per day)
            from services.cashflow_service import DUMMY_DATES
            from collections import defaultdict
            from decimal import Decimal
            
            # Group transactions by date
            date_transactions = defaultdict(list)
            for transaction in created_transactions:
                # PROTECTION: Skip cashflow creation for dummy dates (manual entries)
                # Ensure transaction_date_obj is always a date object
                if isinstance(transaction.transaction_date, datetime):
                    transaction_date_obj = transaction.transaction_date.date()
                elif isinstance(transaction.transaction_date, date):
                    transaction_date_obj = transaction.transaction_date
                elif isinstance(transaction.transaction_date, str):
                    # Handle edge case where string might have slipped through
                    try:
                        if 'T' in transaction.transaction_date:
                            transaction_date_obj = datetime.fromisoformat(transaction.transaction_date.replace('Z', '+00:00')).date()
                        else:
                            transaction_date_obj = datetime.strptime(transaction.transaction_date, '%Y-%m-%d').date()
                    except (ValueError, TypeError) as e:
                        logger.error(f"Error parsing transaction_date '{transaction.transaction_date}': {e}")
                        raise ValueError(f"Invalid transaction_date format: {transaction.transaction_date}")
                else:
                    logger.error(f"Unexpected transaction_date type: {type(transaction.transaction_date)}")
                    raise ValueError(f"transaction_date must be date or datetime, got {type(transaction.transaction_date)}")
                
                if transaction_date_obj in DUMMY_DATES:
                    logger.info(f"Skipping cashflow creation for dummy date {transaction_date_obj} - manual entry")
                    continue
                
                date_transactions[transaction_date_obj].append(transaction)
            
            # Calculate aggregated cashflow for each date and create/update cashflow entries
            for transaction_date_obj, transactions_for_date in date_transactions.items():
                # Calculate total cashflow for this date
                total_cashflow = Decimal('0.0')
                for transaction in transactions_for_date:
                    # BUY = negative (cash outflow), SELL = positive (cash inflow)
                    amount = Decimal(str(transaction.amount))
                    if transaction.type == 'BUY':
                        total_cashflow -= amount
                    elif transaction.type == 'SELL':
                        total_cashflow += amount
                
                # Only create/update cashflow if non-zero
                if abs(total_cashflow) > Decimal('0.01'):  # Allow for decimal precision
                    # Find existing cashflow for this date (using self.db session for consistency)
                    existing_cashflow = self.db.query(Cashflow).filter_by(
                        client_id=client_id,
                        date=datetime.combine(transaction_date_obj, datetime.min.time())
                    ).first()
                    
                    if existing_cashflow:
                        # Update existing cashflow
                        existing_cashflow.amount = float(total_cashflow)
                        # Negative amount = investment (money in) = INFLOW, Positive amount = withdrawal (money out) = OUTFLOW
                        existing_cashflow.type = 'INFLOW' if total_cashflow < 0 else 'OUTFLOW'
                        existing_cashflow.description = f"Net cashflow for {transaction_date_obj}"
                        logger.debug(f"Updated cashflow for client {client_id} on {transaction_date_obj}: {total_cashflow}")
                    else:
                        from models import User
                        system_user = User.query.filter_by(is_admin=True).first() or User.query.first()
                        if not system_user:
                            raise ValueError("No user found for cashflow created_by")
                        # Create new cashflow
                        cashflow = Cashflow(
                            client_id=client_id,
                            amount=float(total_cashflow),
                            date=datetime.combine(transaction_date_obj, datetime.min.time()),
                            # Negative amount = investment (money in) = INFLOW, Positive amount = withdrawal (money out) = OUTFLOW
                            type='INFLOW' if total_cashflow < 0 else 'OUTFLOW',
                            description=f"Net cashflow for {transaction_date_obj}",
                            created_by=system_user.id,
                        )
                        self.db.add(cashflow)
                        logger.debug(f"Created cashflow for client {client_id} on {transaction_date_obj}: {total_cashflow}")
            
            # Step 3: Calculate holdings using forward calculation
            # CRITICAL: Pass self.db session to see uncommitted transactions
            from services.forward_holding_calculation_service import get_client_portfolio_by_date
            
            logger.info(f"Starting forward calculation for bulk transactions - Client {client_id}, {len(created_transactions)} transactions")
            portfolio = get_client_portfolio_by_date(client_id, date.today(), db_session=self.db)
            logger.info(f"Portfolio calculated: {len(portfolio.get('holdings', []))} holdings found")
            
            # Step 4: Update holdings table with forward calculation results
            logger.info(f"Updating holdings from portfolio for client {client_id}")
            self._update_all_holdings_from_forward_calculation(client_id, portfolio)
            logger.info(f"Holdings update completed for client {client_id}")
            
            return {'success': True, 'created_count': len(created_transactions)}
            
        except Exception as e:
            logger.error(f"Error processing bulk transactions: {str(e)}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return {'success': False, 'message': f"Bulk processing failed: {str(e)}"}
    
    def refresh_all_holdings(self, client_id):
        """Recalculate all holdings from transactions (for data correction)"""
        try:
            # Use forward calculation to get accurate holdings
            # Use self.db session for consistency (though committed transactions should work either way)
            from services.forward_holding_calculation_service import get_client_portfolio_by_date
            
            portfolio = get_client_portfolio_by_date(client_id, date.today(), db_session=self.db)
            
            # Update holdings table with forward calculation results
            self._update_all_holdings_from_forward_calculation(client_id, portfolio)
            
            return {'success': True}
            
        except Exception as e:
            logger.error(f"Error refreshing holdings: {str(e)}")
            return {'success': False, 'message': str(e)}
    
    # Private helper methods
    def _update_holding_incremental(self, transaction):
        """Fast O(1) update for single transaction"""
        from models import Holding
        
        if transaction.type not in ['BUY', 'SELL']:
            raise Exception("Only BUY/SELL transactions supported in V2")
        
        holding = Holding.query.filter_by(
            client_id=transaction.client_id,
            security_id=transaction.security_id
        ).first()
        
        if transaction.type == 'BUY':
            if holding:
                # Weighted average calculation
                new_quantity = holding.quantity + transaction.quantity
                new_avg_price = ((holding.quantity * holding.average_price) + 
                               (transaction.quantity * transaction.price)) / new_quantity
                holding.quantity = new_quantity
                holding.average_price = new_avg_price
            else:
                # Create new holding
                holding = Holding(
                    client_id=transaction.client_id,
                    security_id=transaction.security_id,
                    quantity=transaction.quantity,
                    average_price=transaction.price
                )
                self.db.add(holding)
                
        elif transaction.type == 'SELL':
            if holding:
                holding.quantity -= transaction.quantity
                if holding.quantity <= 0:
                    self.db.delete(holding)
            else:
                # Holdings are often out of sync with ledger (CA / forward calc).
                # Do not block trade create/update — log and continue.
                logger.warning(
                    "SELL without holding for client_id=%s security_id=%s qty=%s — skipping holding adjust",
                    transaction.client_id,
                    transaction.security_id,
                    transaction.quantity,
                )
    
    def _revert_holding_impact(self, transaction):
        """Revert holding impact of a transaction (undo BUY/SELL on holdings table)."""
        from models import Transaction

        if transaction.type == 'BUY':
            temp_transaction = Transaction(
                client_id=transaction.client_id,
                security_id=transaction.security_id,
                type='SELL',
                quantity=transaction.quantity,
                price=transaction.price,
                amount=transaction.amount,
            )
            self._update_holding_incremental(temp_transaction)
        elif transaction.type == 'SELL':
            temp_transaction = Transaction(
                client_id=transaction.client_id,
                security_id=transaction.security_id,
                type='BUY',
                quantity=transaction.quantity,
                price=transaction.price,
                amount=transaction.amount,
            )
            self._update_holding_incremental(temp_transaction)

    def _revert_transaction_impact(self, transaction):
        """Revert holdings + trade-derived cashflow (bulk/legacy paths only)."""
        self._revert_holding_impact(transaction)
        self._revert_cashflow_impact(transaction)

    def _revert_cashflow_impact(self, transaction):
        """Delete a trade-style cashflow line matched by description (legacy helper)."""
        from models import Cashflow
        from sqlalchemy import func as sa_func
        
        security_symbol = transaction.security.symbol if transaction.security else "UNKNOWN"
        qty_disp = transaction.quantity
        try:
            qty_disp = (
                int(transaction.quantity)
                if float(transaction.quantity) == int(float(transaction.quantity))
                else transaction.quantity
            )
        except (TypeError, ValueError):
            pass
        old_description = f"{transaction.type} {qty_disp} shares"
        new_description = f"{transaction.type} {qty_disp} shares of {security_symbol}"
        # Also match Decimal-string forms produced when quantity was not int-normalized
        alt_old = f"{transaction.type} {transaction.quantity} shares"
        alt_new = f"{transaction.type} {transaction.quantity} shares of {security_symbol}"

        tx_day = transaction.transaction_date
        if isinstance(tx_day, datetime):
            tx_day = tx_day.date()
        elif isinstance(tx_day, str):
            try:
                tx_day = (
                    datetime.fromisoformat(tx_day.replace('Z', '+00:00')).date()
                    if 'T' in tx_day
                    else datetime.strptime(tx_day[:10], '%Y-%m-%d').date()
                )
            except (ValueError, TypeError):
                tx_day = None

        cashflow_q = Cashflow.query.filter(
            Cashflow.client_id == transaction.client_id,
            Cashflow.description.in_([old_description, new_description, alt_old, alt_new]),
        )
        if tx_day is not None:
            cashflow_q = cashflow_q.filter(sa_func.date(Cashflow.date) == tx_day)
        else:
            cashflow_q = cashflow_q.filter(Cashflow.date == transaction.transaction_date)
        cashflow = cashflow_q.first()
        if cashflow:
            self.db.delete(cashflow)
    
    def _update_cashflow_incremental(self, transaction):
        """Fast O(1) update for single transaction"""
        from models import Cashflow
        from services.cashflow_service import DUMMY_DATES
        
        if transaction.type in ['BUY', 'SELL']:
            # PROTECTION: Skip cashflow creation for dummy dates (manual entries)
            # Ensure transaction_date_obj is always a date object
            if isinstance(transaction.transaction_date, datetime):
                transaction_date_obj = transaction.transaction_date.date()
            elif isinstance(transaction.transaction_date, date):
                transaction_date_obj = transaction.transaction_date
            elif isinstance(transaction.transaction_date, str):
                # Handle edge case where string might have slipped through
                try:
                    if 'T' in transaction.transaction_date:
                        transaction_date_obj = datetime.fromisoformat(transaction.transaction_date.replace('Z', '+00:00')).date()
                    else:
                        transaction_date_obj = datetime.strptime(transaction.transaction_date, '%Y-%m-%d').date()
                except (ValueError, TypeError) as e:
                    logger.error(f"Error parsing transaction_date '{transaction.transaction_date}': {e}")
                    raise ValueError(f"Invalid transaction_date format: {transaction.transaction_date}")
            else:
                logger.error(f"Unexpected transaction_date type: {type(transaction.transaction_date)}")
                raise ValueError(f"transaction_date must be date or datetime, got {type(transaction.transaction_date)}")
            
            if transaction_date_obj in DUMMY_DATES:
                logger.info(f"Skipping cashflow creation for dummy date {transaction_date_obj} - manual entry")
                return
            
            # BUY = negative (cash outflow), SELL = positive (cash inflow)
            amount = -transaction.amount if transaction.type == 'BUY' else transaction.amount
            
            # Ensure date is datetime
            cashflow_date = transaction.transaction_date
            if isinstance(cashflow_date, date):
                cashflow_date = datetime.combine(cashflow_date, datetime.min.time())
            elif isinstance(cashflow_date, datetime):
                # Already a datetime, ensure it has time component
                if cashflow_date.time() != datetime.min.time():
                    cashflow_date = datetime.combine(cashflow_date.date(), datetime.min.time())
            elif isinstance(cashflow_date, str):
                # Handle edge case where string might have slipped through
                try:
                    if 'T' in cashflow_date:
                        parsed_date = datetime.fromisoformat(cashflow_date.replace('Z', '+00:00')).date()
                    else:
                        parsed_date = datetime.strptime(cashflow_date, '%Y-%m-%d').date()
                    cashflow_date = datetime.combine(parsed_date, datetime.min.time())
                except (ValueError, TypeError) as e:
                    logger.error(f"Error parsing cashflow_date '{cashflow_date}': {e}")
                    raise ValueError(f"Invalid cashflow_date format: {cashflow_date}")
            
            # Get security symbol for description
            security_symbol = transaction.security.symbol if transaction.security else "UNKNOWN"
            qty_disp = transaction.quantity
            try:
                qty_disp = (
                    int(transaction.quantity)
                    if float(transaction.quantity) == int(float(transaction.quantity))
                    else transaction.quantity
                )
            except (TypeError, ValueError):
                pass

            from models import User
            system_user = User.query.filter_by(is_admin=True).first() or User.query.first()
            if not system_user:
                raise ValueError("No user found for cashflow created_by")

            cashflow = Cashflow(
                client_id=transaction.client_id,
                amount=amount,
                date=cashflow_date,
                # Convention used across the app:
                # negative amount => investment => INFLOW
                # positive amount => withdrawal/sale proceeds => OUTFLOW
                type='INFLOW' if amount < 0 else 'OUTFLOW',
                description=f"{transaction.type} {qty_disp} shares of {security_symbol}",
                created_by=system_user.id,
            )
            self.db.add(cashflow)
    
    def _update_all_holdings_from_forward_calculation(self, client_id, portfolio):
        """Update holdings table with forward calculation results"""
        try:
            from models import Holding
            from sqlalchemy.exc import IntegrityError
            from decimal import Decimal
            
            portfolio_holdings = portfolio.get('holdings', [])
            logger.info(f"Portfolio holdings list: {len(portfolio_holdings)} items")
            
            # Clear existing holdings using the same session for consistency
            # Use self.db.query() instead of Holding.query for session consistency
            deleted_count = self.db.query(Holding).filter_by(client_id=client_id).count()
            logger.info(f"Found {deleted_count} existing holdings for client {client_id}, deleting...")
            
            if deleted_count > 0:
                self.db.query(Holding).filter_by(client_id=client_id).delete(synchronize_session=False)
                self.db.flush()  # CRITICAL: Flush deletion immediately so count is accurate
                logger.debug(f"Deleted {deleted_count} existing holdings for client {client_id}, flushed to DB")
                
                # Verify deletion worked
                remaining_after_delete = self.db.query(Holding).filter_by(client_id=client_id).count()
                if remaining_after_delete > 0:
                    logger.warning(f"Warning: {remaining_after_delete} holdings still exist after deletion. Attempting force delete...")
                    # Force delete any remaining
                    self.db.query(Holding).filter_by(client_id=client_id).delete(synchronize_session=False)
                    self.db.flush()
                    remaining_after_force = self.db.query(Holding).filter_by(client_id=client_id).count()
                    if remaining_after_force > 0:
                        logger.error(f"ERROR: {remaining_after_force} holdings still exist after force delete! This may indicate a database constraint issue.")
                    else:
                        logger.info(f"Force delete successful: all holdings removed")
            else:
                logger.debug(f"No existing holdings to delete for client {client_id}")
            
            # Check for duplicate security_ids in portfolio (which would cause IntegrityError)
            seen_security_ids = set()
            duplicate_security_ids = set()
            for holding_data in portfolio_holdings:
                security_id = holding_data.get('security_id')
                if security_id is not None:
                    security_id_int = int(security_id)
                    if security_id_int in seen_security_ids:
                        duplicate_security_ids.add(security_id_int)
                    seen_security_ids.add(security_id_int)
            
            if duplicate_security_ids:
                logger.warning(f"Found {len(duplicate_security_ids)} duplicate security_ids in portfolio: {duplicate_security_ids}")
                # Deduplicate by keeping the first occurrence of each security_id
                deduplicated_holdings = {}
                for holding_data in portfolio_holdings:
                    security_id = holding_data.get('security_id')
                    if security_id is not None:
                        security_id_int = int(security_id)
                        if security_id_int not in deduplicated_holdings:
                            deduplicated_holdings[security_id_int] = holding_data
                        else:
                            # If duplicate, merge quantities (sum them) - this handles edge cases
                            existing = deduplicated_holdings[security_id_int]
                            existing_qty = existing.get('quantity', 0) or 0
                            new_qty = holding_data.get('quantity', 0) or 0
                            # For now, just log and keep first - but ideally we'd want to handle this better
                            logger.warning(f"Duplicate security_id {security_id_int}: Keeping first occurrence (qty={existing_qty}), skipping second (qty={new_qty})")
                portfolio_holdings = list(deduplicated_holdings.values())
                logger.info(f"Deduplicated portfolio holdings: {len(portfolio_holdings)} unique holdings")
            
            # Create new holdings from forward calculation results
            holdings_count = 0
            holdings_skipped = []
            holdings_to_create = []
            holdings_errors = []
            holdings_created_details = []
            
            logger.info(f"Starting to create holdings for {len(portfolio_holdings)} portfolio holdings")
            
            for i, holding_data in enumerate(portfolio_holdings):
                try:
                    security_id = holding_data.get('security_id')
                    quantity = holding_data.get('quantity', 0)
                    average_price = holding_data.get('average_price', 0)
                    symbol = holding_data.get('symbol', 'Unknown')
                    
                    logger.debug(f"Processing holding {i+1}/{len(portfolio_holdings)}: security_id={security_id}, symbol={symbol}, qty={quantity}, avg_price={average_price}")
                    
                    # Validate required fields
                    if security_id is None:
                        error_msg = f"Index {i}: Missing security_id"
                        holdings_errors.append(error_msg)
                        logger.warning(f"[HOLDING {i+1}] SKIPPED: {error_msg} - Symbol: {symbol}")
                        continue
                    
                    # Convert to Decimal for database compatibility
                    # Handle float precision issues and ensure proper Decimal conversion
                    try:
                        # For quantity: Handle float, Decimal, int, string, or None
                        if quantity is None:
                            quantity_decimal = Decimal('0')
                        elif isinstance(quantity, Decimal):
                            quantity_decimal = quantity  # Already Decimal
                        elif isinstance(quantity, (int, float)):
                            # Convert float to Decimal via string to preserve precision
                            quantity_decimal = Decimal(str(quantity))
                        elif isinstance(quantity, str):
                            quantity_decimal = Decimal(quantity)
                        else:
                            quantity_decimal = Decimal(str(quantity))
                        
                        # For average_price: Same handling
                        if average_price is None:
                            avg_price_decimal = Decimal('0')
                        elif isinstance(average_price, Decimal):
                            avg_price_decimal = average_price  # Already Decimal
                        elif isinstance(average_price, (int, float)):
                            # Convert float to Decimal via string to preserve precision
                            avg_price_decimal = Decimal(str(average_price))
                        elif isinstance(average_price, str):
                            avg_price_decimal = Decimal(average_price)
                        else:
                            avg_price_decimal = Decimal(str(average_price))
                        
                        # Round to appropriate precision to match database constraints
                        # Holding.quantity is Numeric(15,4) - 4 decimal places
                        quantity_decimal = quantity_decimal.quantize(Decimal('0.0001'))
                        # Holding.average_price is Numeric(15,2) - 2 decimal places
                        avg_price_decimal = avg_price_decimal.quantize(Decimal('0.01'))
                        
                        logger.debug(f"[HOLDING {i+1}] Converted: quantity={quantity_decimal} (from {type(quantity)}={quantity}), price={avg_price_decimal} (from {type(average_price)}={average_price})")
                    except (ValueError, TypeError, Exception) as e:
                        error_msg = f"Index {i} (security_id={security_id}, symbol={symbol}): Invalid quantity/price conversion - {type(e).__name__}: {str(e)}. Original: qty={quantity} (type={type(quantity)}), price={average_price} (type={type(average_price)})"
                        holdings_errors.append(error_msg)
                        logger.error(f"[HOLDING {i+1}] SKIPPED: {error_msg}")
                        import traceback
                        logger.error(f"[HOLDING {i+1}] Conversion error traceback: {traceback.format_exc()}")
                        continue
                    
                    # Only create holding if quantity > 0 (safety check)
                    if quantity_decimal > 0:
                        try:
                            logger.debug(f"[HOLDING {i+1}] Creating Holding object: client_id={client_id}, security_id={int(security_id)}, quantity={quantity_decimal}, avg_price={avg_price_decimal}")
                            holding = Holding(
                                client_id=client_id,
                                security_id=int(security_id),
                                quantity=quantity_decimal,
                                average_price=avg_price_decimal
                            )
                            logger.debug(f"[HOLDING {i+1}] Holding object created successfully: {holding}")
                            
                            holdings_to_create.append((int(security_id), float(quantity_decimal), symbol))
                            self.db.add(holding)
                            holdings_count += 1
                            holdings_created_details.append({
                                'index': i+1,
                                'security_id': int(security_id),
                                'symbol': symbol,
                                'quantity': float(quantity_decimal),
                                'average_price': float(avg_price_decimal)
                            })
                            logger.info(f"[HOLDING {i+1}] ✓ ADDED to session: {symbol} (security_id={security_id}), qty={quantity_decimal}, price=₹{avg_price_decimal:.2f}")
                        except Exception as e:
                            error_msg = f"Index {i} (security_id={security_id}, symbol={symbol}): Failed to create Holding object - {type(e).__name__}: {str(e)}"
                            holdings_errors.append(error_msg)
                            logger.error(f"[HOLDING {i+1}] ✗ FAILED to create Holding object: {error_msg}")
                            import traceback
                            logger.error(f"[HOLDING {i+1}] Traceback: {traceback.format_exc()}")
                            continue
                    else:
                        reason = f"quantity={quantity_decimal} (zero or negative)"
                        holdings_skipped.append({
                            'index': i+1,
                            'security_id': int(security_id) if security_id else None,
                            'symbol': symbol,
                            'quantity': float(quantity_decimal),
                            'reason': reason
                        })
                        logger.info(f"[HOLDING {i+1}] SKIPPED (zero quantity): {symbol} (security_id={security_id}), {reason}")
                        
                except Exception as e:
                    security_id_val = security_id if 'security_id' in locals() else 'unknown'
                    symbol_val = symbol if 'symbol' in locals() else 'unknown'
                    error_msg = f"Index {i} (security_id={security_id_val}, symbol={symbol_val}): {type(e).__name__} - {str(e)}"
                    holdings_errors.append(error_msg)
                    logger.error(f"[HOLDING {i+1}] ✗ UNEXPECTED ERROR: {error_msg}")
                    import traceback
                    logger.error(f"[HOLDING {i+1}] Traceback: {traceback.format_exc()}")
                    continue
            
            # Summary before flush
            logger.info(f"=== HOLDINGS CREATION SUMMARY BEFORE FLUSH ===")
            logger.info(f"Total portfolio holdings: {len(portfolio_holdings)}")
            logger.info(f"Successfully added to session: {holdings_count}")
            logger.info(f"Skipped (zero quantity): {len(holdings_skipped)}")
            logger.info(f"Errors during processing: {len(holdings_errors)}")
            
            if holdings_created_details:
                logger.info(f"\n✓ Successfully created holdings ({len(holdings_created_details)}):")
                for detail in holdings_created_details[:20]:  # First 20
                    logger.info(f"  [{detail['index']}] {detail['symbol']} (ID:{detail['security_id']}) - Qty: {detail['quantity']}, Price: ₹{detail['average_price']:.2f}")
                if len(holdings_created_details) > 20:
                    logger.info(f"  ... and {len(holdings_created_details) - 20} more")
            
            if holdings_skipped:
                logger.info(f"\n⊘ Skipped holdings ({len(holdings_skipped)}):")
                for skip in holdings_skipped[:10]:  # First 10
                    logger.info(f"  [{skip.get('index', '?')}] {skip.get('symbol', 'Unknown')} (ID:{skip.get('security_id', '?')}) - {skip.get('reason', 'zero quantity')}")
                if len(holdings_skipped) > 10:
                    logger.info(f"  ... and {len(holdings_skipped) - 10} more")
            
            if holdings_errors:
                logger.error(f"\n✗ Errors during holding creation ({len(holdings_errors)}):")
                for error in holdings_errors:
                    logger.error(f"  - {error}")
            
            # CRITICAL: Flush changes to ensure they're included in the transaction
            logger.info(f"\n=== FLUSHING {holdings_count} HOLDINGS TO DATABASE ===")
            try:
                self.db.flush()
                logger.info(f"✓ Flush successful - {holdings_count} holdings flushed to database")
            except Exception as e:
                logger.error(f"✗ FLUSH FAILED: {type(e).__name__} - {str(e)}")
                logger.error(f"Attempted to flush {holdings_count} holdings but database rejected them")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                
                # Log details of what we tried to flush
                logger.error("Holdings that failed to flush:")
                for detail in holdings_created_details:
                    logger.error(f"  - {detail['symbol']} (ID:{detail['security_id']}) - Qty: {detail['quantity']}, Price: ₹{detail['average_price']:.2f}")
                raise
            
            # Verify holdings were created
            actual_count = self.db.query(Holding).filter_by(client_id=client_id).count()
            logger.info(f"\n=== HOLDINGS VERIFICATION ===")
            logger.info(f"Expected holdings in DB: {holdings_count}")
            logger.info(f"Actual holdings in DB: {actual_count}")
            logger.info(f"Portfolio calculated: {len(portfolio_holdings)} holdings")
            
            if actual_count != holdings_count:
                error_msg = f"Holding count mismatch: Expected {holdings_count}, Actual {actual_count}"
                logger.error(f"\n✗ {error_msg}")
                
                # Detailed analysis
                logger.error(f"\nDetailed Analysis:")
                logger.error(f"  - Portfolio holdings calculated: {len(portfolio_holdings)}")
                logger.error(f"  - Holdings added to session: {holdings_count}")
                logger.error(f"  - Holdings in database after flush: {actual_count}")
                logger.error(f"  - Holdings skipped (zero qty): {len(holdings_skipped)}")
                logger.error(f"  - Processing errors: {len(holdings_errors)}")
                
                if holdings_errors:
                    logger.error(f"\nAll errors encountered:")
                    for i, error in enumerate(holdings_errors, 1):
                        logger.error(f"  {i}. {error}")
                
                if holdings_created_details:
                    logger.error(f"\nHoldings that were added to session (should be in DB):")
                    for detail in holdings_created_details:
                        # Check if this holding actually exists in DB
                        exists = self.db.query(Holding).filter_by(
                            client_id=client_id,
                            security_id=detail['security_id']
                        ).first() is not None
                        status = "✓ EXISTS" if exists else "✗ MISSING"
                        logger.error(f"  {status}: {detail['symbol']} (ID:{detail['security_id']}) - Qty: {detail['quantity']}")
                
                # CRITICAL: Raise exception to prevent committing incomplete holdings
                raise ValueError(f"{error_msg}. Portfolio calculated {len(portfolio_holdings)} holdings, but only {actual_count} were created. Check logs above for detailed error analysis.")
            
            logger.info(f"\n✓ Holdings creation SUCCESSFUL: {actual_count} holdings created and verified in database")
            
        except Exception as e:
            logger.error(f"Error updating holdings from forward calculation: {type(e).__name__} - {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
    
    def bulk_delete_transactions(self, transaction_ids, client_id=None):
        """
        Delete multiple transactions with smart handling for Corporate Actions.
        
        Logic:
        1. Delete transactions but preserve cashflow impact
        2. Adjust cashflows for deleted transactions
        3. Simple holdings adjustment for securities without CA
        4. Forward calculation for securities with CA history
        
        Args:
            transaction_ids: List of transaction IDs to delete
            client_id: Optional client_id to restrict deletions
            
        Returns:
            dict with success status, deleted_count, and security details
        """
        try:
            from models import Transaction, Cashflow, Holding, CorporateAction
            
            if not transaction_ids:
                return {'success': False, 'message': 'No transaction IDs provided'}
            
            # Get transactions before deletion
            query = Transaction.query.filter(Transaction.id.in_(transaction_ids))
            if client_id is not None:
                query = query.filter_by(client_id=client_id)
            transactions = query.all()
            
            if not transactions:
                return {'success': False, 'message': 'No transactions found'}
            
            # Determine affected client
            affected_client_id = client_id if client_id is not None else transactions[0].client_id
            
            found_ids = {t.id for t in transactions}
            not_found_ids = [tid for tid in transaction_ids if tid not in found_ids]
            
            # Track securities with and without CA history
            securities_with_ca = set()
            securities_without_ca = set()
            
            # Step 1: Check which securities have CA history
            for transaction in transactions:
                security_id = transaction.security_id
                # Check if this security has any corporate actions
                has_ca = CorporateAction.query.filter_by(
                    security_id=security_id,
                    is_active=True
                ).first() is not None
                
                if has_ca:
                    securities_with_ca.add(security_id)
                else:
                    securities_without_ca.add(security_id)
            
            logger.info(f"Securities with CA: {securities_with_ca}, without CA: {securities_without_ca}")
            
            # Step 2: Process each transaction
            deleted_transactions = []
            from decimal import Decimal
            for transaction in transactions:
                # Store transaction details before deletion
                deleted_transactions.append({
                    'id': transaction.id,
                    'client_id': transaction.client_id,
                    'security_id': transaction.security_id,
                    'type': transaction.type,
                    'quantity': Decimal(str(transaction.quantity)) if transaction.quantity is not None else Decimal('0'),
                    'price': Decimal(str(transaction.price)) if transaction.price is not None else Decimal('0'),
                    'amount': Decimal(str(transaction.amount)) if transaction.amount is not None else Decimal('0'),
                    'transaction_date': transaction.transaction_date
                })
                
                # Delete transaction
                self.db.delete(transaction)
            
            # Step 3: Adjust cashflows for deleted transactions
            for txn_data in deleted_transactions:
                self._adjust_cashflow_for_deletion(txn_data)
            
            # Step 4: Simple holdings adjustment for securities without CA
            for txn_data in deleted_transactions:
                if txn_data['security_id'] in securities_without_ca:
                    self._adjust_holdings_simple(txn_data)
            
            # Step 5: Forward calculation for securities with CA
            if securities_with_ca:
                from services.forward_holding_calculation_service import get_client_portfolio_by_date
                
                # Use self.db session to ensure consistency after transaction deletions
                portfolio = get_client_portfolio_by_date(affected_client_id, date.today(), db_session=self.db)
                self._update_all_holdings_from_forward_calculation(affected_client_id, portfolio)
            
            return {
                'success': True,
                'deleted_count': len(transactions),
                'not_found_ids': not_found_ids,
                'securities_with_ca': list(securities_with_ca),
                'securities_without_ca': list(securities_without_ca)
            }
            
        except Exception as e:
            logger.error(f"Error bulk deleting transactions: {str(e)}")
            return {'success': False, 'message': str(e)}
    
    def _adjust_cashflow_for_deletion(self, transaction_data):
        """
        Adjust cashflow for a deleted transaction.
        Instead of deleting cashflow, we adjust the amount.
        
        Args:
            transaction_data: dict with transaction details
        """
        try:
            from models import Cashflow
            
            client_id = transaction_data['client_id']
            transaction_date = transaction_data['transaction_date']
            transaction_type = transaction_data['type']
            from decimal import Decimal
            amount = transaction_data['amount']
            if not isinstance(amount, Decimal):
                amount = Decimal(str(amount))
            
            # For BUY: negative cashflow (money going out)
            # For SELL: positive cashflow (money coming in)
            cashflow_amount = amount if transaction_type == 'SELL' else -amount
            
            # Find existing cashflow for this date
            existing_cashflow = Cashflow.query.filter_by(
                client_id=client_id,
                date=transaction_date
            ).first()
            
            if existing_cashflow:
                # Adjust existing cashflow amount (convert to Decimal for consistency)
                existing_cashflow.amount -= Decimal(str(cashflow_amount))
                
                # If amount becomes zero or very small, delete it
                if abs(float(existing_cashflow.amount)) < 0.01:
                    self.db.delete(existing_cashflow)
                else:
                    # Update cashflow type based on sign
                    existing_cashflow.type = 'INFLOW' if float(existing_cashflow.amount) < 0 else 'OUTFLOW'
            else:
                # Create a reverse cashflow if none exists
                # This shouldn't happen normally, but handle edge case
                logger.warning(f"No existing cashflow found for client {client_id} on {transaction_date}")
                
        except Exception as e:
            logger.error(f"Error adjusting cashflow for deletion: {str(e)}")
            raise
    
    def _adjust_holdings_simple(self, transaction_data):
        """
        Simple holdings adjustment for securities without CA.
        Directly reverse the transaction impact.
        
        Args:
            transaction_data: dict with transaction details
        """
        try:
            from models import Holding
            
            client_id = transaction_data['client_id']
            security_id = transaction_data['security_id']
            transaction_type = transaction_data['type']
            from decimal import Decimal
            quantity = transaction_data['quantity']
            price = transaction_data['price']
            if not isinstance(quantity, Decimal):
                quantity = Decimal(str(quantity))
            if not isinstance(price, Decimal):
                price = Decimal(str(price))
            
            # Get holding
            holding = Holding.query.filter_by(
                client_id=client_id,
                security_id=security_id
            ).first()
            
            if not holding:
                logger.warning(f"No holding found for client {client_id}, security {security_id}")
                return
            
            if transaction_type == 'BUY':
                # Reverse BUY: subtract quantity
                holding.quantity -= quantity
                
                if holding.quantity > 0:
                    # Recalculate average price (convert to Decimal for consistency)
                    total_cost = holding.quantity * holding.average_price
                    new_cost = total_cost - (quantity * price)
                    holding.average_price = new_cost / holding.quantity if holding.quantity > 0 else 0
                else:
                    # Delete holding if quantity becomes 0 or negative
                    self.db.delete(holding)
                    
            elif transaction_type == 'SELL':
                # Reverse SELL: add back quantity
                holding.quantity += quantity
                
                # Note: Average price remains the same for SELL reversals
                # We don't adjust average price when reversing a SELL
                
        except Exception as e:
            logger.error(f"Error adjusting holdings: {str(e)}")
            raise

