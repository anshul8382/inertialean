"""
Portfolio Reconstruction Service
Reconstructs portfolio state at historical points in time by either:
1. Using existing portfolio snapshots (if available)
2. Reverse-engineering from current holdings + trades (if no snapshots)
"""
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy import and_, desc, asc
from models import (
    db, Client, PortfolioSnapshot, HoldingSnapshot, Holding, Transaction, 
    Cashflow, Security, HistoricalPrice, CorporateAction
)
import logging

logger = logging.getLogger(__name__)

# Import audit logger
from utils.audit_logger import audit_api_call, log_api_call

# Import Adjustment Service for corporate action handling
from services.adjustments_service import (
    adjust_holdings_for_corporate_actions,
    get_price_for_security
)

# Import Flask components for API endpoints
from flask import Blueprint, request, jsonify

# Create blueprint for portfolio reconstruction API
portfolio_reconstruction_bp = Blueprint('portfolio_reconstruction', __name__)

class PortfolioReconstructionService:
    
    @staticmethod
    def get_historical_price(security_id, target_date):
        """
        Get historical price for a security on a specific date
        Uses daily cron job maintained historical prices directly from database
        Falls back to prices within 3 days if exact date not found
        """
        from datetime import timedelta
        
        # First try to get exact date
        price_data = HistoricalPrice.query.filter_by(
            security_id=security_id,
            date=target_date
        ).first()
        
        if price_data:
            return float(price_data.close_price)
        
        # If no exact date, try to find price within 3 days back
        for days_back in range(1, 4):  # Try 1, 2, 3 days back
            check_date = target_date - timedelta(days=days_back)
            price_data = HistoricalPrice.query.filter_by(
                security_id=security_id,
                date=check_date
            ).first()
            
            if price_data:
                logger.info(f"Using historical price for security {security_id} from {price_data.date} (3 days back) for date {target_date}")
                return float(price_data.close_price)
        
        # If no price within 3 days, get the closest previous date
        price_data = HistoricalPrice.query.filter(
            HistoricalPrice.security_id == security_id,
            HistoricalPrice.date <= target_date
        ).order_by(desc(HistoricalPrice.date)).first()
        
        if price_data:
            logger.info(f"Using historical price for security {security_id} from {price_data.date} for date {target_date}")
            return float(price_data.close_price)
        
        # If no historical data, try current price as fallback
        security = Security.query.get(security_id)
        if security and security.current_price:
            logger.warning(f"No historical price found for security {security_id} on {target_date}, using current price")
            return float(security.current_price)
        
        logger.error(f"No price data available for security {security_id} on {target_date}")
        return 0.0
    
    @staticmethod
    @audit_api_call
    def get_portfolio_state_at_date(client_id, target_date, apply_adjustments=True, save_snapshot=False):
        """
        Get portfolio state at a specific date using snapshots or reconstruction
        
        Args:
            client_id: Client ID
            target_date: Target date for reconstruction
            apply_adjustments: Whether to apply corporate action adjustments (default: True)
            save_snapshot: Whether to save snapshot files for audit (default: False - only save after complete processing)
        
        Returns:
            Portfolio state with holdings, total_value, total_cost, gains, and source
        """
        logger.info(f"Getting portfolio state for client {client_id} on {target_date}, apply_adjustments={apply_adjustments}")
        
        # First, try to find existing snapshots
        snapshot = PortfolioSnapshot.query.filter_by(
            client_id=client_id, 
            date=target_date
        ).first()
        
        if snapshot:
            logger.info(f"Found existing snapshot for {target_date}")
            portfolio_state = PortfolioReconstructionService._extract_from_snapshot(snapshot)
        else:
            # If no snapshot, try to find the closest snapshot before the target date
            closest_snapshot = PortfolioSnapshot.query.filter(
                PortfolioSnapshot.client_id == client_id,
                PortfolioSnapshot.date <= target_date
            ).order_by(desc(PortfolioSnapshot.date)).first()
            
            if closest_snapshot:
                logger.info(f"Found closest snapshot from {closest_snapshot.date}, reconstructing forward")
                portfolio_state = PortfolioReconstructionService._reconstruct_from_closest_snapshot(
                    client_id, closest_snapshot, target_date
                )
            else:
                # If no snapshots at all, reconstruct from current state backwards
                logger.info(f"No snapshots found, reconstructing backwards from current holdings")
                portfolio_state = PortfolioReconstructionService._reconstruct_backwards(client_id, target_date)
        
        # Apply corporate action adjustments if requested
        # NOTE: reconstructed_backwards already handles corporate actions internally
        # Only apply adjustments for snapshot-based reconstructions
        if apply_adjustments and portfolio_state.get('source') != 'reconstructed_backwards':
            logger.info(f"Applying corporate action adjustments for {target_date}")
            portfolio_state = PortfolioReconstructionService._apply_corporate_action_adjustments(
                client_id, target_date, portfolio_state
            )
        elif portfolio_state.get('source') == 'reconstructed_backwards':
            logger.info(f"Skipping additional adjustments - already handled in backward reconstruction")
        
        # Note: Snapshots are now saved at date breaks during reconstruction, not at the end
        # This reduces snapshot creation and ensures they're only created after complete processing for each date
        
        return portfolio_state
    
    @staticmethod
    def _log_portfolio_snapshot(client_id, target_date, portfolio_state, save_snapshot=True):
        """
        Save detailed portfolio snapshot to audit trail
        Creates TWO files:
        1. logs/portfolio_snapshots/client_{client_id}_{date}.txt - Simple snapshot
        2. logs/audit_trails/reconstruction_client_{client_id}_{date}.txt - Detailed audit trail
        
        Args:
            client_id: Client ID
            target_date: Target date
            portfolio_state: Portfolio state data
            save_snapshot: Whether to actually save files (default: True)
        """
        import os
        import glob
        from datetime import datetime as dt
        
        # Create human-readable table format
        from decimal import Decimal
        
        holdings = portfolio_state.get('holdings', [])
        sorted_holdings = sorted(holdings, key=lambda x: float(x.get('current_value', 0)), reverse=True)
        
        total_value = float(portfolio_state.get('total_value', 0))
        total_invested = float(portfolio_state.get('total_invested', 0))
        total_withdrawn = float(portfolio_state.get('total_withdrawn', 0))
        net_investment = float(portfolio_state.get('net_investment', 0))
        holdings_count = len(holdings)
        source = portfolio_state.get('source', 'unknown')
        
        # ========================================
        # FILE 1: Simple Portfolio Snapshot
        # ========================================
        lines = []
        lines.append("=" * 140)
        lines.append(f"PORTFOLIO RECONSTRUCTION SNAPSHOT - Client {client_id}")
        lines.append(f"Date: {target_date}")
        lines.append(f"Generated: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Source: {source}")
        lines.append("=" * 140)
        lines.append("")
        lines.append(f"PORTFOLIO SUMMARY:")
        lines.append(f"  Total Value: ₹{total_value:,.2f}")
        lines.append(f"  Holdings Count: {holdings_count}")
        lines.append(f"  Total Invested: ₹{total_invested:,.2f}")
        lines.append(f"  Total Withdrawn: ₹{total_withdrawn:,.2f}")
        lines.append(f"  Net Investment: ₹{net_investment:,.2f}")
        lines.append(f"  Absolute Return: ₹{total_value - net_investment:,.2f}")
        if net_investment > 0:
            lines.append(f"  Return %: {((total_value - net_investment) / net_investment * 100):.2f}%")
        lines.append("")
        lines.append("=" * 140)
        lines.append(f"{'Symbol':<15} {'Quantity':>12} {'Avg Price':>12} {'Current Price':>12} {'Value':>15} {'P&L':>15} {'P&L %':>10}")
        lines.append("-" * 140)
        
        total_check = 0
        for h in sorted_holdings:
            symbol = h.get('symbol', 'N/A')
            qty = float(h.get('quantity', 0))
            avg_price = float(h.get('average_price', 0))
            curr_price = float(h.get('current_price', 0))
            value = float(h.get('current_value', 0))
            cost = qty * avg_price
            pnl = value - cost
            pnl_pct = (pnl / cost * 100) if cost > 0 else 0
            
            total_check += value
            
            lines.append(f"{symbol:<15} {qty:>12.2f} ₹{avg_price:>11,.2f} ₹{curr_price:>11,.2f} ₹{value:>14,.2f} ₹{pnl:>14,.2f} {pnl_pct:>9.2f}%")
        
        lines.append("-" * 140)
        lines.append(f"{'TOTAL':<15} {'':<12} {'':<12} {'':<12} ₹{total_check:>14,.2f}")
        lines.append("=" * 140)
        lines.append("")
        lines.append(f"Verification: Total from holdings sum = ₹{total_check:,.2f}")
        lines.append(f"             Portfolio total_value = ₹{total_value:,.2f}")
        lines.append(f"             Match: {'✅ YES' if abs(total_check - total_value) < 1 else '❌ NO - DISCREPANCY!'}")
        lines.append("")
        
        # Save simple snapshot (only if save_snapshot is True)
        if save_snapshot:
            snapshot_dir = '/home/inertia/app/logs/portfolio_snapshots'
            os.makedirs(snapshot_dir, exist_ok=True)
            
            # Clean up existing snapshot file for this client and date
            filename = f'client_{client_id}_{target_date}.txt'
            filepath = os.path.join(snapshot_dir, filename)
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    logger.info(f"Deleted existing snapshot file: {filepath}")
                except Exception as e:
                    logger.warning(f"Failed to delete existing snapshot file {filepath}: {e}")
        
        try:
            with open(filepath, 'w') as f:
                    f.write('\n'.join(lines))
            logger.info(f"Portfolio snapshot saved to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save portfolio snapshot to {filepath}: {e}")
        
        # ========================================
        # FILE 2: Detailed Audit Trail
        # ========================================
        audit_lines = []
        audit_lines.append("=" * 140)
        audit_lines.append(f"PORTFOLIO RECONSTRUCTION - DETAILED AUDIT TRAIL")
        audit_lines.append(f"Client ID: {client_id}")
        audit_lines.append(f"Target Date: {target_date}")
        audit_lines.append(f"Generated: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
        audit_lines.append("=" * 140)
        audit_lines.append("")
        audit_lines.append("RECONSTRUCTION METHOD:")
        audit_lines.append(f"  Source: {source}")
        audit_lines.append(f"  Method: {'Backward reconstruction from current holdings' if 'backward' in source else 'From snapshot or forward reconstruction'}")
        audit_lines.append("")
        audit_lines.append("=" * 140)
        audit_lines.append("FINAL PORTFOLIO HOLDINGS")
        audit_lines.append("=" * 140)
        audit_lines.append("")
        audit_lines.append(f"{'Symbol':<15} {'Security ID':>12} {'Quantity':>15} {'Avg Price':>15} {'Current Price':>15} {'Value':>18}")
        audit_lines.append("-" * 140)
        
        for h in sorted_holdings:
            symbol = h.get('symbol', 'N/A')
            sec_id = h.get('security_id', 0)
            qty = float(h.get('quantity', 0))
            avg_price = float(h.get('average_price', 0))
            curr_price = float(h.get('current_price', 0))
            value = float(h.get('current_value', 0))
            
            audit_lines.append(f"{symbol:<15} {sec_id:>12} {qty:>15.4f} ₹{avg_price:>14,.2f} ₹{curr_price:>14,.2f} ₹{value:>17,.2f}")
        
        audit_lines.append("-" * 140)
        audit_lines.append(f"{'TOTAL':<15} {'':<12} {'':<15} {'':<15} {'':<15} ₹{total_value:>17,.2f}")
        audit_lines.append("=" * 140)
        audit_lines.append("")
        audit_lines.append("PORTFOLIO METRICS:")
        audit_lines.append(f"  Holdings Count: {holdings_count}")
        audit_lines.append(f"  Total Value: ₹{total_value:,.2f}")
        audit_lines.append(f"  Total Invested: ₹{total_invested:,.2f}")
        audit_lines.append(f"  Total Withdrawn: ₹{total_withdrawn:,.2f}")
        audit_lines.append(f"  Net Investment: ₹{net_investment:,.2f}")
        audit_lines.append(f"  Absolute Return: ₹{total_value - net_investment:,.2f}")
        if net_investment > 0:
            audit_lines.append(f"  Return %: {((total_value - net_investment) / net_investment * 100):.2f}%")
        audit_lines.append("")
        audit_lines.append("=" * 140)
        audit_lines.append("END OF AUDIT TRAIL")
        audit_lines.append("=" * 140)
        
        # Save detailed audit trail (only if save_snapshot is True)
        if save_snapshot:
            audit_dir = '/home/inertia/app/logs/audit_trails'
            os.makedirs(audit_dir, exist_ok=True)
            
            # Clean up existing reconstruction audit file for this client and date
            audit_filename = f'reconstruction_client_{client_id}_{target_date}.txt'
            audit_filepath = os.path.join(audit_dir, audit_filename)
            if os.path.exists(audit_filepath):
                try:
                    os.remove(audit_filepath)
                    logger.info(f"Deleted existing reconstruction audit file: {audit_filepath}")
                except Exception as e:
                    logger.warning(f"Failed to delete existing reconstruction audit file {audit_filepath}: {e}")
            
            try:
                with open(audit_filepath, 'w') as f:
                    f.write('\n'.join(audit_lines))
                logger.info(f"Reconstruction audit trail saved to {audit_filepath}")
            except Exception as e:
                logger.error(f"Failed to save reconstruction audit trail to {audit_filepath}: {e}")
            
            # Also log summary to main log
            logger.info(f"PORTFOLIO_SNAPSHOT: Client {client_id} on {target_date} | " +
                           f"Value: ₹{total_value:,.2f} | " +
                           f"Holdings: {holdings_count} | " +
                           f"Source: {source} | " +
                           f"Files: {filename}, {audit_filename}")
        else:
            # Just log minimal info when snapshot is skipped
            logger.info(f"PORTFOLIO_RECONSTRUCTION: Client {client_id} on {target_date} | " +
                       f"Value: ₹{total_value:,.2f} | " +
                       f"Holdings: {holdings_count} | " +
                       f"Source: {source} | " +
                       f"Snapshot: SKIPPED")
    
    @staticmethod
    def _extract_from_snapshot(snapshot):
        """Extract portfolio data from an existing snapshot"""
        holdings_data = []
        total_value = Decimal('0.0')
        
        for holding_snapshot in snapshot.holdings_snapshots:
            holdings_data.append({
                'security_id': holding_snapshot.security_id,
                'symbol': holding_snapshot.security.symbol if holding_snapshot.security else 'Unknown',
                'quantity': float(holding_snapshot.quantity),
                'average_price': float(holding_snapshot.average_price),
                'current_price': float(holding_snapshot.current_price),
                'current_value': float(holding_snapshot.current_value),
                'unrealized_pnl': float(holding_snapshot.unrealized_pnl),
                'unrealized_pnl_percent': float(holding_snapshot.unrealized_pnl_percent)
            })
            total_value += holding_snapshot.current_value
        
        return {
            'date': snapshot.date,
            'total_value': float(snapshot.total_value),
            'total_invested': float(snapshot.total_invested),
            'total_withdrawn': float(snapshot.total_withdrawn),
            'net_investment': float(snapshot.net_investment),
            'absolute_return': float(snapshot.absolute_return),
            'absolute_return_percent': float(snapshot.absolute_return_percent),
            'xirr': float(snapshot.xirr) if snapshot.xirr else None,
            'holdings': holdings_data,
            'source': 'snapshot'
        }
    
    @staticmethod
    def _reconstruct_from_closest_snapshot(client_id, closest_snapshot, target_date):
        """Reconstruct portfolio from closest snapshot by applying trades forward"""
        logger.info(f"Reconstructing from snapshot {closest_snapshot.date} to {target_date}")
        
        # Start with snapshot data
        portfolio_state = PortfolioReconstructionService._extract_from_snapshot(closest_snapshot)
        
        # Get all transactions between snapshot date and target date
        transactions = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.transaction_date > closest_snapshot.date,
            Transaction.transaction_date <= target_date
        ).order_by(Transaction.transaction_date).all()
        
        # Apply transactions forward
        holdings_dict = {h['security_id']: h for h in portfolio_state['holdings']}
        
        for transaction in transactions:
            security_id = transaction.security_id
            if security_id in holdings_dict:
                holding = holdings_dict[security_id]
                if transaction.type == 'BUY':
                    # Add shares
                    old_quantity = holding['quantity']
                    new_quantity = old_quantity + float(transaction.quantity)
                    if new_quantity > 0:
                        # Recalculate average price
                        old_value = old_quantity * holding['average_price']
                        new_value = float(transaction.quantity) * float(transaction.price)
                        holding['average_price'] = (old_value + new_value) / new_quantity
                        holding['quantity'] = new_quantity
                    else:
                        del holdings_dict[security_id]
                elif transaction.type == 'SELL':
                    # Remove shares
                    holding['quantity'] -= float(transaction.quantity)
                    if holding['quantity'] <= 0:
                        del holdings_dict[security_id]
            else:
                # New security
                if transaction.type == 'BUY':
                    holdings_dict[security_id] = {
                        'security_id': security_id,
                        'symbol': transaction.security.symbol if transaction.security else 'Unknown',
                        'quantity': float(transaction.quantity),
                        'average_price': float(transaction.price),
                        'current_price': float(transaction.price),  # Will be updated with current price
                        'current_value': float(transaction.quantity) * float(transaction.price),
                        'unrealized_pnl': 0.0,
                        'unrealized_pnl_percent': 0.0
                    }
        
        # Update prices and values using historical prices
        for holding in holdings_dict.values():
            historical_price = PortfolioReconstructionService.get_historical_price(
                holding['security_id'], target_date
            )
            if historical_price > 0:
                holding['current_price'] = historical_price
                holding['current_value'] = holding['quantity'] * historical_price
                holding['unrealized_pnl'] = holding['current_value'] - (holding['quantity'] * holding['average_price'])
                if holding['average_price'] > 0:
                    holding['unrealized_pnl_percent'] = ((historical_price - holding['average_price']) / holding['average_price']) * 100
        
        # Update total value
        portfolio_state['holdings'] = list(holdings_dict.values())
        portfolio_state['total_value'] = sum(h['current_value'] for h in holdings_dict.values())
        portfolio_state['source'] = 'reconstructed_from_snapshot'
        
        return portfolio_state
    
    @staticmethod
    def _reconstruct_backwards(client_id, target_date):
        """
        Reconstruct portfolio backwards from current holdings to target_date
        
        Algorithm:
        1. Start with current holdings
        2. Get all transactions after target_date
        3. Group transactions by date (newest to oldest)
        4. Identify securities with corporate actions
        5. FOR EACH DATE (LIFO):
           - Process all transactions for that date
           - Create portfolio snapshot after date is complete
           - Update as_of_date to this date for next iteration
        6. Update prices using historical data for target_date
        7. Calculate final portfolio metrics
        """
        logger.info(f"=== PORTFOLIO RECONSTRUCTION START ===")
        logger.info(f"Client: {client_id}, Target date: {target_date}")
        
        # Step 1: Get current holdings
        current_holdings = Holding.query.filter_by(client_id=client_id).all()
        holdings_dict = {}
        
        for holding in current_holdings:
            if holding.security:
                holdings_dict[holding.security_id] = {
                    'security_id': holding.security_id,
                    'symbol': holding.security.symbol,
                    'quantity': float(holding.quantity) if holding.quantity else 0.0,
                    'average_price': float(holding.average_price) if holding.average_price else 0.0,
                }
        
        logger.info(f"Starting with {len(holdings_dict)} current holdings")
        
        # Step 1.5: Add source securities from mergers that occurred after target_date
        from services.merger_reconstruction_service import get_source_securities_for_reconstruction
        from datetime import datetime
        from collections import defaultdict
        
        # Get source securities that were merged after target_date
        source_securities = get_source_securities_for_reconstruction(
            client_id=client_id,
            target_date=target_date,
            standing_date=datetime.now().date()
        )
        
        for source_sec in source_securities:
            security_id = source_sec['security_id']
            if security_id not in holdings_dict:
                holdings_dict[security_id] = {
                    'security_id': security_id,
                    'symbol': source_sec['symbol'],
                    'quantity': source_sec['quantity'],  # Start with actual quantity at target date
                    'average_price': source_sec['average_price'],
                }
                logger.info(f"Added merged source security {source_sec['symbol']} to reconstruction "
                           f"(had {source_sec['quantity']:.2f} shares at target date, merged on {source_sec['merger_date']})")
        
        logger.info(f"After checking for merged securities: {len(holdings_dict)} holdings")
        
        # Step 2: Get all transactions after target_date
        
        future_transactions = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.transaction_date > target_date
        ).order_by(desc(Transaction.transaction_date)).all()
        
        logger.info(f"Found {len(future_transactions)} transactions to reverse")
        
        # Step 3: Group transactions by date (newest first)
        transactions_by_date = defaultdict(list)
        for txn in future_transactions:
            trade_date = txn.transaction_date.date() if isinstance(txn.transaction_date, datetime) else txn.transaction_date
            transactions_by_date[trade_date].append(txn)
        
        unique_dates = sorted(transactions_by_date.keys(), reverse=True)  # Newest first
        if unique_dates:
            logger.info(f"Transactions span {len(unique_dates)} unique dates from {unique_dates[0]} to {unique_dates[-1]}")
        else:
            logger.info(f"No transactions found between {target_date} and today - using current holdings as-is")
        
        # Step 4: Identify securities with corporate actions in rollback period
        corporate_actions = CorporateAction.query.filter(
            CorporateAction.is_active == True,
            CorporateAction.action_date > target_date,
            CorporateAction.action_date <= datetime.now().date()
        ).all()
        
        securities_with_corp_actions = set()
        for ca in corporate_actions:
            securities_with_corp_actions.add(ca.security_id)
            # For mergers, also mark the SOURCE security as having corporate actions
            if ca.action_type == 'MERGER':
                source_security_id = getattr(ca, 'source_security_id', None)
                if source_security_id:
                    securities_with_corp_actions.add(source_security_id)
                    logger.info(f"  Merger action: {source_security_id} → {ca.security_id} (both marked for CA adjustment)")
        
        logger.info(f"Found {len(securities_with_corp_actions)} securities with corporate actions")
        
        # Step 5: Process each date (LIFO - newest to oldest)
        from services.quantity_adjustment_service import adjust_quantity_for_portfolio_reconstruction
        
        # Track per-security as_of_date (when we last processed each security)
        # For securities with corporate actions, we need to know their specific as_of_date
        security_as_of_dates = {}  # security_id -> last_date_processed_for_this_security
        
        for trade_date in unique_dates:
            logger.info(f"\n{'='*80}")
            logger.info(f"Processing transactions for {trade_date}")
            logger.info(f"{'='*80}")
            
            transactions_for_this_date = transactions_by_date[trade_date]
            logger.info(f"  {len(transactions_for_this_date)} transactions on this date")
            
            # Process all transactions for this date
            for transaction in transactions_for_this_date:
                security_id = transaction.security_id
                trade_type = transaction.type
                trade_quantity = float(transaction.quantity)
                
                if security_id not in holdings_dict:
                    logger.warning(f"  Security {security_id} not in holdings during rollback of {trade_type}")
                    continue
                
                holding = holdings_dict[security_id]
                current_qty = holding['quantity']
                
                # Get as_of_date for THIS specific security
                if security_id not in security_as_of_dates:
                    # First time processing this security
                    security_as_of_date = datetime.now().date()
                else:
                    # Use the last date we processed for THIS security
                    security_as_of_date = security_as_of_dates[security_id]
                
                # Check if this security has corporate actions
                if security_id in securities_with_corp_actions:
                    # Use quantity adjustment API
                    logger.info(f"  Processing {holding['symbol']} {trade_type} {trade_quantity} (HAS CORP ACTIONS)")
                    logger.info(f"    as_of_date: {security_as_of_date}, target_date: {trade_date}, qty: {current_qty:.2f}")
                    
                    new_quantity = adjust_quantity_for_portfolio_reconstruction(
                        standing_date=security_as_of_date,
                        target_date=trade_date,
                        security_id=security_id,
                        current_quantity=current_qty,
                        trade_type=trade_type,
                        trade_quantity=trade_quantity
                    )
                    
                    holding['quantity'] = new_quantity
                    logger.info(f"    Result: {current_qty:.2f} → {new_quantity:.2f}")
                    
                else:
                    # Simple rollback (no corporate actions)
                    if trade_type == 'BUY':
                        new_quantity = current_qty - trade_quantity
                    else:  # SELL
                        new_quantity = current_qty + trade_quantity
                    
                    holding['quantity'] = new_quantity
                    holding['average_price'] = float(transaction.price)
                    logger.debug(f"  {holding['symbol']} {trade_type} {trade_quantity}: {current_qty:.2f} → {new_quantity:.2f}")
                
                # Remove holding if quantity becomes zero or negative
                if holding['quantity'] <= 0:
                    logger.info(f"  Removing {holding['symbol']} (quantity: {holding['quantity']:.2f})")
                    del holdings_dict[security_id]
                
                # Update as_of_date for THIS security
                security_as_of_dates[security_id] = trade_date
            
            # All transactions for this date processed
            logger.info(f"Completed processing {trade_date}. {len(holdings_dict)} holdings remaining")
            
            # CA-only pass for holdings with corporate actions that didn't trade on this date
            ca_only_adjustments = 0
            for sec_id, holding in list(holdings_dict.items()):
                if sec_id in securities_with_corp_actions:
                    prev_as_of = security_as_of_dates.get(sec_id, datetime.now().date())
                    if prev_as_of != trade_date:
                        logger.info(f"  CA-ONLY adjustment for {holding['symbol']}: {prev_as_of} → {trade_date}")
                        logger.info(f"    Current quantity: {holding['quantity']:.2f}")
                        
                        new_qty = adjust_quantity_for_portfolio_reconstruction(
                            standing_date=prev_as_of,
                            target_date=trade_date,
                            security_id=sec_id,
                            current_quantity=holding['quantity'],
                            trade_type='BUY',      # zero-quantity rollback → CA-only
                            trade_quantity=0.0
                        )
                        
                        logger.info(f"    CA-ONLY result: {holding['quantity']:.2f} → {new_qty:.2f}")
                        holding['quantity'] = new_qty
                        
                        # Remove holding if quantity becomes zero or negative
                        if holding['quantity'] <= 0:
                            logger.info(f"  Removing {holding['symbol']} (quantity: {holding['quantity']:.2f})")
                            del holdings_dict[sec_id]
                        else:
                            security_as_of_dates[sec_id] = trade_date
                        
                        ca_only_adjustments += 1
            
            if ca_only_adjustments > 0:
                logger.info(f"Applied {ca_only_adjustments} CA-only adjustments for {trade_date}")
            
            # Save snapshot at date break after processing all trades for this date
            # Calculate portfolio state for this trade_date
            intermediate_total_value = 0.0
            intermediate_holdings = []
            
            for holding in holdings_dict.values():
                # Fetch historical price for this specific trade_date
                historical_price = PortfolioReconstructionService.get_historical_price(
                    holding['security_id'], trade_date
                )
                price = historical_price if historical_price else holding['average_price']
                current_value = holding['quantity'] * price
                intermediate_total_value += current_value
                
                intermediate_holding = {
                    'security_id': holding['security_id'],
                    'symbol': holding['symbol'],
                    'quantity': holding['quantity'],
                    'average_price': holding['average_price'],
                    'current_price': price,
                    'current_value': current_value,
                    'unrealized_pnl': current_value - (holding['quantity'] * holding['average_price']),
                    'unrealized_pnl_percent': ((price - holding['average_price']) / holding['average_price'] * 100) if holding['average_price'] > 0 else 0.0
                }
                intermediate_holdings.append(intermediate_holding)
            
            # Create portfolio state for this date
            intermediate_state = {
                'date': trade_date,
                'total_value': intermediate_total_value,
                'total_invested': 0.0,  # Will be calculated at the end
                'total_withdrawn': 0.0,  # Will be calculated at the end
                'net_investment': 0.0,   # Will be calculated at the end
                'absolute_return': 0.0,  # Will be calculated at the end
                'absolute_return_percent': 0.0,  # Will be calculated at the end
                'xirr': None,
                'holdings': intermediate_holdings,
                'source': 'intermediate_reconstruction'
            }
            
            # Save snapshot at date break after completing all processing for this date
            PortfolioReconstructionService._log_portfolio_snapshot(client_id, trade_date, intermediate_state, save_snapshot=True)
        
        logger.info(f"\n{'='*80}")
        logger.info(f"After all rollbacks: {len(holdings_dict)} holdings remaining")
        logger.info(f"{'='*80}\n")
        
        # Final CA-only pass from last per-security as_of_date down to target_date
        logger.info(f"Applying final CA-only adjustments from last standing dates to {target_date}...")
        final_ca_adjustments = 0
        for sec_id, holding in list(holdings_dict.items()):
            if sec_id in securities_with_corp_actions:
                prev_as_of = security_as_of_dates.get(sec_id, datetime.now().date())
                if prev_as_of != target_date:
                    logger.info(f"  Final CA-ONLY adjustment for {holding['symbol']}: {prev_as_of} → {target_date}")
                    logger.info(f"    Current quantity: {holding['quantity']:.2f}")
                    
                    new_qty = adjust_quantity_for_portfolio_reconstruction(
                        standing_date=prev_as_of,
                        target_date=target_date,
                        security_id=sec_id,
                        current_quantity=holding['quantity'],
                        trade_type='BUY',     # zero-quantity rollback → CA-only
                        trade_quantity=0.0
                    )
                    
                    logger.info(f"    Final CA-ONLY result: {holding['quantity']:.2f} → {new_qty:.2f}")
                    holding['quantity'] = new_qty
                    
                    # Remove holding if quantity becomes zero or negative
                    if holding['quantity'] <= 0:
                        logger.info(f"  Removing {holding['symbol']} (quantity: {holding['quantity']:.2f})")
                        del holdings_dict[sec_id]
                    else:
                        security_as_of_dates[sec_id] = target_date
                    
                    final_ca_adjustments += 1
        
        if final_ca_adjustments > 0:
            logger.info(f"Applied {final_ca_adjustments} final CA-only adjustments to {target_date}")
        
        # Step 5: Update prices using historical data for target_date
        logger.info(f"Updating prices for {target_date}...")
        total_value = 0.0
        for holding in holdings_dict.values():
            historical_price = PortfolioReconstructionService.get_historical_price(
                holding['security_id'], target_date
            )
            
            if historical_price > 0:
                holding['current_price'] = historical_price
                holding['current_value'] = holding['quantity'] * historical_price
                holding['unrealized_pnl'] = holding['current_value'] - (holding['quantity'] * holding['average_price'])
                if holding['average_price'] > 0:
                    holding['unrealized_pnl_percent'] = ((historical_price - holding['average_price']) / holding['average_price']) * 100
            else:
                # Fallback to average price if no historical data
                holding['current_price'] = holding['average_price']
                holding['current_value'] = holding['quantity'] * holding['average_price']
                holding['unrealized_pnl'] = 0.0
                holding['unrealized_pnl_percent'] = 0.0
            
            total_value += holding['current_value']
            logger.info(f"  {holding['symbol']}: {holding['quantity']:.2f} @ ₹{historical_price:.2f} = ₹{holding['current_value']:,.2f}")
        
        # Step 6: Calculate cashflows up to target date
        cashflows_to_date = Cashflow.query.filter(
            Cashflow.client_id == client_id,
            Cashflow.date <= target_date
        ).all()
        
        total_invested = sum(float(cf.amount) for cf in cashflows_to_date if cf.amount > 0)
        total_withdrawn = abs(sum(float(cf.amount) for cf in cashflows_to_date if cf.amount < 0))
        net_investment = total_invested - total_withdrawn
        
        logger.info(f"=== PORTFOLIO RECONSTRUCTION COMPLETE ===")
        logger.info(f"Total value: ₹{total_value:,.2f}, Net investment: ₹{net_investment:,.2f}")
        
        return {
            'date': target_date,
            'total_value': total_value,
            'total_invested': total_invested,
            'total_withdrawn': total_withdrawn,
            'net_investment': net_investment,
            'absolute_return': total_value - net_investment,
            'absolute_return_percent': ((total_value - net_investment) / net_investment * 100) if net_investment > 0 else 0.0,
            'xirr': None,  # Would need cashflow analysis for XIRR
            'holdings': list(holdings_dict.values()),
            'source': 'reconstructed_backwards'
        }
    
    @staticmethod
    def _apply_corporate_action_adjustments(client_id, target_date, portfolio_state):
        """
        Apply corporate action adjustments to portfolio state using pure service
        
        This ensures that historical portfolio states reflect corporate actions
        that occurred after the target date, providing accurate historical analysis
        Uses daily cron job maintained historical prices directly from database
        """
        try:
            # ✅ CRITICAL FIX: Use holdings from portfolio_state (already transaction-reversed)
            # NOT from database (current state)!
            # Create mock Holding objects from portfolio_state for the adjustment API
            from collections import namedtuple
            MockHolding = namedtuple('MockHolding', ['security_id', 'quantity', 'security'])
            
            mock_holdings = []
            for holding_data in portfolio_state.get('holdings', []):
                security = Security.query.get(holding_data['security_id'])
                mock_holding = MockHolding(
                    security_id=holding_data['security_id'],
                    quantity=holding_data['quantity'],
                    security=security
                )
                mock_holdings.append(mock_holding)
            
            current_holdings = mock_holdings
            
            # Debug logging
            for h in current_holdings:
                if h.security and h.security.symbol == 'NAUKRI':
                    logger.info(f"_apply_corporate_action_adjustments: NAUKRI quantity from portfolio_state = {h.quantity}")
            
            # ✅ NEW ARCHITECTURE: Get ALL corporate actions for quantity adjustments
            # We need actions AFTER target_date to reverse quantities to historical levels
            all_corporate_actions = CorporateAction.query.filter(
                CorporateAction.is_active == True
            ).all()  # No date filter - quantity adjustment service needs all actions
            
            # Get all historical prices
            all_historical_prices = HistoricalPrice.query.all()
            
            # ✅ NEW ARCHITECTURE: Use UNADJUSTED historical prices only
            # Quantity adjustment service handles the qty adjustments
            # Prices remain unadjusted (raw historical prices from DB)
            from services.quantity_adjustment_service import get_unadjusted_historical_price
            
            def price_lookup_fn(security_id, lookup_date):
                return get_unadjusted_historical_price(
                    security_id, 
                    lookup_date, 
                    all_historical_prices,
                    lambda sid: Security.query.get(sid).current_price if Security.query.get(sid) else 0.0
                )
                # NO corporate actions, NO price adjustments - just raw historical price!
            
            # Use pure service to calculate adjusted holdings
            adjusted_holdings = adjust_holdings_for_corporate_actions(
                current_holdings, 
                all_corporate_actions, 
                target_date, 
                price_lookup_fn
            )
            
            if not adjusted_holdings:
                logger.info(f"No adjusted holdings found for {target_date}, returning original state")
                return portfolio_state
            
            # Convert adjusted holdings to portfolio state format
            adjusted_holdings_data = []
            total_value = Decimal('0.0')
            total_cost = Decimal('0.0')
            
            for holding in adjusted_holdings:
                security_id = holding['security_id']
                quantity = Decimal(str(holding['quantity_at_cutoff']))
                avg_price = Decimal(str(holding['adjusted_avg_price']))
                
                # ✅ NEW ARCHITECTURE: Get UNADJUSTED price (quantity is already adjusted)
                from services.quantity_adjustment_service import get_unadjusted_historical_price
                current_price = get_unadjusted_historical_price(
                    security_id, 
                    target_date, 
                    all_historical_prices,
                    lambda sid: Security.query.get(sid).current_price if Security.query.get(sid) else 0.0
                )
                # NO price adjustment - prices are always unadjusted, only quantities are adjusted
                current_value = quantity * Decimal(str(current_price))
                cost_basis = quantity * avg_price
                
                # Get security symbol
                security = Security.query.get(security_id)
                symbol = security.symbol if security else f'SEC_{security_id}'
                
                adjusted_holdings_data.append({
                    'security_id': security_id,
                    'symbol': symbol,
                    'quantity': float(quantity),
                    'average_price': float(avg_price),
                    'current_price': current_price,
                    'current_value': float(current_value),
                    'cost_basis': float(cost_basis),
                    'gain': float(current_value - cost_basis),
                    'gain_percent': float((current_value - cost_basis) / cost_basis * 100) if cost_basis > 0 else 0
                })
                
                total_value += current_value
                total_cost += cost_basis
            
            total_gain = total_value - total_cost
            total_gain_percent = (total_gain / total_cost * 100) if total_cost > 0 else 0
            
            logger.info(f"Applied corporate action adjustments: {len(adjusted_holdings_data)} holdings, "
                       f"Total value: {total_value}, Total cost: {total_cost}")
            
            return {
                'holdings': adjusted_holdings_data,
                'total_value': float(total_value),
                'total_cost': float(total_cost),
                'total_gain': float(total_gain),
                'total_gain_percent': float(total_gain_percent),
                'source': f"{portfolio_state['source']}_adjusted"
            }
            
        except Exception as e:
            logger.error(f"Error applying corporate action adjustments: {e}")
            # Return original state if adjustment fails
            return portfolio_state

# API Endpoint for Portfolio Reconstruction
@portfolio_reconstruction_bp.route('/<int:client_id>/portfolio-reconstruction', methods=['GET'])
@audit_api_call
def get_portfolio_reconstruction(client_id):
    """
    Get portfolio reconstruction for a specific client and date
    """
    try:
        # Get date parameter
        date_str = request.args.get('date')
        if not date_str:
            return jsonify({
                'success': False,
                'message': 'Date parameter is required'
            }), 400
        
        # Parse date
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({
                'success': False,
                'message': 'Invalid date format. Use YYYY-MM-DD'
            }), 400
        
        # Get portfolio state
        portfolio_state = PortfolioReconstructionService.get_portfolio_state_at_date(
            client_id, target_date, apply_adjustments=True, save_snapshot=True
        )
        
        return jsonify({
            'success': True,
            'data': portfolio_state
        })
        
    except Exception as e:
        logger.error(f"Error in portfolio reconstruction API: {e}")
        return jsonify({
            'success': False,
            'message': f'Internal server error: {str(e)}'
        }), 500
    
    @staticmethod
    def get_portfolio_comparison(client_id, start_date, end_date):
        """
        Get portfolio comparison between two dates
        """
        start_state = PortfolioReconstructionService.get_portfolio_state_at_date(client_id, start_date, save_snapshot=False)
        end_state = PortfolioReconstructionService.get_portfolio_state_at_date(client_id, end_date, save_snapshot=False)
        
        return {
            'start_date': start_state,
            'end_date': end_state,
            'period_return': end_state['total_value'] - start_state['total_value'],
            'period_return_percent': ((end_state['total_value'] - start_state['total_value']) / start_state['total_value'] * 100) if start_state['total_value'] > 0 else 0.0
        }
