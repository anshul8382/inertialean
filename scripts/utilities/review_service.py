#!/usr/bin/env python3
"""
Review Service
Handles client review generation, storage, and notifications
"""
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy import and_, desc, func
from models import (
    Client, PortfolioSnapshot, HoldingSnapshot, Transaction, 
    Holding, Security, AssetClass, AssetAllocationModel, SecurityAllocationModel,
    ModelAssignment, Benchmark, BenchmarkData, Review, ReviewShare
)
from extensions import db
from performance_service import PerformanceService
import logging
import json

logger = logging.getLogger(__name__)

class ReviewService:
    """Service class for generating and managing client reviews"""
    
    @staticmethod
    def generate_client_review(client_id, start_date, end_date, review_type='comprehensive'):
        """
        Generate comprehensive client review report
        
        Args:
            client_id (int): Client ID
            start_date (str): Start date in YYYY-MM-DD format
            end_date (str): End date in YYYY-MM-DD format
            review_type (str): Type of review (comprehensive, performance, allocation)
        
        Returns:
            dict: Review data
        """
        try:
            logger.info(f"=== REVIEW GENERATION DEBUG ===")
            logger.info(f"Client ID: {client_id}")
            logger.info(f"Start Date: {start_date}")
            logger.info(f"End Date: {end_date}")
            logger.info(f"Review Type: {review_type}")
            
            # Parse dates
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
            logger.info(f"Parsed dates - Start: {start_dt}, End: {end_dt}")
            
            # Get client
            client = Client.query.get(client_id)
            if not client:
                logger.error(f"Client {client_id} not found")
                raise ValueError(f"Client {client_id} not found")
            
            logger.info(f"Client found: {client.name} (ID: {client.id})")
            
            # Initialize review data
            review_data = {
                'client_id': client_id,
                'client_name': client.name,
                'start_date': start_date,
                'end_date': end_date,
                'review_type': review_type,
                'generated_at': datetime.utcnow().isoformat(),
                'sections': {}
            }
            
            # Generate different sections based on review type
            logger.info(f"=== GENERATING REVIEW SECTIONS ===")
            
            if review_type in ['comprehensive', 'performance']:
                logger.info(f"Step 1: Analyzing performance...")
                performance_data = ReviewService._analyze_performance(client, start_dt, end_dt)
                logger.info(f"Performance analysis result: {performance_data}")
                review_data['sections']['performance'] = performance_data
            
            if review_type in ['comprehensive', 'allocation']:
                logger.info(f"Step 2: Analyzing allocation...")
                allocation_data = ReviewService._analyze_allocation(client, start_dt, end_dt)
                logger.info(f"Allocation analysis result: {allocation_data}")
                review_data['sections']['allocation'] = allocation_data
            
            if review_type in ['comprehensive', 'transactions']:
                review_data['sections']['transactions'] = ReviewService._analyze_transactions(
                    client, start_dt, end_dt
                )
            
            if review_type in ['comprehensive', 'securities']:
                review_data['sections']['securities'] = ReviewService._analyze_securities(
                    client, start_dt, end_dt
                )
            
            if review_type in ['comprehensive', 'recommendations']:
                review_data['sections']['recommendations'] = ReviewService._generate_recommendations(
                    client, start_dt, end_dt
                )
            
            return review_data
            
        except Exception as e:
            logger.error(f"Error generating review for client {client_id}: {str(e)}")
            raise
    
    @staticmethod
    def _analyze_performance(client, start_date, end_date):
        """Analyze portfolio performance for the period"""
        try:
            logger.info(f"=== PERFORMANCE ANALYSIS DEBUG ===")
            logger.info(f"Client ID: {client.id}, Name: {client.name}")
            logger.info(f"Start Date: {start_date}, End Date: {end_date}")
            
            # Check if snapshots exist
            snapshot_count = PortfolioSnapshot.query.filter_by(client_id=client.id).count()
            logger.info(f"Total snapshots for client {client.id}: {snapshot_count}")
            
            if snapshot_count == 0:
                logger.info("No snapshots found, using current holdings + cashflow data")
                return ReviewService._analyze_performance_without_snapshots(client, start_date, end_date)
            
            # Get portfolio snapshots
            logger.info(f"Looking for start snapshot for client {client.id} on/after {start_date}")
            start_snapshot = PortfolioSnapshot.query.filter(
                and_(
                    PortfolioSnapshot.client_id == client.id,
                    PortfolioSnapshot.date >= start_date
                )
            ).order_by(PortfolioSnapshot.date).first()
            
            logger.info(f"Start snapshot found: {start_snapshot}")
            if start_snapshot:
                logger.info(f"Start snapshot date: {start_snapshot.date}, value: {start_snapshot.total_value}")
            
            logger.info(f"Looking for end snapshot for client {client.id} on/before {end_date}")
            end_snapshot = PortfolioSnapshot.query.filter(
                and_(
                    PortfolioSnapshot.client_id == client.id,
                    PortfolioSnapshot.date <= end_date
                )
            ).order_by(desc(PortfolioSnapshot.date)).first()
            
            logger.info(f"End snapshot found: {end_snapshot}")
            if end_snapshot:
                logger.info(f"End snapshot date: {end_snapshot.date}, value: {end_snapshot.total_value}")
                logger.info(f"End snapshot net_investment: {end_snapshot.net_investment}")
                logger.info(f"End snapshot absolute_return_percent: {end_snapshot.absolute_return_percent}")
            
            if not start_snapshot or not end_snapshot:
                logger.error(f"Insufficient snapshot data - Start: {start_snapshot}, End: {end_snapshot}")
                return {'error': 'Insufficient snapshot data for performance analysis'}
            
            # Calculate REVIEW PERIOD performance metrics
            logger.info(f"=== REVIEW PERIOD CALCULATIONS ===")
            logger.info(f"Start snapshot total_value: {start_snapshot.total_value}")
            logger.info(f"End snapshot total_value: {end_snapshot.total_value}")
            
            review_period_return = float(end_snapshot.total_value - start_snapshot.total_value)
            review_period_return_percent = float(end_snapshot.total_value / start_snapshot.total_value - 1) * 100
            
            logger.info(f"Review period return: {review_period_return}")
            logger.info(f"Review period return percent: {review_period_return_percent}")
            
            # Calculate OVERALL performance metrics (from inception)
            logger.info(f"=== OVERALL PERFORMANCE CALCULATIONS ===")
            logger.info(f"End snapshot net_investment: {end_snapshot.net_investment}")
            logger.info(f"End snapshot absolute_return_percent: {end_snapshot.absolute_return_percent}")
            
            overall_return = float(end_snapshot.total_value - end_snapshot.net_investment)
            overall_return_percent = float(end_snapshot.absolute_return_percent)
            
            logger.info(f"Overall return: {overall_return}")
            logger.info(f"Overall return percent: {overall_return_percent}")
            
            # Calculate XIRR if available
            xirr = float(end_snapshot.xirr) if end_snapshot.xirr else None
            logger.info(f"XIRR: {xirr}")
            
            # Get benchmark performance for REVIEW PERIOD
            logger.info(f"=== BENCHMARK CALCULATIONS ===")
            logger.info(f"Getting benchmark return for review period: {start_date} to {end_date}")
            benchmark_return_review_period = ReviewService._get_benchmark_return(start_date, end_date)
            logger.info(f"Benchmark return for review period: {benchmark_return_review_period}")
            
            # Get benchmark performance for OVERALL PERIOD (from inception)
            logger.info(f"Getting benchmark return for overall period")
            benchmark_return_overall = ReviewService._get_benchmark_return_overall(client, end_date)
            logger.info(f"Benchmark return for overall period: {benchmark_return_overall}")
            
            return {
                # Review period metrics
                'start_value': float(start_snapshot.total_value),
                'end_value': float(end_snapshot.total_value),
                'review_period_return': review_period_return,
                'review_period_return_percent': review_period_return_percent,
                'benchmark_return_review_period': benchmark_return_review_period,
                'excess_return_review_period': review_period_return_percent - benchmark_return_review_period if benchmark_return_review_period else None,
                
                # Overall metrics
                'net_investment': float(end_snapshot.net_investment),
                'current_value': float(end_snapshot.total_value),
                'overall_return': overall_return,
                'overall_return_percent': overall_return_percent,
                'benchmark_return_overall': benchmark_return_overall,
                'excess_return_overall': overall_return_percent - benchmark_return_overall if benchmark_return_overall else None,
                
                # Legacy fields for backward compatibility
                'total_return': review_period_return,
                'total_return_percent': review_period_return_percent,
                'xirr': xirr,
                'benchmark_return': benchmark_return_review_period,
                'excess_return': review_period_return_percent - benchmark_return_review_period if benchmark_return_review_period else None,
                'absolute_return': overall_return,
                'absolute_return_percent': overall_return_percent
            }
            
        except Exception as e:
            logger.error(f"Error analyzing performance: {str(e)}")
            return {'error': str(e)}
    
    @staticmethod
    def _get_benchmark_return_overall(client, end_date):
        """Calculate benchmark return from client's first investment to end date"""
        try:
            # Get client's first cashflow date
            first_cashflow = Cashflow.query.filter_by(client_id=client.id).order_by(Cashflow.date).first()
            if not first_cashflow:
                return None
            
            start_date = first_cashflow.date.date()
            return ReviewService._get_benchmark_return(start_date, end_date)
            
        except Exception as e:
            logger.error(f"Error calculating overall benchmark return: {str(e)}")
            return None
    
    @staticmethod
    def _analyze_performance_without_snapshots(client, start_date, end_date):
        """Analyze portfolio performance using current holdings and cashflow data"""
        try:
            logger.info(f"=== PERFORMANCE ANALYSIS WITHOUT SNAPSHOTS ===")
            
            # Get current portfolio value from holdings
            from models import Holding, Security
            holdings = Holding.query.filter_by(client_id=client.id).all()
            current_value = 0.0
            
            for h in holdings:
                if h.security and hasattr(h.security, 'current_price') and h.security.current_price:
                    current_value += float(h.quantity * h.security.current_price)
                elif h.security and hasattr(h.security, 'price') and h.security.price:
                    current_value += float(h.quantity * h.security.price)
                else:
                    # Fallback to average price if current price not available
                    current_value += float(h.quantity * h.average_price)
            
            logger.info(f"Current portfolio value from holdings: {current_value}")
            
            # Get cashflow data for the period
            from models import Cashflow
            cashflows = Cashflow.query.filter_by(client_id=client.id).all()
            logger.info(f"Total cashflows found: {len(cashflows)}")
            
            # Calculate net investment from cashflows
            net_investment = 0.0
            for cf in cashflows:
                net_investment += float(cf.amount)
            
            logger.info(f"Net investment from cashflows: {net_investment}")
            
            # Calculate overall return
            overall_return = current_value - abs(net_investment)
            overall_return_percent = (overall_return / abs(net_investment)) * 100 if net_investment != 0 else 0
            
            logger.info(f"Overall return: {overall_return}")
            logger.info(f"Overall return percent: {overall_return_percent}")
            
            # For review period, we can't calculate without historical data
            # So we'll show current performance and note the limitation
            review_period_return = 0.0  # Cannot calculate without historical snapshots
            review_period_return_percent = 0.0
            
            # Get benchmark performance
            benchmark_return_review_period = ReviewService._get_benchmark_return(start_date, end_date)
            benchmark_return_overall = ReviewService._get_benchmark_return_overall(client, end_date)
            
            return {
                # Review period metrics (limited without snapshots)
                'start_value': 0.0,  # Cannot determine without snapshots
                'end_value': current_value,
                'review_period_return': review_period_return,
                'review_period_return_percent': review_period_return_percent,
                'benchmark_return_review_period': benchmark_return_review_period,
                'excess_return_review_period': review_period_return_percent - benchmark_return_review_period if benchmark_return_review_period else None,
                
                # Overall metrics (can calculate from current data)
                'net_investment': abs(net_investment),
                'current_value': current_value,
                'overall_return': overall_return,
                'overall_return_percent': overall_return_percent,
                'benchmark_return_overall': benchmark_return_overall,
                'excess_return_overall': overall_return_percent - benchmark_return_overall if benchmark_return_overall else None,
                
                # Legacy fields for backward compatibility
                'total_return': review_period_return,
                'total_return_percent': review_period_return_percent,
                'xirr': None,  # Cannot calculate without cashflow dates
                'benchmark_return': benchmark_return_review_period,
                'excess_return': review_period_return_percent - benchmark_return_review_period if benchmark_return_review_period else None,
                'absolute_return': overall_return,
                'absolute_return_percent': overall_return_percent,
                
                # Add warning about data limitations
                'data_limitation': 'Review period performance cannot be calculated without historical portfolio snapshots. Only overall performance is shown.'
            }
            
        except Exception as e:
            logger.error(f"Error analyzing performance without snapshots: {str(e)}")
            return {'error': str(e)}
    
    @staticmethod
    def _analyze_allocation(client, start_date, end_date):
        """Analyze asset allocation changes"""
        try:
            # Get current holdings
            holdings = Holding.query.filter_by(client_id=client.id).all()
            
            # Calculate current allocation
            total_value = sum(float(h.quantity * h.security.current_price) for h in holdings if h.security.current_price)
            
            allocation_data = {}
            for holding in holdings:
                if holding.security.current_price:
                    security_value = float(holding.quantity * holding.security.current_price)
                    allocation_percent = (security_value / total_value * 100) if total_value > 0 else 0
                    
                    allocation_data[holding.security.symbol] = {
                        'name': holding.security.name,
                        'quantity': float(holding.quantity),
                        'current_price': float(holding.security.current_price),
                        'current_value': security_value,
                        'allocation_percent': allocation_percent,
                        'asset_class': holding.security.asset_class.name if holding.security.asset_class else 'Unknown'
                    }
            
            # Get target allocation from model
            model_assignment = ModelAssignment.query.filter_by(client_id=client.id).first()
            target_allocation = {}
            
            if model_assignment and model_assignment.asset_model:
                for allocation in model_assignment.asset_model.asset_allocations:
                    target_allocation[allocation.asset_class.name] = float(allocation.allocation_percentage)
            
            return {
                'current_allocation': allocation_data,
                'target_allocation': target_allocation,
                'total_value': total_value,
                'drift_analysis': ReviewService._calculate_allocation_drift(allocation_data, target_allocation)
            }
            
        except Exception as e:
            logger.error(f"Error analyzing allocation: {str(e)}")
            return {'error': str(e)}
    
    @staticmethod
    def _analyze_transactions(client, start_date, end_date):
        """Analyze transaction patterns and significant changes"""
        try:
            # Get transactions for the period
            transactions = Transaction.query.filter(
                and_(
                    Transaction.client_id == client.id,
                    Transaction.transaction_date >= start_date,
                    Transaction.transaction_date <= end_date
                )
            ).order_by(Transaction.transaction_date).all()
            
            # Analyze transaction patterns
            buy_transactions = [t for t in transactions if t.type == 'buy']
            sell_transactions = [t for t in transactions if t.type == 'sell']
            
            # Calculate significant changes
            significant_changes = []
            total_buy_amount = sum(float(t.amount) for t in buy_transactions)
            total_sell_amount = sum(float(t.amount) for t in sell_transactions)
            
            # Group by security and convert to serializable format
            security_transactions = {}
            for transaction in transactions:
                symbol = transaction.security.symbol
                if symbol not in security_transactions:
                    security_transactions[symbol] = {'buys': [], 'sells': []}
                
                # Convert transaction to dictionary
                transaction_dict = {
                    'id': transaction.id,
                    'type': transaction.type,
                    'quantity': float(transaction.quantity),
                    'price': float(transaction.price),
                    'amount': float(transaction.amount),
                    'transaction_date': transaction.transaction_date.isoformat() if transaction.transaction_date else None,
                    'security_symbol': transaction.security.symbol,
                    'security_name': transaction.security.name
                }
                
                if transaction.type == 'buy':
                    security_transactions[symbol]['buys'].append(transaction_dict)
                else:
                    security_transactions[symbol]['sells'].append(transaction_dict)
            
            # Identify significant changes
            for symbol, data in security_transactions.items():
                buy_amount = sum(float(t['amount']) for t in data['buys'])
                sell_amount = sum(float(t['amount']) for t in data['sells'])
                
                if buy_amount > 100000 or sell_amount > 100000:  # 1L threshold
                    significant_changes.append({
                        'security': symbol,
                        'buy_amount': buy_amount,
                        'sell_amount': sell_amount,
                        'net_change': buy_amount - sell_amount,
                        'transaction_count': len(data['buys']) + len(data['sells'])
                    })
            
            return {
                'total_transactions': len(transactions),
                'buy_transactions': len(buy_transactions),
                'sell_transactions': len(sell_transactions),
                'total_buy_amount': total_buy_amount,
                'total_sell_amount': total_sell_amount,
                'net_flow': total_buy_amount - total_sell_amount,
                'significant_changes': significant_changes,
                'security_transactions': security_transactions
            }
            
        except Exception as e:
            logger.error(f"Error analyzing transactions: {str(e)}")
            return {'error': str(e)}
    
    @staticmethod
    def _analyze_securities(client, start_date, end_date):
        """Analyze individual security performance"""
        try:
            # Get holdings
            holdings = Holding.query.filter_by(client_id=client.id).all()
            
            security_performance = []
            for holding in holdings:
                if holding.security.current_price:
                    # Calculate basic metrics
                    current_value = float(holding.quantity * holding.security.current_price)
                    cost_basis = float(holding.quantity * holding.average_buy_price)
                    unrealized_pnl = current_value - cost_basis
                    unrealized_pnl_percent = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0
                    
                    security_performance.append({
                        'symbol': holding.security.symbol,
                        'name': holding.security.name,
                        'quantity': float(holding.quantity),
                        'current_price': float(holding.security.current_price),
                        'average_buy_price': float(holding.average_buy_price),
                        'current_value': current_value,
                        'cost_basis': cost_basis,
                        'unrealized_pnl': unrealized_pnl,
                        'unrealized_pnl_percent': unrealized_pnl_percent,
                        'asset_class': holding.security.asset_class.name if holding.security.asset_class else 'Unknown'
                    })
            
            # Sort by performance
            security_performance.sort(key=lambda x: x['unrealized_pnl_percent'], reverse=True)
            
            return {
                'best_performers': security_performance[:5],
                'worst_performers': security_performance[-5:],
                'all_securities': security_performance
            }
            
        except Exception as e:
            logger.error(f"Error analyzing securities: {str(e)}")
            return {'error': str(e)}
    
    @staticmethod
    def _generate_recommendations(client, start_date, end_date):
        """Generate recommendations based on analysis"""
        try:
            recommendations = []
            
            # Get allocation analysis
            allocation = ReviewService._analyze_allocation(client, start_date, end_date)
            
            # Check for allocation drift
            if 'drift_analysis' in allocation:
                for asset_class, drift in allocation['drift_analysis'].items():
                    if abs(drift) > 5:  # More than 5% drift
                        action = 'reduce' if drift > 0 else 'increase'
                        recommendations.append({
                            'type': 'allocation',
                            'asset_class': asset_class,
                            'action': action,
                            'reason': f'{asset_class} is {abs(drift):.1f}% {"overweight" if drift > 0 else "underweight"}',
                            'priority': 'high' if abs(drift) > 10 else 'medium'
                        })
            
            # Add more recommendation logic here
            # (rebalancing, new investments, etc.)
            
            return recommendations
            
        except Exception as e:
            logger.error(f"Error generating recommendations: {str(e)}")
            return {'error': str(e)}
    
    @staticmethod
    def _get_benchmark_return(start_date, end_date):
        """Get benchmark (Nifty) return for the period"""
        try:
            # This would integrate with your benchmark data
            # For now, return a placeholder
            return 12.5  # Placeholder return
        except Exception as e:
            logger.error(f"Error getting benchmark return: {str(e)}")
            return None
    
    @staticmethod
    def _calculate_allocation_drift(current_allocation, target_allocation):
        """Calculate drift from target allocation"""
        drift = {}
        
        # Group current allocation by asset class
        current_by_class = {}
        for symbol, data in current_allocation.items():
            asset_class = data['asset_class']
            if asset_class not in current_by_class:
                current_by_class[asset_class] = 0
            current_by_class[asset_class] += data['allocation_percent']
        
        # Calculate drift
        for asset_class, target_percent in target_allocation.items():
            current_percent = current_by_class.get(asset_class, 0)
            drift[asset_class] = current_percent - target_percent
        
        return drift
    
    @staticmethod
    def store_review_result(review_data):
        """Store review result in database"""
        try:
            from models import Review
            
            # Find the review record
            review = Review.query.filter_by(
                client_id=review_data['client_id'],
                start_date=datetime.strptime(review_data['start_date'], '%Y-%m-%d').date(),
                end_date=datetime.strptime(review_data['end_date'], '%Y-%m-%d').date(),
                review_type=review_data['review_type']
            ).order_by(Review.generated_at.desc()).first()
            
            if review:
                # Ensure data is JSON serializable
                serializable_data = ReviewService._make_json_serializable(review_data)
                
                # Update the review with data
                review.review_data = serializable_data
                review.status = 'completed'
                review.completed_at = datetime.utcnow()
                db.session.commit()
                
                logger.info(f"Review {review.id} completed and stored successfully")
            else:
                logger.warning(f"No review record found for client {review_data['client_id']}")
            
        except Exception as e:
            logger.error(f"Error storing review result: {str(e)}")
            raise
    
    @staticmethod
    def _make_json_serializable(obj):
        """Convert object to JSON serializable format"""
        import json
        from datetime import datetime, date
        
        def convert_value(value):
            if isinstance(value, (datetime, date)):
                return value.isoformat()
            elif hasattr(value, '__dict__') and not isinstance(value, (str, int, float, bool, type(None))):
                # Convert SQLAlchemy objects to dict
                if hasattr(value, 'id'):
                    return {'id': value.id, 'type': value.__class__.__name__}
                else:
                    return str(value)
            elif isinstance(value, dict):
                return {k: convert_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [convert_value(v) for v in value]
            else:
                return value
        
        return convert_value(obj)
    
    @staticmethod
    def send_review_notification(client_id):
        """Send notification that review is ready"""
        try:
            # This will be implemented later
            logger.info(f"Review notification sent for client {client_id}")
            
        except Exception as e:
            logger.error(f"Error sending review notification: {str(e)}")
            raise
    
    def generate_review_async(self, review_id):
        """Generate review asynchronously"""
        try:
            # Get the review record
            review = Review.query.get(review_id)
            if not review:
                raise ValueError(f"Review {review_id} not found")
            
            # Get client
            client = Client.query.get(review.client_id)
            if not client:
                raise ValueError(f"Client {review.client_id} not found")
            
            # Generate the review
            review_data = ReviewService.generate_client_review(
                client_id=review.client_id,
                start_date=review.start_date.strftime('%Y-%m-%d'),
                end_date=review.end_date.strftime('%Y-%m-%d'),
                review_type=review.review_type
            )
            
            # Generate market commentary
            try:
                from market_commentary_service import MarketCommentaryService
                commentary_service = MarketCommentaryService()
                market_commentary = commentary_service.generate_market_commentary(review_id)
                
                if market_commentary:
                    # Add market commentary to review data
                    if 'sections' not in review_data:
                        review_data['sections'] = {}
                    review_data['sections']['market_commentary'] = market_commentary
                    logger.info(f"Market commentary generated for review {review_id}")
                else:
                    logger.warning(f"Failed to generate market commentary for review {review_id}")
            except Exception as e:
                logger.error(f"Error generating market commentary for review {review_id}: {str(e)}")
                # Continue without market commentary
            
            # Store the result
            ReviewService.store_review_result(review_data)
            
            # Send notification
            ReviewService.send_review_notification(review.client_id)
            
            logger.info(f"Review {review_id} generated successfully")
            
        except Exception as e:
            logger.error(f"Error generating review {review_id}: {str(e)}")
            # Update review status to failed
            review = Review.query.get(review_id)
            if review:
                review.status = 'failed'
                review.error_message = str(e)
                db.session.commit()
            raise
    
    def share_review_async(self, share_id):
        """Share review asynchronously"""
        try:
            # Get the share record
            share = ReviewShare.query.get(share_id)
            if not share:
                raise ValueError(f"Review share {share_id} not found")
            
            # Get the review
            review = Review.query.get(share.review_id)
            if not review:
                raise ValueError(f"Review {share.review_id} not found")
            
            # Get client
            client = Client.query.get(review.client_id)
            if not client:
                raise ValueError(f"Client {review.client_id} not found")
            
            # Filter review data for client sharing
            shared_data = ReviewService._filter_review_for_client(review.review_data)
            
            # Update share record
            share.shared_data = shared_data
            share.delivery_status = 'sent'
            db.session.commit()
            
            # Send email notification (placeholder)
            logger.info(f"Review share {share_id} processed successfully")
            
        except Exception as e:
            logger.error(f"Error sharing review {share_id}: {str(e)}")
            # Update share status to failed
            share = ReviewShare.query.get(share_id)
            if share:
                share.delivery_status = 'failed'
                db.session.commit()
            raise
    
    @staticmethod
    def _filter_review_for_client(review_data):
        """Filter review data for client sharing (remove sensitive information)"""
        try:
            if not review_data:
                return {}
            
            # Create a filtered version for client
            filtered_data = {
                'client_name': review_data.get('client_name'),
                'start_date': review_data.get('start_date'),
                'end_date': review_data.get('end_date'),
                'review_type': review_data.get('review_type'),
                'generated_at': review_data.get('generated_at'),
                'sections': {}
            }
            
            # Filter sections based on what should be shared
            sections = review_data.get('sections', {})
            
            # Include performance data
            if 'performance' in sections:
                perf_data = sections['performance']
                if 'error' not in perf_data:
                    filtered_data['sections']['performance'] = {
                        'start_value': perf_data.get('start_value'),
                        'end_value': perf_data.get('end_value'),
                        'total_return_percent': perf_data.get('total_return_percent'),
                        'benchmark_return': perf_data.get('benchmark_return'),
                        'excess_return': perf_data.get('excess_return')
                    }
            
            # Include allocation data (without sensitive details)
            if 'allocation' in sections:
                alloc_data = sections['allocation']
                if 'error' not in alloc_data:
                    filtered_data['sections']['allocation'] = {
                        'total_value': alloc_data.get('total_value'),
                        'current_allocation': alloc_data.get('current_allocation'),
                        'target_allocation': alloc_data.get('target_allocation')
                    }
            
            # Include recommendations (filtered)
            if 'recommendations' in sections:
                rec_data = sections['recommendations']
                if isinstance(rec_data, list) and 'error' not in rec_data:
                    filtered_data['sections']['recommendations'] = rec_data
            
            return filtered_data
            
        except Exception as e:
            logger.error(f"Error filtering review data: {str(e)}")
            return {}
