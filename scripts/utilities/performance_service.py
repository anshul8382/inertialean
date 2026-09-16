#!/usr/bin/env python3
"""
Performance Tracking Service
Handles portfolio snapshots, benchmark data, and performance calculations
"""
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy import and_, desc
from models import (
    db, Client, PortfolioSnapshot, HoldingSnapshot, Benchmark, BenchmarkData, 
    ModelPerformance, Holding, Security, Cashflow, AssetAllocationModel, SecurityAllocationModel
)
from routes.main import calculate_xirr
import logging

logger = logging.getLogger(__name__)

class PerformanceService:
    """Service class for performance tracking and calculations"""
    
    @staticmethod
    def create_portfolio_snapshot(client_id, snapshot_date=None):
        """Create a portfolio snapshot for a client on a specific date"""
        if snapshot_date is None:
            snapshot_date = date.today()
        
        try:
            client = Client.query.get(client_id)
            if not client:
                raise ValueError(f"Client {client_id} not found")
            
            # Check if snapshot already exists for this date
            existing_snapshot = PortfolioSnapshot.query.filter_by(
                client_id=client_id, 
                date=snapshot_date
            ).first()
            
            if existing_snapshot:
                logger.info(f"Snapshot already exists for client {client_id} on {snapshot_date}")
                return existing_snapshot
            
            # Calculate portfolio metrics
            total_value = Decimal('0.0')
            holdings_data = []
            
            # Get current holdings
            for holding in client.holdings:
                if holding.security and holding.security.current_price:
                    quantity = Decimal(str(holding.quantity)) if holding.quantity else Decimal('0.0')
                    current_price = Decimal(str(holding.security.current_price))
                    avg_price = Decimal(str(holding.average_price)) if holding.average_price else Decimal('0.0')
                    
                    current_value = quantity * current_price
                    total_value += current_value
                    
                    unrealized_pnl = current_value - (quantity * avg_price)
                    unrealized_pnl_percent = ((current_price - avg_price) / avg_price * Decimal('100')) if avg_price > 0 else Decimal('0.0')
                    
                    holdings_data.append({
                        'security_id': holding.security_id,
                        'quantity': quantity,
                        'average_price': avg_price,
                        'current_price': current_price,
                        'current_value': current_value,
                        'unrealized_pnl': unrealized_pnl,
                        'unrealized_pnl_percent': unrealized_pnl_percent
                    })
            
            # Calculate cashflow metrics
            total_invested, total_withdrawn, net_investment = PerformanceService._calculate_cashflows(client_id)
            
            # Calculate absolute return
            absolute_return = total_value - net_investment
            absolute_return_percent = (absolute_return / net_investment * Decimal('100')) if net_investment > 0 else Decimal('0.0')
            
            # Calculate XIRR
            xirr = PerformanceService._calculate_client_xirr(client_id, total_value)
            
            # Create portfolio snapshot
            snapshot = PortfolioSnapshot(
                client_id=client_id,
                date=snapshot_date,
                total_value=total_value,
                total_invested=total_invested,
                total_withdrawn=total_withdrawn,
                net_investment=net_investment,
                absolute_return=absolute_return,
                absolute_return_percent=absolute_return_percent,
                xirr=xirr
            )
            
            db.session.add(snapshot)
            db.session.flush()  # Get the snapshot ID
            
            # Create holding snapshots
            for holding_data in holdings_data:
                allocation_percent = (holding_data['current_value'] / total_value * Decimal('100')) if total_value > 0 else Decimal('0.0')
                
                holding_snapshot = HoldingSnapshot(
                    portfolio_snapshot_id=snapshot.id,
                    security_id=holding_data['security_id'],
                    quantity=holding_data['quantity'],
                    average_price=holding_data['average_price'],
                    current_price=holding_data['current_price'],
                    current_value=holding_data['current_value'],
                    unrealized_pnl=holding_data['unrealized_pnl'],
                    unrealized_pnl_percent=holding_data['unrealized_pnl_percent'],
                    allocation_percent=allocation_percent
                )
                
                db.session.add(holding_snapshot)
            
            db.session.commit()
            logger.info(f"Created portfolio snapshot for client {client_id} on {snapshot_date}")
            return snapshot
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating portfolio snapshot: {str(e)}")
            raise
    
    @staticmethod
    def _calculate_cashflows(client_id):
        """Calculate total invested, withdrawn, and net investment for a client"""
        cashflows = Cashflow.query.filter_by(client_id=client_id).all()
        
        total_invested = Decimal('0.0')
        total_withdrawn = Decimal('0.0')
        
        for cf in cashflows:
            amount = Decimal(str(cf.amount))
            if amount < 0:  # Negative amount = INVESTMENT
                total_invested += abs(amount)
            else:  # Positive amount = WITHDRAWAL
                total_withdrawn += amount
        
        net_investment = total_withdrawn - total_invested
        return total_invested, total_withdrawn, net_investment
    
    @staticmethod
    def _calculate_client_xirr(client_id, current_value):
        """Calculate XIRR for a client"""
        try:
            cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
            cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
            
            xirr, _, _, _, _ = calculate_xirr(cashflow_data, float(current_value))
            return Decimal(str(xirr)) if xirr else None
        except Exception as e:
            logger.error(f"Error calculating XIRR for client {client_id}: {str(e)}")
            return None
    
    @staticmethod
    def update_benchmark_data(benchmark_id=None):
        """Update benchmark data from Google Sheets (Yahoo Finance removed due to rate limiting)"""
        try:
            logger.warning("Benchmark data updates now require Google Sheets integration. Yahoo Finance has been removed due to rate limiting issues.")
            logger.info("Please use the Google Sheets integration for benchmark data updates.")
            return
            
        except Exception as e:
            logger.error(f"Error updating benchmark data: {str(e)}")
            db.session.rollback()
            raise
    
    @staticmethod
    def calculate_model_performance(model_id, model_type, benchmark_id=None, start_date=None, end_date=None):
        """Calculate performance for a specific model"""
        try:
            if start_date is None:
                start_date = date.today() - timedelta(days=365)
            if end_date is None:
                end_date = date.today()
            
            # Get model
            if model_type == 'asset':
                model = AssetAllocationModel.query.get(model_id)
            else:
                model = SecurityAllocationModel.query.get(model_id)
            
            if not model:
                raise ValueError(f"Model {model_id} of type {model_type} not found")
            
            # Get clients using this model
            from models import ModelAssignment
            model_assignments = ModelAssignment.query.filter_by(
                asset_model_id=model_id if model_type == 'asset' else None,
                stock_model_id=model_id if model_type == 'security' else None
            ).all()
            
            if not model_assignments:
                logger.warning(f"No clients assigned to model {model_id}")
                return None
            
            # Calculate aggregate performance across all clients using this model
            total_value = Decimal('0.0')
            total_invested = Decimal('0.0')
            
            for assignment in model_assignments:
                client = assignment.client
                if client:
                    # Get latest snapshot for this client
                    latest_snapshot = PortfolioSnapshot.query.filter_by(
                        client_id=client.id
                    ).order_by(desc(PortfolioSnapshot.date)).first()
                    
                    if latest_snapshot:
                        total_value += latest_snapshot.total_value
                        total_invested += latest_snapshot.net_investment
            
            # Calculate model return
            model_return = ((total_value - total_invested) / total_invested * Decimal('100')) if total_invested > 0 else Decimal('0.0')
            
            # Get benchmark return if specified
            benchmark_return = None
            if benchmark_id:
                benchmark_data = BenchmarkData.query.filter_by(
                    benchmark_id=benchmark_id,
                    date=end_date
                ).first()
                if benchmark_data:
                    benchmark_return = benchmark_data.cumulative_return
            
            # Calculate excess return
            excess_return = None
            if benchmark_return is not None:
                excess_return = model_return - benchmark_return
            
            # Create or update model performance record
            model_performance = ModelPerformance.query.filter_by(
                model_id=model_id,
                model_type=model_type,
                date=end_date
            ).first()
            
            if model_performance:
                model_performance.total_value = total_value
                model_performance.return_percent = model_return
                model_performance.benchmark_id = benchmark_id
                model_performance.benchmark_return = benchmark_return
                model_performance.excess_return = excess_return
            else:
                model_performance = ModelPerformance(
                    model_id=model_id,
                    model_type=model_type,
                    date=end_date,
                    total_value=total_value,
                    return_percent=model_return,
                    benchmark_id=benchmark_id,
                    benchmark_return=benchmark_return,
                    excess_return=excess_return
                )
                db.session.add(model_performance)
            
            db.session.commit()
            logger.info(f"Calculated performance for model {model_id} ({model_type})")
            return model_performance
            
        except Exception as e:
            logger.error(f"Error calculating model performance: {str(e)}")
            db.session.rollback()
            raise
    
    @staticmethod
    def get_performance_data_for_dashboard(days=30):
        """Get performance data for dashboard charts"""
        try:
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            
            # Get aggregate portfolio performance
            portfolio_data = []
            benchmark_data = {}
            
            # Get all clients
            clients = Client.query.all()
            
            for current_date in [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]:
                daily_total_value = Decimal('0.0')
                daily_total_invested = Decimal('0.0')
                
                for client in clients:
                    snapshot = PortfolioSnapshot.query.filter_by(
                        client_id=client.id,
                        date=current_date
                    ).first()
                    
                    if snapshot:
                        daily_total_value += snapshot.total_value
                        daily_total_invested += snapshot.net_investment
                
                if daily_total_invested > 0:
                    daily_return = ((daily_total_value - daily_total_invested) / daily_total_invested * Decimal('100'))
                    portfolio_data.append({
                        'date': current_date.strftime('%Y-%m-%d'),
                        'value': float(daily_total_value),
                        'return': float(daily_return)
                    })
            
            # Get benchmark data
            benchmarks = Benchmark.query.filter_by(is_active=True).limit(3).all()
            for benchmark in benchmarks:
                benchmark_series = []
                benchmark_records = BenchmarkData.query.filter(
                    BenchmarkData.benchmark_id == benchmark.id,
                    BenchmarkData.date >= start_date,
                    BenchmarkData.date <= end_date
                ).order_by(BenchmarkData.date).all()
                
                for record in benchmark_records:
                    benchmark_series.append({
                        'date': record.date.strftime('%Y-%m-%d'),
                        'return': float(record.cumulative_return) if record.cumulative_return else 0.0
                    })
                
                benchmark_data[benchmark.name] = benchmark_series
            
            return {
                'portfolio': portfolio_data,
                'benchmarks': benchmark_data
            }
            
        except Exception as e:
            logger.error(f"Error getting performance data for dashboard: {str(e)}")
            return {
                'portfolio': [],
                'benchmarks': {}
            }
    
    @staticmethod
    def create_snapshots_for_all_clients():
        """Create snapshots for all clients"""
        try:
            clients = Client.query.all()
            created_count = 0
            
            for client in clients:
                try:
                    PerformanceService.create_portfolio_snapshot(client.id)
                    created_count += 1
                except Exception as e:
                    logger.error(f"Error creating snapshot for client {client.id}: {str(e)}")
                    continue
            
            logger.info(f"Created snapshots for {created_count} clients")
            return created_count
            
        except Exception as e:
            logger.error(f"Error creating snapshots for all clients: {str(e)}")
            raise 