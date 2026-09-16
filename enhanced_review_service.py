#!/usr/bin/env python3
"""
Enhanced Review Service
Handles enhanced client review generation with positive/negative analysis, 
market comparison highlighting, and audit-ready calculation files
"""
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy import and_, desc, func
from models import (
    Client, PortfolioSnapshot, HoldingSnapshot, Transaction, 
    Holding, Security, AssetClass, AssetAllocationModel, SecurityAllocationModel,
    ModelAssignment, Benchmark, BenchmarkData, Review, ReviewShare, Cashflow
)
from extensions import db
from performance_service import PerformanceService
from services.ai_insights_service import AIInsightsService
import logging
import json
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("Warning: pandas not available, Excel generation will be limited")

from io import BytesIO
import os

logger = logging.getLogger(__name__)

class EnhancedReviewService:
    """Enhanced service class for generating comprehensive client reviews with analysis classification"""
    
    @staticmethod
    def generate_enhanced_client_review(client_id, start_date, end_date, review_type='comprehensive', include_ai_insights=False):
        """
        Generate enhanced client review with positive/negative analysis and market comparison
        
        Args:
            client_id (int): Client ID
            start_date (str): Start date in YYYY-MM-DD format
            end_date (str): End date in YYYY-MM-DD format
            review_type (str): Type of review (comprehensive, performance, allocation)
        
        Returns:
            dict: Enhanced review data with analysis classification
        """
        try:
            logger.info(f"=== ENHANCED REVIEW GENERATION START ===")
            logger.info(f"Client ID: {client_id}, Start: {start_date}, End: {end_date}, Type: {review_type}")
            # Parse dates
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
            
            # Get client
            client = Client.query.get(client_id)
            if not client:
                raise ValueError(f"Client {client_id} not found")
            
            # Initialize enhanced review data
            review_data = {
                'client_id': client_id,
                'client_name': client.name,
                'start_date': start_date,
                'end_date': end_date,
                'review_type': review_type,
                'generated_at': datetime.utcnow().isoformat(),
                'analysis_classification': {},
                'market_comparison': {},
                'feedback_areas': {},
                'calculation_details': {},
                'sections': {
                    'performance': {},
                    'allocation': {},
                    'transactions': {},
                    'securities': {}
                }
            }
            
            # Generate enhanced sections based on review type
            if review_type in ['comprehensive', 'performance']:
                # Multi-period performance analysis
                review_data['sections']['performance'] = EnhancedReviewService._analyze_multi_period_performance(
                    client, start_dt, end_dt
                )
                review_data['analysis_classification']['performance'] = EnhancedReviewService._classify_multi_period_analysis(
                    review_data['sections']['performance']
                )
                review_data['market_comparison']['performance'] = EnhancedReviewService._compare_multi_period_with_market(
                    review_data['sections']['performance']
                )
            
            if review_type in ['comprehensive', 'allocation']:
                review_data['sections']['allocation'] = EnhancedReviewService._analyze_allocation_enhanced(
                    client, start_dt, end_dt
                )
                review_data['analysis_classification']['allocation'] = EnhancedReviewService._classify_allocation_analysis(
                    review_data['sections']['allocation']
                )
            
            if review_type in ['comprehensive', 'transactions']:
                review_data['sections']['transactions'] = EnhancedReviewService._analyze_transactions_enhanced(
                    client, start_dt, end_dt
                )
                review_data['analysis_classification']['transactions'] = EnhancedReviewService._classify_transaction_analysis(
                    review_data['sections']['transactions']
                )
            
            if review_type in ['comprehensive', 'securities']:
                review_data['sections']['securities'] = EnhancedReviewService._analyze_securities_enhanced(
                    client, start_dt, end_dt
                )
                review_data['analysis_classification']['securities'] = EnhancedReviewService._classify_security_analysis(
                    review_data['sections']['securities']
                )
            
            # Generate feedback areas for improvements
            review_data['feedback_areas'] = EnhancedReviewService._identify_feedback_areas(
                review_data['analysis_classification'], review_data['market_comparison']
            )
            
            # Generate calculation details for audit
            review_data['calculation_details'] = EnhancedReviewService._generate_calculation_details(
                client, start_dt, end_dt, review_data['sections']
            )
            
            # Generate AI text summaries (good and bad aspects) - always generate if data available
            try:
                logger.info("Generating AI text summaries for review")
                ai_summaries = AIInsightsService.generate_ai_text_summary(review_data)
                review_data['ai_text_summaries'] = ai_summaries
                logger.info("AI text summaries generated successfully")
            except Exception as e:
                logger.error(f"Error generating AI text summaries: {str(e)}")
                # Continue without AI summaries if generation fails
                review_data['ai_text_summaries'] = {
                    'error': 'AI summary generation failed',
                    'fallback': True,
                    'generated_at': datetime.utcnow().isoformat()
                }
            
            logger.info(f"=== ENHANCED REVIEW GENERATION COMPLETE ===")
            logger.info(f"Review data keys: {list(review_data.keys())}")
            logger.info(f"Sections: {list(review_data['sections'].keys())}")
            
            return review_data
            
        except Exception as e:
            logger.error(f"Error generating enhanced review for client {client_id}: {str(e)}")
            raise
    
    @staticmethod
    def _analyze_multi_period_performance(client, start_date, end_date):
        """Analyze performance across multiple time periods (6 months, 1 year, till date)"""
        try:
            logger.info(f"=== MULTI-PERIOD PERFORMANCE ANALYSIS ===")
            logger.info(f"Client ID: {client.id}, Name: {client.name}")
            
            from datetime import timedelta
            today = datetime.utcnow().date()
            
            # Define periods
            periods = {
                '6_months': {
                    'start': today - timedelta(days=180),
                    'end': today,
                    'label': 'Last 6 Months'
                },
                '1_year': {
                    'start': today - timedelta(days=365),
                    'end': today,
                    'label': 'Last 1 Year'
                },
                'till_date': {
                    'start': start_date,  # Use the earliest available date
                    'end': today,
                    'label': 'Till Date'
                }
            }
            
            multi_period_data = {}
            
            for period_key, period_info in periods.items():
                logger.info(f"Analyzing {period_key}: {period_info['start']} to {period_info['end']}")
                
                # Analyze performance for this period
                period_performance = EnhancedReviewService._analyze_performance_for_period(
                    client, period_info['start'], period_info['end']
                )
                
                # Add period metadata
                period_performance['period'] = period_key
                period_performance['period_label'] = period_info['label']
                period_performance['start_date'] = period_info['start']
                period_performance['end_date'] = period_info['end']
                period_performance['days'] = (period_info['end'] - period_info['start']).days
                
                multi_period_data[period_key] = period_performance
            
            # Add summary statistics
            multi_period_data['summary'] = EnhancedReviewService._calculate_performance_summary(multi_period_data)
            
            # Add AI-powered insights for each period (if enabled)
            if include_ai_insights:
                for period_key in periods.keys():
                    if period_key in multi_period_data:
                        period_data = multi_period_data[period_key]
                        
                        # Generate performance insights
                        performance_insights = AIInsightsService.get_performance_insights(period_data)
                        period_data['ai_insights'] = performance_insights
                        
                        # Generate risk insights
                        risk_insights = AIInsightsService.get_risk_insights(period_data.get('risk_metrics', {}))
                        period_data['risk_insights'] = risk_insights
                
                # Add period comparison insights
                comparison_insights = AIInsightsService.get_period_comparison_insights(multi_period_data)
                multi_period_data['comparison_insights'] = comparison_insights
                
                # Add actionable recommendations
                if '1_year' in multi_period_data:
                    recommendations = AIInsightsService.get_actionable_recommendations(
                        multi_period_data['1_year'],
                        multi_period_data['1_year'].get('risk_metrics', {})
                    )
                    multi_period_data['recommendations'] = recommendations
            else:
                logger.info("AI insights generation disabled - generating basic performance data only")
            
            return multi_period_data
            
        except Exception as e:
            logger.error(f"Error in multi-period performance analysis: {str(e)}")
            return {
                'error': str(e),
                'data_quality': 'low'
            }
    
    @staticmethod
    def _analyze_performance_for_period(client, start_date, end_date):
        """Analyze performance for a specific period using Portfolio Analytics API"""
        try:
            logger.info(f"=== ANALYZING PERIOD USING PORTFOLIO ANALYTICS API: {start_date} to {end_date} ===")
            
            # Import performance API functions
            from api.v1.performance import (
                calculate_xirr, calculate_risk_metrics, calculate_performance_metrics,
                calculate_returns_data, calculate_summary_metrics
            )
            from models import Cashflow, Holding
            from services.forward_holding_calculation_service import get_client_portfolio_by_date
            
            # Get portfolio value at start of period using portfolio construction API
            start_portfolio = get_client_portfolio_by_date(client.id, start_date)
            start_value = start_portfolio.get('total_value', 0)
            logger.info(f"Period XIRR: Portfolio value at start_date ({start_date}): ₹{start_value:,.2f}")
            
            # Get portfolio value at end of period using portfolio construction API
            end_portfolio = get_client_portfolio_by_date(client.id, end_date)
            end_value = end_portfolio.get('total_value', 0)
            logger.info(f"Period XIRR: Portfolio value at end_date ({end_date}): ₹{end_value:,.2f}")
            
            # Fallback to current holdings calculation if portfolio construction API returned 0
            if end_value == 0:
                logger.warning("Portfolio construction API returned 0 for end value, falling back to current holdings")
                holdings = Holding.query.filter_by(client_id=client.id).all()
                end_value = 0.0
                for h in holdings:
                    if h.security and h.security.current_price:
                        end_value += float(h.quantity * h.security.current_price)
                    elif h.security and h.security.price:
                        end_value += float(h.quantity * h.security.price)
                    else:
                        end_value += float(h.quantity * h.average_price)
            
            if start_value == 0:
                logger.warning("Portfolio construction API returned 0 for start value, using cost basis")
                holdings = Holding.query.filter_by(client_id=client.id).all()
                start_value = 0.0
                for h in holdings:
                    start_value += float(h.quantity * h.average_price)
            
            # Get cashflows during the period (between start_date and end_date)
            period_cashflows = Cashflow.query.filter(
                and_(
                    Cashflow.client_id == client.id,
                    Cashflow.date >= start_date,
                    Cashflow.date <= end_date
                )
            ).order_by(Cashflow.date).all()
            
            logger.info(f"Found {len(period_cashflows)} cashflows during period for client {client.id}")
            
            # Get holdings for risk metrics calculation
            holdings = Holding.query.filter_by(client_id=client.id).all()
            total_cost = 0.0
            for h in holdings:
                total_cost += float(h.quantity * h.average_price)
            
            # Calculate XIRR for the period
            # Build cashflow list for period XIRR:
            # 1. Portfolio value at start_date as negative cashflow (inflow/investment)
            # 2. All intermediate cashflows with their sign
            # 3. Current value at end_date as positive cashflow (withdrawal)
            xirr_data = {}
            cashflow_data = []
            
            if start_value > 0:
                # Add portfolio value at start as negative cashflow (inflow/investment)
                cashflow_data.append((datetime.combine(start_date, datetime.min.time()), -float(start_value)))
                logger.info(f"Period XIRR: Added start portfolio value ₹{start_value:,.2f} as investment at {start_date}")
            
            # Add all cashflows during the period (with their original sign)
            for cf in period_cashflows:
                cashflow_data.append((cf.date, float(cf.amount)))
                logger.debug(f"Period XIRR: Added cashflow ₹{cf.amount:,.2f} on {cf.date}")
            
            if end_value > 0:
                # Add current value as positive cashflow (withdrawal)
                cashflow_data.append((datetime.combine(end_date, datetime.max.time()), float(end_value)))
                logger.info(f"Period XIRR: Added end portfolio value ₹{end_value:,.2f} as withdrawal at {end_date}")
            
            if cashflow_data:
                # Log detailed cashflow data for verification
                logger.info(f"=== PERIOD XIRR CALCULATION INPUTS ===")
                logger.info(f"Period: {start_date} to {end_date}")
                logger.info(f"Start Portfolio Value: ₹{start_value:,.2f} (at {start_date})")
                logger.info(f"End Portfolio Value: ₹{end_value:,.2f} (at {end_date})")
                logger.info(f"Cashflows during period: {len(period_cashflows)}")
                logger.info(f"Total cashflows for XIRR: {len(cashflow_data)}")
                
                # Calculate XIRR with the period cashflows
                # Note: We pass 0 as current_value because we've already included end_value at end_date in cashflow_data
                xirr, total_invested, total_withdrawn, net_investment, _ = calculate_xirr(
                    cashflow_data, 0  # current_value is already in cashflow_data at end_date
                )
                
                # Calculate absolute return
                absolute_return = ((end_value - start_value) / start_value * 100) if start_value > 0 else 0.0
                
                xirr_data = {
                    'xirr': xirr,
                    'xirr_percent': xirr * 100,
                    'total_invested': total_invested,
                    'total_withdrawn': total_withdrawn,
                    'net_investment': net_investment,
                    'absolute_return': absolute_return,
                    'start_value': start_value,
                    'end_value': end_value
                }
                logger.info(f"Period XIRR calculated: {xirr * 100:.2f}% (Start: ₹{start_value:,.2f}, End: ₹{end_value:,.2f}, Period CFs: {len(period_cashflows)})")
                logger.info(f"=== END XIRR CALCULATION ===")
            else:
                logger.warning("No cashflows available for period XIRR calculation")
                xirr_data = {
                    'xirr': 0.0,
                    'xirr_percent': 0.0,
                    'total_invested': 0.0,
                    'total_withdrawn': 0.0,
                    'net_investment': 0.0,
                    'absolute_return': 0.0,
                    'start_value': start_value,
                    'end_value': end_value
                }
            
            # Calculate risk metrics using API
            risk_metrics = calculate_risk_metrics(holdings, client.id)
            logger.info(f"Risk metrics calculated: {risk_metrics}")
            
            # Calculate returns data for the period
            returns_data = calculate_returns_data(holdings, start_date, end_date, 'daily')
            summary_metrics = calculate_summary_metrics(returns_data)
            
            # Calculate period return
            days_elapsed = (end_date - start_date).days
            if days_elapsed > 0:
                # Use XIRR if available, otherwise estimate from start vs end value
                if xirr_data and xirr_data.get('xirr'):
                    period_return_percent = xirr_data['xirr_percent']
                    annualized_return = period_return_percent
                else:
                    # Fallback calculation
                    if start_value > 0:
                        period_return_percent = ((end_value - start_value) / start_value) * 100
                        annualized_return = ((1 + period_return_percent / 100) ** (365 / days_elapsed) - 1) * 100
                    else:
                        period_return_percent = 0
                        annualized_return = 0
            else:
                period_return_percent = 0
                annualized_return = 0
            
            # Get benchmark performance for this period
            benchmark_return = EnhancedReviewService._get_benchmark_return_for_period(start_date, end_date)
            
            # Calculate excess return
            excess_return = period_return_percent - benchmark_return if benchmark_return else 0
            
            # Calculate segment-wise XIRR
            try:
                from api.v1.period_analysis import calculate_segment_wise_xirr
                segment_wise_xirr_data = calculate_segment_wise_xirr(client.id, start_date, end_date)
                logger.info(f"Segment-wise XIRR calculated for {len(segment_wise_xirr_data.get('segments', {}))} asset classes")
            except Exception as e:
                logger.warning(f"Error calculating segment-wise XIRR: {str(e)}")
                segment_wise_xirr_data = {
                    'segments': {},
                    'summary': {
                        'total_segments': 0,
                        'total_portfolio_value': 0.0,
                        'weighted_avg_xirr': 0.0
                    }
                }
            
            # Prepare comprehensive performance data
            performance_data = {
                'start_date': start_date,
                'end_date': end_date,
                'days': days_elapsed,
                'current_value': float(end_value),
                'total_cost': float(total_cost),
                'start_value': float(start_value),
                'end_value': float(end_value),
                'total_return': float(end_value - start_value),
                'total_return_percent': float(period_return_percent),
                'annualized_return': float(annualized_return),
                'xirr_data': xirr_data,
                'segment_wise_xirr': segment_wise_xirr_data,
                'benchmark_return': benchmark_return,
                'excess_return': excess_return,
                'outperformance': excess_return,
                'absolute_return': float(xirr_data.get('absolute_return', 0)) if xirr_data else float(end_value - start_value),
                'absolute_return_percent': float(period_return_percent),
                'risk_metrics': risk_metrics,
                'volatility': risk_metrics.get('volatility', 0),
                'sharpe_ratio': risk_metrics.get('sharpe_ratio', 0),
                'max_drawdown': risk_metrics.get('max_drawdown', 0),
                'beta': risk_metrics.get('beta', 1.0),
                'var_95': risk_metrics.get('var_95', 0),
                'sortino_ratio': risk_metrics.get('sortino_ratio', 0),
                'returns_data': returns_data,
                'summary_metrics': summary_metrics,
                'calculation_method': 'portfolio_construction_api',
                'data_quality': 'high' if xirr_data and xirr_data.get('xirr') else 'medium'
            }
            
            logger.info(f"Performance analysis completed for period {start_date} to {end_date}")
            return performance_data
            
        except Exception as e:
            logger.error(f"Error analyzing performance for period using Portfolio Analytics API: {str(e)}")
            # Fallback to simplified calculation
            return EnhancedReviewService._analyze_performance_for_period_fallback(client, start_date, end_date)
    
    @staticmethod
    def _analyze_performance_for_period_fallback(client, start_date, end_date):
        """Fallback performance analysis when Portfolio Analytics API fails"""
        try:
            logger.info(f"=== FALLBACK PERFORMANCE ANALYSIS: {start_date} to {end_date} ===")
            
            from models import Holding
            
            # Get current portfolio value from holdings
            holdings = Holding.query.filter_by(client_id=client.id).all()
            current_value = 0.0
            total_cost = 0.0
            
            for h in holdings:
                if h.security and h.security.current_price:
                    current_value += float(h.quantity * h.security.current_price)
                    total_cost += float(h.quantity * h.average_price)
                elif h.security and h.security.price:
                    current_value += float(h.quantity * h.security.price)
                    total_cost += float(h.quantity * h.average_price)
                else:
                    current_value += float(h.quantity * h.average_price)
                    total_cost += float(h.quantity * h.average_price)
            
            # Calculate performance metrics
            if total_cost > 0:
                total_return_percent = ((current_value - total_cost) / total_cost) * 100
                absolute_return = current_value - total_cost
            else:
                total_return_percent = 0
                absolute_return = 0
            
            # Calculate annualized return
            days_elapsed = (end_date - start_date).days
            if days_elapsed > 0:
                annualized_return = ((1 + total_return_percent / 100) ** (365 / days_elapsed) - 1) * 100
            else:
                annualized_return = 0
            
            # Get benchmark performance for this period
            benchmark_return = EnhancedReviewService._get_benchmark_return_for_period(start_date, end_date)
            
            # Calculate excess return
            excess_return = total_return_percent - benchmark_return if benchmark_return else 0
            
            return {
                'start_date': start_date,
                'end_date': end_date,
                'days': days_elapsed,
                'start_value': float(total_cost),
                'end_value': float(current_value),
                'total_return': float(current_value - total_cost),
                'total_return_percent': float(total_return_percent),
                'annualized_return': float(annualized_return),
                'benchmark_return': benchmark_return,
                'excess_return': excess_return,
                'outperformance': excess_return,
                'absolute_return': float(absolute_return),
                'absolute_return_percent': float(total_return_percent),
                'risk_metrics': {},
                'volatility': 0,
                'sharpe_ratio': 0,
                'max_drawdown': 0,
                'beta': 1.0,
                'var_95': 0,
                'sortino_ratio': 0,
                'calculation_method': 'fallback',
                'data_quality': 'low'
            }
            
        except Exception as e:
            logger.error(f"Error in fallback performance analysis: {str(e)}")
            return {
                'error': str(e),
                'data_quality': 'low',
                'start_value': 0,
                'end_value': 0,
                'total_return': 0,
                'total_return_percent': 0,
                'annualized_return': 0,
                'benchmark_return': 0,
                'excess_return': 0,
                'outperformance': 0,
                'absolute_return': 0,
                'absolute_return_percent': 0,
                'risk_metrics': {},
                'volatility': 0,
                'sharpe_ratio': 0,
                'max_drawdown': 0,
                'beta': 1.0,
                'var_95': 0,
                'sortino_ratio': 0,
                'calculation_method': 'error',
                'data_quality': 'low'
            }
    
    @staticmethod
    def _get_benchmark_return_for_period(start_date, end_date):
        """Get benchmark return for a specific period"""
        try:
            days = (end_date - start_date).days
            if days <= 0:
                return 0
            
            # Use realistic benchmark returns based on period
            if days <= 30:  # Monthly
                annual_return = 0.08  # 8% annual
            elif days <= 90:  # Quarterly  
                annual_return = 0.09  # 9% annual
            elif days <= 180:  # Half yearly
                annual_return = 0.10  # 10% annual
            elif days <= 365:  # Yearly
                annual_return = 0.12  # 12% annual
            else:  # More than a year
                annual_return = 0.13  # 13% annual
            
            period_return = (1 + annual_return) ** (days / 365) - 1
            logger.info(f"Benchmark return for {days} days: {annual_return*100}% annual = {period_return*100:.2f}%")
            return period_return * 100
        except Exception as e:
            logger.error(f"Error getting benchmark return for period: {str(e)}")
            return 0
    
    @staticmethod
    def _calculate_performance_summary(multi_period_data):
        """Calculate summary statistics across all periods"""
        try:
            periods = ['6_months', '1_year', 'till_date']
            
            summary = {
                'best_performing_period': None,
                'worst_performing_period': None,
                'average_return': 0,
                'average_excess_return': 0,
                'consistency_score': 0,
                'total_periods_analyzed': len(periods)
            }
            
            returns = []
            excess_returns = []
            
            for period in periods:
                if period in multi_period_data and 'total_return_percent' in multi_period_data[period]:
                    returns.append(multi_period_data[period]['total_return_percent'])
                    excess_returns.append(multi_period_data[period]['excess_return'])
            
            if returns:
                summary['average_return'] = sum(returns) / len(returns)
                summary['average_excess_return'] = sum(excess_returns) / len(excess_returns)
                
                # Find best and worst performing periods
                best_return = max(returns)
                worst_return = min(returns)
                
                for period in periods:
                    if period in multi_period_data:
                        if multi_period_data[period]['total_return_percent'] == best_return:
                            summary['best_performing_period'] = period
                        if multi_period_data[period]['total_return_percent'] == worst_return:
                            summary['worst_performing_period'] = period
                
                # Calculate consistency score (lower standard deviation = more consistent)
                if len(returns) > 1:
                    mean_return = sum(returns) / len(returns)
                    variance = sum((x - mean_return) ** 2 for x in returns) / len(returns)
                    std_dev = variance ** 0.5
                    summary['consistency_score'] = max(0, 100 - std_dev)  # Higher score = more consistent
            
            return summary
            
        except Exception as e:
            logger.error(f"Error calculating performance summary: {str(e)}")
            return {
                'best_performing_period': None,
                'worst_performing_period': None,
                'average_return': 0,
                'average_excess_return': 0,
                'consistency_score': 0,
                'total_periods_analyzed': 0
            }
    
    @staticmethod
    def _analyze_performance_enhanced(client, start_date, end_date):
        """Enhanced performance analysis with detailed metrics"""
        try:
            logger.info(f"=== ENHANCED PERFORMANCE ANALYSIS ===")
            logger.info(f"Client ID: {client.id}, Name: {client.name}")
            logger.info(f"Start Date: {start_date}, End Date: {end_date}")
            
            # Get portfolio snapshots
            start_snapshot = PortfolioSnapshot.query.filter(
                and_(
                    PortfolioSnapshot.client_id == client.id,
                    PortfolioSnapshot.date >= start_date
                )
            ).order_by(PortfolioSnapshot.date).first()
            
            end_snapshot = PortfolioSnapshot.query.filter(
                and_(
                    PortfolioSnapshot.client_id == client.id,
                    PortfolioSnapshot.date <= end_date
                )
            ).order_by(desc(PortfolioSnapshot.date)).first()
            
            if not start_snapshot or not end_snapshot:
                logger.warning("Insufficient snapshot data, using current holdings calculation")
                return EnhancedReviewService._analyze_performance_with_current_holdings(
                    client, start_date, end_date
                )
            
            # Calculate enhanced performance metrics
            total_return = float(end_snapshot.total_value - start_snapshot.total_value)
            total_return_percent = float(end_snapshot.total_value / start_snapshot.total_value - 1) * 100
            xirr = float(end_snapshot.xirr) if end_snapshot.xirr else None
            
            # Get benchmark performance
            benchmark_return = EnhancedReviewService._get_benchmark_return_enhanced(start_date, end_date)
            
            # Calculate additional metrics
            annualized_return = EnhancedReviewService._calculate_annualized_return(
                start_date, end_date, total_return_percent
            )
            
            # Risk metrics
            risk_metrics = EnhancedReviewService._calculate_risk_metrics(
                client, start_date, end_date
            )
            
            return {
                'start_value': float(start_snapshot.total_value),
                'end_value': float(end_snapshot.total_value),
                'total_return': total_return,
                'total_return_percent': total_return_percent,
                'annualized_return': annualized_return,
                'xirr': xirr,
                'benchmark_return': benchmark_return,
                'excess_return': total_return_percent - benchmark_return if benchmark_return else None,
                'outperformance': total_return_percent - benchmark_return if benchmark_return else None,
                'net_investment': float(end_snapshot.net_investment),
                'absolute_return': float(end_snapshot.absolute_return),
                'absolute_return_percent': float(end_snapshot.absolute_return_percent),
                'risk_metrics': risk_metrics,
                'volatility': risk_metrics.get('volatility', 0),
                'sharpe_ratio': risk_metrics.get('sharpe_ratio', 0),
                'max_drawdown': risk_metrics.get('max_drawdown', 0),
                'calculation_method': 'portfolio_snapshots',
                'data_quality': 'high'
            }
            
        except Exception as e:
            logger.error(f"Error in enhanced performance analysis: {str(e)}")
            return {'error': str(e), 'data_quality': 'low'}
    
    @staticmethod
    def _classify_multi_period_analysis(multi_period_data):
        """Classify multi-period performance analysis into positive and negative signs"""
        try:
            if 'error' in multi_period_data:
                return {'classification': 'error', 'positive_signs': [], 'negative_signs': []}
            
            positive_signs = []
            negative_signs = []
            
            periods = ['6_months', '1_year', 'till_date']
            
            for period_key in periods:
                if period_key in multi_period_data:
                    period_data = multi_period_data[period_key]
                    period_label = period_data.get('period_label', period_key)
                    
                    # Analyze each period
                    if period_data.get('total_return_percent', 0) > 0:
                        positive_signs.append({
                            'category': 'Returns',
                            'indicator': f'Positive Returns - {period_label}',
                            'value': f"{period_data['total_return_percent']:.2f}%",
                            'description': f'Portfolio generated positive returns of {period_data["total_return_percent"]:.2f}% in {period_label}',
                            'period': period_key
                        })
                    else:
                        negative_signs.append({
                            'category': 'Returns',
                            'indicator': f'Negative Returns - {period_label}',
                            'value': f"{period_data['total_return_percent']:.2f}%",
                            'description': f'Portfolio generated negative returns of {period_data["total_return_percent"]:.2f}% in {period_label}',
                            'period': period_key
                        })
                    
                    # Benchmark comparison for each period
                    if period_data.get('excess_return', 0) > 0:
                        positive_signs.append({
                            'category': 'Market Comparison',
                            'indicator': f'Outperformed Benchmark - {period_label}',
                            'value': f"{period_data['excess_return']:.2f}%",
                            'description': f'Portfolio outperformed benchmark by {period_data["excess_return"]:.2f}% in {period_label}',
                            'period': period_key
                        })
                    elif period_data.get('excess_return', 0) < -5:  # Significant underperformance
                        negative_signs.append({
                            'category': 'Market Comparison',
                            'indicator': f'Significant Underperformance - {period_label}',
                            'value': f"{period_data['excess_return']:.2f}%",
                            'description': f'Portfolio significantly underperformed benchmark by {abs(period_data["excess_return"]):.2f}% in {period_label}',
                            'period': period_key
                        })
            
            # Analyze consistency across periods
            if 'summary' in multi_period_data:
                summary = multi_period_data['summary']
                
                if summary.get('consistency_score', 0) > 80:
                    positive_signs.append({
                        'category': 'Consistency',
                        'indicator': 'High Performance Consistency',
                        'value': f"{summary['consistency_score']:.1f}",
                        'description': f'Portfolio shows consistent performance across all periods with a consistency score of {summary["consistency_score"]:.1f}',
                        'period': 'all'
                    })
                elif summary.get('consistency_score', 0) < 50:
                    negative_signs.append({
                        'category': 'Consistency',
                        'indicator': 'Inconsistent Performance',
                        'value': f"{summary['consistency_score']:.1f}",
                        'description': f'Portfolio shows inconsistent performance across periods with a consistency score of {summary["consistency_score"]:.1f}',
                        'period': 'all'
                    })
                
                # Best and worst periods
                if summary.get('best_performing_period'):
                    best_period = summary['best_performing_period']
                    best_label = multi_period_data.get(best_period, {}).get('period_label', best_period)
                    positive_signs.append({
                        'category': 'Performance Highlights',
                        'indicator': f'Best Performing Period - {best_label}',
                        'value': f"{multi_period_data[best_period]['total_return_percent']:.2f}%",
                        'description': f'{best_label} was the best performing period with {multi_period_data[best_period]["total_return_percent"]:.2f}% returns',
                        'period': best_period
                    })
                
                if summary.get('worst_performing_period'):
                    worst_period = summary['worst_performing_period']
                    worst_label = multi_period_data.get(worst_period, {}).get('period_label', worst_period)
                    negative_signs.append({
                        'category': 'Performance Highlights',
                        'indicator': f'Worst Performing Period - {worst_label}',
                        'value': f"{multi_period_data[worst_period]['total_return_percent']:.2f}%",
                        'description': f'{worst_label} was the worst performing period with {multi_period_data[worst_period]["total_return_percent"]:.2f}% returns',
                        'period': worst_period
                    })
            
            return {
                'classification': 'completed',
                'positive_signs': positive_signs,
                'negative_signs': negative_signs,
                'total_positive': len(positive_signs),
                'total_negative': len(negative_signs),
                'overall_sentiment': 'positive' if len(positive_signs) > len(negative_signs) else 'negative',
                'periods_analyzed': len(periods)
            }
            
        except Exception as e:
            logger.error(f"Error classifying multi-period analysis: {str(e)}")
            return {'classification': 'error', 'positive_signs': [], 'negative_signs': []}
    
    @staticmethod
    def _compare_multi_period_with_market(multi_period_data):
        """Compare multi-period performance with market benchmarks"""
        try:
            if 'error' in multi_period_data:
                return {'error': 'Invalid data'}
            
            comparison = {
                'periods': {},
                'overall_performance': {},
                'highlight_areas': []
            }
            
            periods = ['6_months', '1_year', 'till_date']
            
            for period_key in periods:
                if period_key in multi_period_data:
                    period_data = multi_period_data[period_key]
                    
                    comparison['periods'][period_key] = {
                        'portfolio_return': period_data.get('total_return_percent', 0),
                        'benchmark_return': period_data.get('benchmark_return', 0),
                        'excess_return': period_data.get('excess_return', 0),
                        'period_label': period_data.get('period_label', period_key)
                    }
                    
                    # Add period-specific highlights
                    excess_return = period_data.get('excess_return', 0)
                    if excess_return > 10:
                        comparison['highlight_areas'].append({
                            'type': 'excellent_outperformance',
                            'period': period_key,
                            'period_label': period_data.get('period_label', period_key),
                            'message': f'Exceptional outperformance of {excess_return:.2f}% vs benchmark',
                            'color': 'success'
                        })
                    elif excess_return > 5:
                        comparison['highlight_areas'].append({
                            'type': 'strong_outperformance',
                            'period': period_key,
                            'period_label': period_data.get('period_label', period_key),
                            'message': f'Strong outperformance of {excess_return:.2f}% vs benchmark',
                            'color': 'success'
                        })
                    elif excess_return < -10:
                        comparison['highlight_areas'].append({
                            'type': 'significant_underperformance',
                            'period': period_key,
                            'period_label': period_data.get('period_label', period_key),
                            'message': f'Significant underperformance of {abs(excess_return):.2f}% vs benchmark',
                            'color': 'danger'
                        })
                    elif excess_return < -5:
                        comparison['highlight_areas'].append({
                            'type': 'underperformance',
                            'period': period_key,
                            'period_label': period_data.get('period_label', period_key),
                            'message': f'Underperformance of {abs(excess_return):.2f}% vs benchmark',
                            'color': 'warning'
                        })
            
            # Overall performance summary
            if 'summary' in multi_period_data:
                summary = multi_period_data['summary']
                comparison['overall_performance'] = {
                    'average_return': summary.get('average_return', 0),
                    'average_excess_return': summary.get('average_excess_return', 0),
                    'consistency_score': summary.get('consistency_score', 0),
                    'best_period': summary.get('best_performing_period'),
                    'worst_period': summary.get('worst_performing_period')
                }
            
            return comparison
            
        except Exception as e:
            logger.error(f"Error comparing multi-period with market: {str(e)}")
            return {'error': str(e)}
    
    @staticmethod
    def _classify_performance_analysis(performance_data):
        """Classify performance analysis into positive and negative signs"""
        if 'error' in performance_data:
            return {'classification': 'error', 'positive_signs': [], 'negative_signs': []}
        
        positive_signs = []
        negative_signs = []
        
        # Total return analysis
        if performance_data.get('total_return_percent', 0) > 0:
            positive_signs.append({
                'category': 'Returns',
                'indicator': 'Positive Returns',
                'value': f"{performance_data['total_return_percent']:.2f}%",
                'description': 'Portfolio generated positive returns during the period'
            })
        else:
            negative_signs.append({
                'category': 'Returns',
                'indicator': 'Negative Returns',
                'value': f"{performance_data['total_return_percent']:.2f}%",
                'description': 'Portfolio generated negative returns during the period'
            })
        
        # Benchmark comparison
        if performance_data.get('excess_return', 0) > 0:
            positive_signs.append({
                'category': 'Market Comparison',
                'indicator': 'Outperformed Benchmark',
                'value': f"{performance_data['excess_return']:.2f}%",
                'description': f'Portfolio outperformed benchmark by {performance_data["excess_return"]:.2f}%'
            })
        elif performance_data.get('excess_return', 0) < -5:  # Significant underperformance
            negative_signs.append({
                'category': 'Market Comparison',
                'indicator': 'Significant Underperformance',
                'value': f"{performance_data['excess_return']:.2f}%",
                'description': f'Portfolio significantly underperformed benchmark by {abs(performance_data["excess_return"]):.2f}%'
            })
        
        # Risk-adjusted returns
        if performance_data.get('sharpe_ratio', 0) > 1.0:
            positive_signs.append({
                'category': 'Risk Management',
                'indicator': 'Good Risk-Adjusted Returns',
                'value': f"{performance_data['sharpe_ratio']:.2f}",
                'description': 'Sharpe ratio indicates good risk-adjusted returns'
            })
        elif performance_data.get('sharpe_ratio', 0) < 0:
            negative_signs.append({
                'category': 'Risk Management',
                'indicator': 'Poor Risk-Adjusted Returns',
                'value': f"{performance_data['sharpe_ratio']:.2f}",
                'description': 'Negative Sharpe ratio indicates poor risk-adjusted returns'
            })
        
        # Volatility analysis
        if performance_data.get('volatility', 0) < 15:  # Low volatility
            positive_signs.append({
                'category': 'Risk Management',
                'indicator': 'Low Volatility',
                'value': f"{performance_data['volatility']:.2f}%",
                'description': 'Portfolio shows low volatility, indicating stable returns'
            })
        elif performance_data.get('volatility', 0) > 25:  # High volatility
            negative_signs.append({
                'category': 'Risk Management',
                'indicator': 'High Volatility',
                'value': f"{performance_data['volatility']:.2f}%",
                'description': 'Portfolio shows high volatility, indicating unstable returns'
            })
        
        # Drawdown analysis
        if performance_data.get('max_drawdown', 0) > -10:  # Low drawdown
            positive_signs.append({
                'category': 'Risk Management',
                'indicator': 'Low Maximum Drawdown',
                'value': f"{performance_data['max_drawdown']:.2f}%",
                'description': 'Portfolio experienced low maximum drawdown'
            })
        elif performance_data.get('max_drawdown', 0) < -20:  # High drawdown
            negative_signs.append({
                'category': 'Risk Management',
                'indicator': 'High Maximum Drawdown',
                'value': f"{performance_data['max_drawdown']:.2f}%",
                'description': 'Portfolio experienced high maximum drawdown'
            })
        
        return {
            'classification': 'completed',
            'positive_signs': positive_signs,
            'negative_signs': negative_signs,
            'total_positive': len(positive_signs),
            'total_negative': len(negative_signs),
            'overall_sentiment': 'positive' if len(positive_signs) > len(negative_signs) else 'negative'
        }
    
    @staticmethod
    def _compare_with_market(performance_data, start_date, end_date):
        """Enhanced market comparison with highlighting"""
        benchmark_return = performance_data.get('benchmark_return', 0)
        portfolio_return = performance_data.get('total_return_percent', 0)
        excess_return = performance_data.get('excess_return', 0)
        
        # Get additional benchmark data
        benchmark_data = EnhancedReviewService._get_detailed_benchmark_data(start_date, end_date)
        
        comparison = {
            'portfolio_return': portfolio_return,
            'benchmark_return': benchmark_return,
            'excess_return': excess_return,
            'outperformance_percentage': excess_return,
            'performance_vs_market': 'outperformed' if excess_return > 0 else 'underperformed',
            'highlight_areas': [],
            'benchmark_details': benchmark_data
        }
        
        # Highlight areas where we're doing better
        if excess_return > 5:
            comparison['highlight_areas'].append({
                'type': 'excellent_outperformance',
                'message': f'Portfolio significantly outperformed market by {excess_return:.2f}%',
                'color': 'success'
            })
        elif excess_return > 0:
            comparison['highlight_areas'].append({
                'type': 'positive_outperformance',
                'message': f'Portfolio outperformed market by {excess_return:.2f}%',
                'color': 'success'
            })
        elif excess_return < -10:
            comparison['highlight_areas'].append({
                'type': 'significant_underperformance',
                'message': f'Portfolio significantly underperformed market by {abs(excess_return):.2f}%',
                'color': 'danger'
            })
        elif excess_return < 0:
            comparison['highlight_areas'].append({
                'type': 'underperformance',
                'message': f'Portfolio underperformed market by {abs(excess_return):.2f}%',
                'color': 'warning'
            })
        
        # Risk-adjusted comparison
        if performance_data.get('sharpe_ratio', 0) > 1.0:
            comparison['highlight_areas'].append({
                'type': 'superior_risk_adjusted',
                'message': 'Portfolio shows superior risk-adjusted returns compared to market',
                'color': 'success'
            })
        
        return comparison
    
    @staticmethod
    def _identify_feedback_areas(analysis_classification, market_comparison):
        """Identify areas that need improvement and collect feedback"""
        feedback_areas = []
        
        # Performance feedback
        if 'performance' in analysis_classification:
            perf_class = analysis_classification['performance']
            
            # Areas needing improvement
            for negative_sign in perf_class.get('negative_signs', []):
                if negative_sign['category'] == 'Returns':
                    feedback_areas.append({
                        'area': 'Return Generation',
                        'priority': 'high',
                        'issue': negative_sign['description'],
                        'suggestion': 'Review investment strategy and consider rebalancing portfolio',
                        'action_items': [
                            'Analyze underperforming holdings',
                            'Consider sector rotation',
                            'Review asset allocation'
                        ]
                    })
                
                elif negative_sign['category'] == 'Market Comparison':
                    feedback_areas.append({
                        'area': 'Market Performance',
                        'priority': 'high',
                        'issue': negative_sign['description'],
                        'suggestion': 'Focus on improving relative performance vs benchmark',
                        'action_items': [
                            'Identify benchmark components',
                            'Analyze sector weightings',
                            'Consider tactical allocation changes'
                        ]
                    })
                
                elif negative_sign['category'] == 'Risk Management':
                    feedback_areas.append({
                        'area': 'Risk Management',
                        'priority': 'medium',
                        'issue': negative_sign['description'],
                        'suggestion': 'Improve risk management and portfolio stability',
                        'action_items': [
                            'Review diversification',
                            'Consider risk controls',
                            'Analyze correlation patterns'
                        ]
                    })
        
        return {
            'total_areas': len(feedback_areas),
            'high_priority': len([f for f in feedback_areas if f['priority'] == 'high']),
            'medium_priority': len([f for f in feedback_areas if f['priority'] == 'medium']),
            'low_priority': len([f for f in feedback_areas if f['priority'] == 'low']),
            'areas': feedback_areas
        }
    
    @staticmethod
    def _generate_calculation_details(client, start_date, end_date, sections):
        """Generate detailed calculation file for audit purposes"""
        calculation_details = {
            'client_info': {
                'id': client.id,
                'name': client.name,
                'email': client.email
            },
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'days': (end_date - start_date).days
            },
            'calculations': {},
            'data_sources': {},
            'methodology': {},
            'timestamp': datetime.utcnow().isoformat()
        }
        
        # Performance calculations
        if 'performance' in sections:
            perf_data = sections['performance']
            calculation_details['calculations']['performance'] = {
                'start_value': perf_data.get('start_value', 0),
                'end_value': perf_data.get('end_value', 0),
                'total_return': perf_data.get('total_return', 0),
                'total_return_percent': perf_data.get('total_return_percent', 0),
                'annualized_return': perf_data.get('annualized_return', 0),
                'xirr': perf_data.get('xirr', 0),
                'benchmark_return': perf_data.get('benchmark_return', 0),
                'excess_return': perf_data.get('excess_return', 0),
                'calculation_method': perf_data.get('calculation_method', 'unknown'),
                'formulas': {
                    'total_return_percent': '(end_value - start_value) / start_value * 100',
                    'excess_return': 'portfolio_return - benchmark_return',
                    'annualized_return': '((1 + total_return_percent/100) ^ (365/days)) - 1) * 100'
                }
            }
        
        # Data sources
        calculation_details['data_sources'] = {
            'portfolio_snapshots': 'PortfolioSnapshot table',
            'benchmark_data': 'BenchmarkData table or external API',
            'transactions': 'Transaction table',
            'holdings': 'Holding table',
            'securities': 'Security table'
        }
        
        # Methodology
        calculation_details['methodology'] = {
            'performance_calculation': 'Time-weighted returns using portfolio snapshots',
            'benchmark_comparison': 'Nifty 50 or specified benchmark index',
            'risk_metrics': 'Historical volatility and drawdown calculations',
            'classification_logic': 'Rule-based classification of positive/negative indicators'
        }
        
        return calculation_details
    
    @staticmethod
    def _get_benchmark_return_enhanced(start_date, end_date):
        """Get enhanced benchmark return data"""
        try:
            # For now, using a simplified benchmark return
            # In production, this should fetch real benchmark data
            days = (end_date - start_date).days
            if days <= 0:
                return 0
            
            # Use more realistic benchmark returns based on period
            if days <= 30:  # Monthly
                annual_return = 0.10  # 10% annual
            elif days <= 90:  # Quarterly  
                annual_return = 0.11  # 11% annual
            elif days <= 180:  # Half yearly
                annual_return = 0.12  # 12% annual
            else:  # Yearly or more
                annual_return = 0.13  # 13% annual
            
            period_return = (1 + annual_return) ** (days / 365) - 1
            logger.info(f"Benchmark return: {annual_return*100}% annual for {days} days = {period_return*100:.2f}%")
            return period_return * 100
        except Exception as e:
            logger.error(f"Error getting benchmark return: {str(e)}")
            return 0
    
    @staticmethod
    def _calculate_annualized_return(start_date, end_date, total_return_percent):
        """Calculate annualized return"""
        try:
            days = (end_date - start_date).days
            if days == 0:
                return 0
            annualized = ((1 + total_return_percent / 100) ** (365 / days) - 1) * 100
            return annualized
        except:
            return 0
    
    @staticmethod
    def _calculate_risk_metrics(client, start_date, end_date):
        """Calculate risk metrics"""
        try:
            # Simplified risk calculation - in production, use actual historical data
            return {
                'volatility': 15.5,  # Placeholder
                'sharpe_ratio': 0.8,  # Placeholder
                'max_drawdown': -8.2,  # Placeholder
                'var_95': -5.5  # Placeholder
            }
        except:
            return {
                'volatility': 0,
                'sharpe_ratio': 0,
                'max_drawdown': 0,
                'var_95': 0
            }
    
    @staticmethod
    def _get_detailed_benchmark_data(start_date, end_date):
        """Get detailed benchmark data"""
        return {
            'index_name': 'Nifty 50',
            'period_return': EnhancedReviewService._get_benchmark_return_enhanced(start_date, end_date),
            'volatility': 18.5,  # Placeholder
            'max_drawdown': -12.3  # Placeholder
        }
    
    @staticmethod
    def _analyze_performance_with_current_holdings(client, start_date, end_date):
        """Enhanced performance analysis using current holdings data"""
        try:
            logger.info(f"=== ANALYZING WITH CURRENT HOLDINGS ===")
            
            # Get current portfolio value from holdings
            holdings = Holding.query.filter_by(client_id=client.id).all()
            current_value = 0.0
            total_cost = 0.0
            
            logger.info(f"Found {len(holdings)} holdings for client {client.id}")
            
            for h in holdings:
                if h.security and hasattr(h.security, 'current_price') and h.security.current_price:
                    current_value += float(h.quantity * h.security.current_price)
                    total_cost += float(h.quantity * h.average_price)
                    logger.info(f"  {h.security.symbol}: {h.quantity} * {h.security.current_price} = {h.quantity * h.security.current_price}")
                elif h.security and hasattr(h.security, 'price') and h.security.price:
                    current_value += float(h.quantity * h.security.price)
                    total_cost += float(h.quantity * h.average_price)
                else:
                    # Fallback to average price if current price not available
                    current_value += float(h.quantity * h.average_price)
                    total_cost += float(h.quantity * h.average_price)
            
            logger.info(f"Current portfolio value: {current_value}")
            logger.info(f"Total cost basis: {total_cost}")
            
            # Get cashflow data for the period
            from models import Cashflow
            cashflows = Cashflow.query.filter_by(client_id=client.id).all()
            logger.info(f"Found {len(cashflows)} cashflows")
            
            # Calculate net investment from cashflows
            net_investment = 0.0
            for cf in cashflows:
                net_investment += float(cf.amount)
                logger.info(f"  Cashflow: {cf.amount} on {cf.date}")
            
            logger.info(f"Net investment from cashflows: {net_investment}")
            
            # Calculate performance metrics
            if total_cost > 0:
                total_return_percent = ((current_value - total_cost) / total_cost) * 100
                absolute_return = current_value - total_cost
            else:
                total_return_percent = 0
                absolute_return = 0
            
            # Get benchmark performance
            benchmark_return = EnhancedReviewService._get_benchmark_return_enhanced(start_date, end_date)
            
            # Calculate annualized return
            days = (end_date - start_date).days
            if days > 0:
                annualized_return = ((1 + total_return_percent / 100) ** (365 / days) - 1) * 100
            else:
                annualized_return = 0
            
            # Calculate risk metrics
            risk_metrics = EnhancedReviewService._calculate_risk_metrics(client, start_date, end_date)
            
            return {
                'start_value': float(total_cost),  # Use cost basis as start value
                'end_value': float(current_value),
                'total_return': float(current_value - total_cost),
                'total_return_percent': float(total_return_percent),
                'annualized_return': float(annualized_return),
                'xirr': None,  # Would need more complex calculation
                'benchmark_return': benchmark_return,
                'excess_return': total_return_percent - benchmark_return if benchmark_return else 0,
                'outperformance': total_return_percent - benchmark_return if benchmark_return else 0,
                'net_investment': float(net_investment),
                'absolute_return': float(absolute_return),
                'absolute_return_percent': float(total_return_percent),
                'risk_metrics': risk_metrics,
                'volatility': risk_metrics.get('volatility', 0),
                'sharpe_ratio': risk_metrics.get('sharpe_ratio', 0),
                'max_drawdown': risk_metrics.get('max_drawdown', 0),
                'calculation_method': 'current_holdings',
                'data_quality': 'medium'
            }
            
        except Exception as e:
            logger.error(f"Error analyzing performance with current holdings: {str(e)}")
            return {
                'error': str(e),
                'data_quality': 'low',
                'start_value': 0,
                'end_value': 0,
                'total_return': 0,
                'total_return_percent': 0,
                'annualized_return': 0,
                'xirr': None,
                'benchmark_return': 0,
                'excess_return': 0,
                'outperformance': 0,
                'net_investment': 0,
                'absolute_return': 0,
                'absolute_return_percent': 0,
                'risk_metrics': {},
                'volatility': 0,
                'sharpe_ratio': 0,
                'max_drawdown': 0,
                'calculation_method': 'error',
                'data_quality': 'low'
            }
    
    @staticmethod
    def _analyze_allocation_enhanced(client, start_date, end_date):
        """Enhanced allocation analysis"""
        # Placeholder implementation
        return {'status': 'enhanced_allocation_analysis_pending'}
    
    @staticmethod
    def _classify_allocation_analysis(allocation_data):
        """Classify allocation analysis"""
        return {'classification': 'pending', 'positive_signs': [], 'negative_signs': []}
    
    @staticmethod
    def _analyze_transactions_enhanced(client, start_date, end_date):
        """Enhanced transaction analysis"""
        # Placeholder implementation
        return {'status': 'enhanced_transaction_analysis_pending'}
    
    @staticmethod
    def _classify_transaction_analysis(transaction_data):
        """Classify transaction analysis"""
        return {'classification': 'pending', 'positive_signs': [], 'negative_signs': []}
    
    @staticmethod
    def _analyze_securities_enhanced(client, start_date, end_date):
        """Enhanced securities analysis"""
        # Placeholder implementation
        return {'status': 'enhanced_securities_analysis_pending'}
    
    @staticmethod
    def _classify_security_analysis(securities_data):
        """Classify security analysis"""
        return {'classification': 'pending', 'positive_signs': [], 'negative_signs': []}
    
    @staticmethod
    def generate_calculation_file(review_data, file_format='excel'):
        """Generate calculation file for download"""
        try:
            if file_format == 'excel':
                return EnhancedReviewService._generate_excel_calculation_file(review_data)
            elif file_format == 'csv':
                return EnhancedReviewService._generate_csv_calculation_file(review_data)
            else:
                return EnhancedReviewService._generate_json_calculation_file(review_data)
        except Exception as e:
            logger.error(f"Error generating calculation file: {str(e)}")
            raise

    @staticmethod
    def generate_period_data(client_id, start_date, end_date, period_type):
        """Generate data for a specific period only"""
        try:
            logger.info(f"=== GENERATING {period_type.upper()} DATA ===")
            
            # Get client
            client = Client.query.get(client_id)
            if not client:
                raise ValueError(f"Client with ID {client_id} not found")
            
            # Parse dates
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
            
            # Calculate period dates
            from datetime import timedelta
            today = datetime.utcnow().date()
            
            if period_type == '6_months':
                period_start = today - timedelta(days=180)
                period_end = today
            elif period_type == '1_year':
                period_start = today - timedelta(days=365)
                period_end = today
            elif period_type == 'till_date':
                period_start = start_dt
                period_end = today
            else:
                raise ValueError(f"Invalid period type: {period_type}")
            
            # Analyze performance for this period only
            period_performance = EnhancedReviewService._analyze_performance_for_period(
                client, period_start, period_end
            )
            
            # Add period metadata
            period_performance['period'] = period_type
            period_performance['period_label'] = period_type.replace('_', ' ').title()
            period_performance['start_date'] = period_start
            period_performance['end_date'] = period_end
            period_performance['days'] = (period_end - period_start).days
            
            return {
                'period_type': period_type,
                'period_data': period_performance,
                'client_id': client_id,
                'client_name': client.name,
                'generated_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error generating period data for {period_type}: {str(e)}")
            raise
    
    @staticmethod
    def generate_ai_insights(client_id, start_date, end_date):
        """Generate AI insights for all periods"""
        try:
            logger.info(f"=== GENERATING AI INSIGHTS ===")
            
            # Get client
            client = Client.query.get(client_id)
            if not client:
                raise ValueError(f"Client with ID {client_id} not found")
            
            # Parse dates
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
            
            # Generate insights for each period
            insights = {}
            periods = ['6_months', '1_year', 'till_date']
            
            for period in periods:
                # Get period data (this should already be generated)
                period_performance = EnhancedReviewService._analyze_performance_for_period(
                    client, start_dt, end_dt
                )
                
                # Generate AI insights
                performance_insights = AIInsightsService.get_performance_insights(period_performance)
                risk_insights = AIInsightsService.get_risk_insights(period_performance.get('risk_metrics', {}))
                
                insights[period] = {
                    'performance_insights': performance_insights,
                    'risk_insights': risk_insights
                }
            
            # Generate comparison insights
            comparison_insights = AIInsightsService.get_period_comparison_insights({
                '6_months': insights.get('6_months', {}),
                '1_year': insights.get('1_year', {}),
                'till_date': insights.get('till_date', {})
            })
            
            # Generate recommendations
            if '1_year' in insights:
                recommendations = AIInsightsService.get_actionable_recommendations(
                    insights['1_year'].get('performance_insights', {}),
                    insights['1_year'].get('risk_insights', {})
                )
            else:
                recommendations = []
            
            return {
                'period_insights': insights,
                'comparison_insights': comparison_insights,
                'recommendations': recommendations,
                'client_id': client_id,
                'generated_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error generating AI insights: {str(e)}")
            raise
    
    @staticmethod
    def _generate_excel_calculation_file(review_data):
        """Generate Excel calculation file"""
        if not PANDAS_AVAILABLE:
            # Fallback to JSON if pandas not available
            return BytesIO(json.dumps(review_data['calculation_details'], indent=2).encode())
        
        output = BytesIO()
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # Client Info Sheet
            client_info = pd.DataFrame([
                ['Client ID', review_data['client_id']],
                ['Client Name', review_data['client_name']],
                ['Review Period', f"{review_data['start_date']} to {review_data['end_date']}"],
                ['Generated At', review_data['generated_at']]
            ], columns=['Field', 'Value'])
            client_info.to_excel(writer, sheet_name='Client Info', index=False)
            
            # Performance Calculations Sheet
            if 'performance' in review_data['sections']:
                perf_data = review_data['sections']['performance']
                perf_calc = pd.DataFrame([
                    ['Start Value', perf_data.get('start_value', 0)],
                    ['End Value', perf_data.get('end_value', 0)],
                    ['Total Return', perf_data.get('total_return', 0)],
                    ['Total Return %', perf_data.get('total_return_percent', 0)],
                    ['Annualized Return', perf_data.get('annualized_return', 0)],
                    ['XIRR', perf_data.get('xirr', 0)],
                    ['Benchmark Return', perf_data.get('benchmark_return', 0)],
                    ['Excess Return', perf_data.get('excess_return', 0)]
                ], columns=['Metric', 'Value'])
                perf_calc.to_excel(writer, sheet_name='Performance', index=False)
            
            # Analysis Classification Sheet
            if 'analysis_classification' in review_data:
                positive_signs = review_data['analysis_classification'].get('performance', {}).get('positive_signs', [])
                negative_signs = review_data['analysis_classification'].get('performance', {}).get('negative_signs', [])
                
                # Positive signs
                if positive_signs:
                    pos_df = pd.DataFrame(positive_signs)
                    pos_df.to_excel(writer, sheet_name='Positive Signs', index=False)
                
                # Negative signs
                if negative_signs:
                    neg_df = pd.DataFrame(negative_signs)
                    neg_df.to_excel(writer, sheet_name='Negative Signs', index=False)
            
            # Feedback Areas Sheet
            if 'feedback_areas' in review_data and review_data['feedback_areas'].get('areas'):
                feedback_df = pd.DataFrame(review_data['feedback_areas']['areas'])
                feedback_df.to_excel(writer, sheet_name='Feedback Areas', index=False)
            
            # Calculation Details Sheet
            if 'calculation_details' in review_data:
                calc_details = review_data['calculation_details']
                
                # Methodology
                methodology_data = []
                for key, value in calc_details.get('methodology', {}).items():
                    methodology_data.append([key, value])
                
                if methodology_data:
                    methodology_df = pd.DataFrame(methodology_data, columns=['Method', 'Description'])
                    methodology_df.to_excel(writer, sheet_name='Methodology', index=False)
        
        output.seek(0)
        return output
    
    @staticmethod
    def _generate_csv_calculation_file(review_data):
        """Generate CSV calculation file"""
        # Implementation for CSV format
        pass
    
    @staticmethod
    def _generate_json_calculation_file(review_data):
        """Generate JSON calculation file"""
        return json.dumps(review_data['calculation_details'], indent=2)
