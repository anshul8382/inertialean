"""
Review Routes
Flask routes for review generation and management
"""
from flask import Blueprint, request, jsonify, render_template, flash, redirect, url_for
from flask_login import login_required, current_user
from models import Client, User, Review, ReviewSection, ReviewShare
from review_service import ReviewService
from market_commentary_service import MarketCommentaryService
from extensions import db
from datetime import datetime, date, timedelta
import requests
import json
import logging
from decimal import Decimal
from models import Transaction, Cashflow
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import os

logger = logging.getLogger(__name__)

review_bp = Blueprint('review', __name__)

@review_bp.route('/reviews')
@login_required
def review_list():
    """Legacy list URL: retired; use Period Analysis V2 (Maintenance) or direct review links."""
    from flask import current_app

    if current_app.config.get('NAV_ENHANCED_REVIEW_ENABLED'):
        flash('The all-reviews list is retired. Use Period Analysis (V2) under Maintenance.', 'info')
        return redirect(url_for('enhanced_review.period_analysis_v2'))
    flash('The all-reviews list is retired.', 'info')
    return redirect(url_for('main.dashboard'))

@review_bp.route('/reviews/debug/<int:client_id>')
@login_required
def debug_review_generation(client_id):
    """Debug review generation with step-by-step logging"""
    try:
        from datetime import datetime, timedelta
        
        # Get client
        client = Client.query.get(client_id)
        if not client:
            return jsonify({'error': f'Client {client_id} not found'}), 404
        
        # Set default dates (last 6 months)
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=180)
        
        logger.info(f"=== DEBUG REVIEW GENERATION ===")
        logger.info(f"Client: {client.name} (ID: {client_id})")
        logger.info(f"Date range: {start_date} to {end_date}")
        
        # Generate review with detailed logging
        review_data = ReviewService.generate_client_review(
            client_id, 
            start_date.strftime('%Y-%m-%d'), 
            end_date.strftime('%Y-%m-%d'), 
            'comprehensive'
        )
        
        return jsonify({
            'success': True,
            'client': client.name,
            'date_range': f"{start_date} to {end_date}",
            'review_data': review_data,
            'debug_log': 'Check server logs for detailed step-by-step information'
        })
        
    except Exception as e:
        logger.error(f"Debug review generation error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'debug_log': 'Check server logs for detailed error information'
        }), 500

@review_bp.route('/reviews/generate-enhanced', methods=['GET'])
@login_required
def generate_review_enhanced():
    """Deprecated legacy route redirected to the active Period Analysis V2 flow."""
    return redirect(url_for('enhanced_review.period_analysis_v2'))

@review_bp.route('/api/reviews/<int:review_id>/pathway', methods=['GET'])
@login_required
def get_review_pathway(review_id):
    """Get pathway analysis for a review showing value breakdown"""
    try:
        review = Review.query.get_or_404(review_id)
        
        # Check access
        from access_control import get_accessible_clients
        accessible_clients = get_accessible_clients()
        if not any(c.id == review.client_id for c in accessible_clients):
            return jsonify({'error': 'Access denied'}), 403
        
        # Calculate pathway using period analysis
        from api.v1.period_analysis import calculate_pathway_breakdown
        try:
            pathway_data = calculate_pathway_breakdown(
                review.client_id,
                review.start_date,
                review.end_date
            )
        except Exception as e:
            logger.error(f"Error in pathway calculation: {str(e)}")
            return jsonify({'error': f'Pathway calculation failed: {str(e)}'}), 500
        
        return jsonify({
            'success': True,
            'pathway': pathway_data
        })
        
    except Exception as e:
        logger.error(f"Error calculating pathway: {str(e)}")
        return jsonify({'error': str(e)}), 500

@review_bp.route('/reviews/<int:review_id>')
@login_required
def review_detail(review_id):
    """View review details"""
    try:
        review = Review.query.get_or_404(review_id)
        
        # Check access
        from access_control import get_accessible_clients
        accessible_clients = get_accessible_clients()
        if not any(c.id == review.client_id for c in accessible_clients):
            flash('Access denied to this review', 'error')
            return redirect(url_for('enhanced_review.period_analysis_v2'))
        
        # Get client and calculate performance data (same as client_details route)
        client = review.client
        
        # Calculate current portfolio value and holdings
        current_value = Decimal('0.0')
        holdings_data = {}
        
        for holding in client.holdings:
            # Skip holdings with zero or negative quantity
            if not holding.quantity or holding.quantity <= 0:
                continue
                
            if holding.security and holding.security.current_price is not None:
                quantity = Decimal(str(holding.quantity)) if holding.quantity else Decimal('0.0')
                current_price = Decimal(str(holding.security.current_price)) if holding.security.current_price else Decimal('0.0')
                value = quantity * current_price
                current_value += value
                
                # Calculate unrealized P&L using Decimal
                avg_buy_price_decimal = Decimal(str(holding.average_price)) if holding.average_price else Decimal('0.0')
                unrealized_pnl = value - (quantity * avg_buy_price_decimal)
                unrealized_pnl_percent = ((current_price - avg_buy_price_decimal) / avg_buy_price_decimal * Decimal('100')) if avg_buy_price_decimal > 0 else Decimal('0.0')
                
                holdings_data[holding.security.id] = {
                    'symbol': holding.security.symbol,
                    'name': holding.security.name,
                    'quantity': float(quantity),
                    'current_price': float(current_price),
                    'value': float(value),
                    'avg_buy_price': float(avg_buy_price_decimal),
                    'unrealized_pnl': float(unrealized_pnl),
                    'unrealized_pnl_percent': float(unrealized_pnl_percent),
                    'asset_class': holding.security.asset_class.name if holding.security.asset_class else 'Unknown'
                }
        
        # Calculate total invested and withdrawn from transactions
        transactions = Transaction.query.filter_by(client_id=client.id).all()
        gross_invested = Decimal('0.0')
        gross_withdrawn = Decimal('0.0')
        
        for transaction in transactions:
            if transaction.type == 'BUY':
                gross_invested += Decimal(str(transaction.amount))
            elif transaction.type == 'SELL':
                gross_withdrawn += Decimal(str(transaction.amount))
        
        # Total invested should be the net cashflow (what the investor actually put in)
        total_invested = gross_invested - gross_withdrawn
        
        # Get all cashflows for XIRR calculation
        cashflows = Cashflow.query.filter_by(client_id=client.id).order_by(Cashflow.date).all()
        
        # Group cashflows by date (ignoring time)
        daily_cashflows = {}
        for cf in cashflows:
            date_key = datetime.combine(cf.date.date(), datetime.min.time())
            if date_key not in daily_cashflows:
                daily_cashflows[date_key] = Decimal('0.0')
            daily_cashflows[date_key] += cf.amount
        
        # Calculate XIRR using netted daily cashflows and current value
        cashflow_data = [(date, float(amount)) for date, amount in daily_cashflows.items()]
        current_value_float = float(current_value) if isinstance(current_value, Decimal) else current_value
        
        # Define XIRR calculation functions locally to avoid import issues
        def calculate_xirr(cashflows, current_value):
            """
            Calculate XIRR (Internal Rate of Return) for a series of cashflows and current value.
            """
            if not cashflows:
                return 0.0, 0.0, 0.0, 0.0, 0.0
            
            # Initialize variables
            total_invested = Decimal('0.0')
            total_withdrawn = Decimal('0.0')
            
            # Sort cashflows by date
            cashflows = sorted(cashflows, key=lambda x: x[0])
            
            # Calculate total invested and withdrawn
            for date, amount in cashflows:
                if isinstance(amount, (int, float)):
                    amount = Decimal(str(amount))
                if amount < 0:  # Negative amount = INVESTMENT
                    total_invested += abs(amount)
                else:  # Positive amount = WITHDRAWAL
                    total_withdrawn += amount
            
            net_investment = total_invested - total_withdrawn
            
            # Add current value as final cashflow for XIRR calculation
            current_date = datetime.now()
            current_value_float = float(current_value) if isinstance(current_value, Decimal) else current_value
            cashflows_with_current = cashflows + [(current_date, current_value_float)]
            
            # Convert dates to years from first cashflow
            first_date = cashflows_with_current[0][0]
            years = [(cf[0] - first_date).days / 365.0 for cf in cashflows_with_current]
            amounts = [float(cf[1]) if isinstance(cf[1], Decimal) else cf[1] for cf in cashflows_with_current]
            
            def xnpv(rate):
                try:
                    npv = sum([amount / (1 + rate) ** year for amount, year in zip(amounts, years)])
                    return npv
                except (OverflowError, ZeroDivisionError):
                    return float('inf')
            
            try:
                from scipy import optimize
                bounds = (-0.99, 10.0)  # Allow up to 1000% return for high-return investments
                test_rates = [-0.2, -0.1, 0.0, 0.1, 0.2]
                best_npv = float('inf')
                best_rate = 0.0
                
                for rate in test_rates:
                    npv = abs(xnpv(rate))
                    if npv < best_npv:
                        best_npv = npv
                        best_rate = rate
                
                result = optimize.minimize_scalar(
                    lambda r: abs(xnpv(r)),
                    bounds=bounds,
                    method='bounded'
                )
                
                xirr = result.x if result.success else 0.0
                
            except ImportError:
                xirr = 0.0
            except Exception:
                xirr = 0.0
            
            # Calculate absolute return
            net_investment_float = float(net_investment)
            current_value_float = float(current_value) if isinstance(current_value, Decimal) else current_value
            absolute_return = ((current_value_float - net_investment_float) / net_investment_float * 100) if net_investment_float > 0 else 0
            
            return xirr, float(total_invested), float(total_withdrawn), float(net_investment), absolute_return
        
        def calculate_nifty_xirr(client_id):
            """
            Calculate XIRR assuming client cashflows were invested in Nifty benchmark.
            """
            try:
                from models import BenchmarkData, Benchmark
                
                # Get client cashflows
                cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
                if not cashflows:
                    return 0.0, 0.0, 0.0
                
                # Get Nifty benchmark data
                benchmark = Benchmark.query.filter_by(id=1).first()  # NIFTY 50
                if not benchmark:
                    return 0.0, 0.0, 0.0
                
                # Get benchmark data for the date range
                # Convert all dates to date objects before comparison to avoid datetime/date comparison errors
                date_list = [cf.date.date() if isinstance(cf.date, datetime) else cf.date for cf in cashflows]
                min_date = min(date_list)
                max_date = max(date_list)
                
                benchmark_data = BenchmarkData.query.filter_by(benchmark_id=benchmark.id)\
                    .filter(BenchmarkData.date >= min_date)\
                    .filter(BenchmarkData.date <= max_date)\
                    .order_by(BenchmarkData.date).all()
                
                if not benchmark_data:
                    return 0.0, 0.0, 0.0
                
                # Create a price lookup dictionary
                price_lookup = {bd.date: float(bd.price) for bd in benchmark_data}
                
                # Get the latest benchmark price
                latest_benchmark = BenchmarkData.query.filter_by(benchmark_id=benchmark.id)\
                    .order_by(BenchmarkData.date.desc()).first()
                
                if not latest_benchmark:
                    return 0.0, 0.0, 0.0
                
                latest_price = float(latest_benchmark.price)
                
                # Simulate Nifty investment
                nifty_units = 0.0
                total_invested = 0.0
                total_withdrawn = 0.0
                weighted_total_cost = 0.0
                
                for cf in cashflows:
                    cf_date = cf.date
                    cf_amount = float(cf.amount)
                    
                    cf_date = cf_date.date() if hasattr(cf_date, 'date') else cf_date
                    
                    benchmark_price = None
                    if cf_date in price_lookup:
                        benchmark_price = price_lookup[cf_date]
                    else:
                        available_dates = [d for d in price_lookup.keys() if d <= cf_date]
                        if available_dates:
                            closest_date = max(available_dates)
                            benchmark_price = price_lookup[closest_date]
                    
                    if benchmark_price is None or benchmark_price <= 0:
                        continue
                    
                    if cf_amount < 0:  # Investment
                        units_bought = abs(cf_amount) / benchmark_price
                        nifty_units += units_bought
                        total_invested += abs(cf_amount)
                        weighted_total_cost += abs(cf_amount)
                    else:  # Withdrawal
                        if nifty_units > 0:
                            units_sold = min(cf_amount / benchmark_price, nifty_units)
                            nifty_units -= units_sold
                            total_withdrawn += cf_amount
                            if nifty_units > 0:
                                reduction_ratio = units_sold / (nifty_units + units_sold)
                                weighted_total_cost -= weighted_total_cost * reduction_ratio
                
                # Calculate current value of Nifty investment
                nifty_current_value = nifty_units * latest_price
                
                # Calculate weighted average Nifty price
                weighted_avg_nifty_price = weighted_total_cost / nifty_units if nifty_units > 0 else 0.0
                
                # Calculate absolute return
                if weighted_avg_nifty_price > 0 and nifty_units > 0:
                    nifty_absolute_return = ((latest_price - weighted_avg_nifty_price) / weighted_avg_nifty_price * 100)
                else:
                    nifty_absolute_return = 0.0
                
                # Calculate XIRR for Nifty investment
                cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
                nifty_xirr, _, _, _, _ = calculate_xirr(cashflow_data, nifty_current_value)
                
                return nifty_xirr, nifty_current_value, nifty_absolute_return
                
            except Exception as e:
                logger.error(f"Error calculating Nifty XIRR: {str(e)}")
                return 0.0, 0.0, 0.0
        
        # Calculate XIRR
        xirr, xirr_invested, xirr_withdrawn, xirr_net, absolute_return = calculate_xirr(cashflow_data, current_value_float)
        
        # Calculate Nifty XIRR
        nifty_xirr, nifty_current_value, nifty_absolute_return = calculate_nifty_xirr(client.id)
        
        # Get client's holdings for the template
        valid_holdings = [h for h in client.holdings if h.quantity and h.quantity > 0 and h.security and h.security.current_price]
        holdings = valid_holdings
        
        # Use the stored review data if available, otherwise calculate
        if review.review_data and 'sections' in review.review_data and 'performance' in review.review_data['sections']:
            # Use the stored performance data from the review service
            performance_data = review.review_data['sections']['performance']
            logger.info(f"Using stored review performance data: {performance_data}")
        else:
            # Fallback to calculated data
            performance_data = {
                'start_value': float(total_invested),  # This should be the value at review start date
                'end_value': float(current_value),
                'total_return_percent': float(absolute_return) if absolute_return else 0.0,
                'xirr': float(xirr * 100) if xirr else None,
                'benchmark_return': float(nifty_absolute_return) if nifty_absolute_return else None,
                'excess_return': float(absolute_return - nifty_absolute_return) if absolute_return and nifty_absolute_return else None
            }
            logger.info(f"Using calculated performance data: {performance_data}")
        
        # Create a mock review_data structure that matches what the template expects
        review.review_data = {
            'sections': {
                'performance': performance_data,
                'allocation': {
                    'current_allocation': holdings_data,
                    'drift_analysis': {}  # This would be calculated for the review period
                },
                'securities': {
                    'best_performers': sorted(holdings_data.values(), key=lambda x: x['unrealized_pnl_percent'], reverse=True)[:5],
                    'worst_performers': sorted(holdings_data.values(), key=lambda x: x['unrealized_pnl_percent'])[:5]
                },
                'recommendations': []  # This would be generated based on analysis
            }
        }
        
        # Extract performance data for template variables
        current_value = performance_data.get('current_value', current_value)
        total_invested = performance_data.get('net_investment', total_invested)
        absolute_return = performance_data.get('overall_return_percent', absolute_return)
        xirr = performance_data.get('xirr', xirr)
        
        # Extract review period data
        start_value = performance_data.get('start_value', 0)
        end_value = performance_data.get('end_value', current_value)
        review_period_return_percent = performance_data.get('review_period_return_percent', 0)
        benchmark_return_review_period = performance_data.get('benchmark_return_review_period', None)
        benchmark_return_overall = performance_data.get('benchmark_return_overall', None)
        excess_return_review_period = performance_data.get('excess_return_review_period', None)
        excess_return_overall = performance_data.get('excess_return_overall', None)
        
        logger.info(f"Template variables - current_value: {current_value}, total_invested: {total_invested}, absolute_return: {absolute_return}")
        
        # Calculate pathway data for visualization
        pathway_data = None
        try:
            from api.v1.period_analysis import calculate_pathway_breakdown
            logger.info(f"Calculating pathway data for client {client.id}, period {review.start_date} to {review.end_date}")
            pathway_data = calculate_pathway_breakdown(
                client.id,
                review.start_date,
                review.end_date
            )
            if pathway_data:
                logger.info(f"Pathway data calculated successfully: start={pathway_data.get('start_value')}, end={pathway_data.get('end_value')}")
            else:
                logger.warning("Pathway calculation returned None")
        except ImportError as e:
            logger.error(f"Import error in pathway calculation: {str(e)}", exc_info=True)
        except Exception as e:
            logger.error(f"Could not calculate pathway data: {str(e)}", exc_info=True)
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
        
        return render_template('reviews/view.html', 
                             review=review,
                             current_value=current_value,
                             total_invested=total_invested,
                             xirr=xirr,
                             absolute_return=absolute_return,
                             nifty_xirr=nifty_xirr,
                             nifty_current_value=nifty_current_value,
                             nifty_absolute_return=nifty_absolute_return,
                             holdings=holdings,
                             holdings_data=holdings_data,
                             # New variables for the updated template
                             start_value=start_value,
                             end_value=end_value,
                             review_period_return_percent=review_period_return_percent,
                             benchmark_return_review_period=benchmark_return_review_period,
                             benchmark_return_overall=benchmark_return_overall,
                             excess_return_review_period=excess_return_review_period,
                             excess_return_overall=excess_return_overall,
                             net_investment=total_invested,
                             overall_return_percent=absolute_return,
                             pathway_data=pathway_data)
        
    except Exception as e:
        logger.error(f"Error viewing review: {str(e)}")
        flash(f'Error loading review: {str(e)}', 'error')
        return redirect(url_for('enhanced_review.period_analysis_v2'))

@review_bp.route('/reviews/<int:review_id>/share', methods=['GET', 'POST'])
@login_required
def share_review(review_id):
    """Share review with client"""
    try:
        review = Review.query.get_or_404(review_id)
        
        # Check access
        from access_control import get_accessible_clients
        accessible_clients = get_accessible_clients()
        if not any(c.id == review.client_id for c in accessible_clients):
            flash('Access denied to this review', 'error')
            return redirect(url_for('enhanced_review.period_analysis_v2'))
        
        if request.method == 'GET':
            return render_template('reviews/share.html', review=review)
        
        elif request.method == 'POST':
            delivery_method = request.form.get('delivery_method', 'email')
            
            # Create share record
            share = ReviewShare(
                review_id=review.id,
                shared_by=current_user.id,
                delivery_method=delivery_method,
                delivery_status='pending'
            )
            
            db.session.add(share)
            db.session.commit()
            
            # Process sharing
            try:
                review_service = ReviewService()
                review_service.share_review_async(share.id)
                flash('Review shared successfully', 'success')
            except Exception as e:
                logger.error(f"Error sharing review: {str(e)}")
                share.delivery_status = 'failed'
                db.session.commit()
                flash('Error sharing review', 'error')
            
            return redirect(url_for('review.review_detail', review_id=review.id))
            
    except Exception as e:
        logger.error(f"Error sharing review: {str(e)}")
        flash(f'Error sharing review: {str(e)}', 'error')
        return redirect(url_for('enhanced_review.period_analysis_v2'))

@review_bp.route('/reviews/<int:review_id>/market-commentary')
@login_required
def market_commentary(review_id):
    """View and edit market commentary for a review"""
    try:
        review = Review.query.get_or_404(review_id)
        
        # Check access permissions
        if not current_user.has_role('admin') and review.user_id != current_user.id:
            flash('Access denied', 'error')
            return redirect(url_for('enhanced_review.period_analysis_v2'))
        
        # Get market commentary
        commentary_service = MarketCommentaryService()
        commentary = commentary_service.generate_market_commentary(review_id)
        
        return render_template('reviews/market_commentary.html', 
                             review=review, 
                             commentary=commentary)
        
    except Exception as e:
        logger.error(f"Error loading market commentary: {str(e)}")
        flash('Error loading market commentary', 'error')
        return redirect(url_for('enhanced_review.period_analysis_v2'))

@review_bp.route('/api/reviews/<int:review_id>/market-commentary', methods=['POST'])
@login_required
def update_market_commentary(review_id):
    """Update market commentary with custom insights"""
    try:
        review = Review.query.get_or_404(review_id)
        
        # Check access permissions
        if not current_user.has_role('admin') and review.user_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        data = request.get_json()
        custom_insights = data.get('custom_insights', '')
        
        # Generate updated commentary with custom insights
        commentary_service = MarketCommentaryService()
        commentary = commentary_service.generate_market_commentary(review_id, custom_insights)
        
        # Update review data with market commentary
        review_data = review.review_data or {}
        review_data['market_commentary'] = commentary
        review.review_data = review_data
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Market commentary updated successfully',
            'commentary': commentary
        })
        
    except Exception as e:
        logger.error(f"Error updating market commentary: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@review_bp.route('/api/reviews/<int:review_id>/generate-commentary', methods=['POST'])
@login_required
def generate_market_commentary(review_id):
    """Generate fresh market commentary"""
    try:
        review = Review.query.get_or_404(review_id)
        
        # Check access permissions
        if not current_user.has_role('admin') and review.user_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        # Generate new commentary
        commentary_service = MarketCommentaryService()
        commentary = commentary_service.generate_market_commentary(review_id)
        
        if commentary:
            return jsonify({
                'success': True,
                'commentary': commentary
            })
        else:
            return jsonify({'error': 'Failed to generate market commentary'}), 500
        
    except Exception as e:
        logger.error(f"Error generating market commentary: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@review_bp.route('/api/reviews/<int:review_id>/status')
@login_required
def review_status(review_id):
    """Get review generation status"""
    try:
        review = Review.query.get_or_404(review_id)
        
        # Check access
        from access_control import get_accessible_clients
        accessible_clients = get_accessible_clients()
        if not any(c.id == review.client_id for c in accessible_clients):
            return jsonify({'error': 'Access denied'}), 403
        
        return jsonify({
            'id': review.id,
            'status': review.status,
            'generated_at': review.generated_at.isoformat() if review.generated_at else None,
            'completed_at': review.completed_at.isoformat() if review.completed_at else None,
            'error_message': review.error_message
        })
        
    except Exception as e:
        logger.error(f"Error getting review status: {str(e)}")
        return jsonify({'error': str(e)}), 500

@review_bp.route('/reviews/<int:review_id>/save', methods=['POST'])
@login_required
def save_review(review_id):
    """Save edited review content"""
    try:
        review = Review.query.get_or_404(review_id)
        
        # Check access
        from access_control import get_accessible_clients
        accessible_clients = get_accessible_clients()
        if not any(c.id == review.client_id for c in accessible_clients):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Get JSON data
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        # Update review data
        if not review.review_data:
            review.review_data = {}
        
        if 'sections' not in review.review_data:
            review.review_data['sections'] = {}
        
        # Save edited sections
        if 'sections' in data:
            for field_name, content in data['sections'].items():
                review.review_data['sections'][field_name] = content
        
        # Save hidden sections info
        if 'hidden_sections' in data:
            review.review_data['hidden_sections'] = data['hidden_sections']
        
        # Mark as edited
        review.review_data['last_edited'] = datetime.now().isoformat()
        review.review_data['edited_by'] = current_user.id
        
        # Save to database
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Review saved successfully'})
        
    except Exception as e:
        logger.error(f"Error saving review: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@review_bp.route('/reviews/<int:review_id>/email', methods=['POST'])
@login_required
def email_review(review_id):
    """Email review to anshul@equities4wealth.com"""
    try:
        review = Review.query.get_or_404(review_id)
        
        # Check access
        from access_control import get_accessible_clients
        accessible_clients = get_accessible_clients()
        if not any(c.id == review.client_id for c in accessible_clients):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Get JSON data
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        html_content = data.get('html_content', '')
        
        # Create email content
        subject = f"Client Review: {review.client.name} - {review.start_date.strftime('%B %Y')} to {review.end_date.strftime('%B %Y')}"
        
        # Create rich HTML email
        email_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>{subject}</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 1200px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px;
                    border-radius: 10px;
                    margin-bottom: 30px;
                    text-align: center;
                }}
                .review-section {{
                    margin-bottom: 30px;
                    padding: 20px;
                    border-radius: 8px;
                    background: #fff;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    border-left: 4px solid #007bff;
                }}
                .metric-card {{
                    text-align: center;
                    padding: 15px;
                    border-radius: 8px;
                    background: #f8f9fa;
                    border: 1px solid #dee2e6;
                    margin: 10px 0;
                }}
                .metric-value {{
                    font-size: 1.5rem;
                    font-weight: bold;
                    margin-bottom: 5px;
                }}
                .metric-label {{
                    font-size: 0.875rem;
                    color: #6c757d;
                }}
                .text-success {{ color: #28a745; }}
                .text-danger {{ color: #dc3545; }}
                .text-warning {{ color: #ffc107; }}
                .text-info {{ color: #17a2b8; }}
                .insight-card {{
                    border-left: 4px solid #007bff;
                    padding-left: 15px;
                    margin: 15px 0;
                    background: #f8f9fa;
                    border-radius: 4px;
                }}
                .market-insight {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 20px;
                    border-radius: 8px;
                    margin: 15px 0;
                }}
                .benchmark-comparison {{
                    background: #e3f2fd;
                    padding: 15px;
                    border-radius: 6px;
                    margin: 15px 0;
                }}
                .data-table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin: 15px 0;
                }}
                .data-table th, .data-table td {{
                    border: 1px solid #dee2e6;
                    padding: 8px;
                    text-align: left;
                }}
                .data-table th {{
                    background: #f8f9fa;
                    font-weight: 600;
                }}
                .footer {{
                    margin-top: 40px;
                    padding: 20px;
                    background: #f8f9fa;
                    border-radius: 8px;
                    text-align: center;
                    color: #6c757d;
                }}
                h1, h2, h3, h4, h5, h6 {{
                    color: #495057;
                }}
                .fas {{
                    margin-right: 8px;
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>Comprehensive Client Review</h1>
                <h2>{review.client.name}</h2>
                <p>Review Period: {review.start_date.strftime('%B %d, %Y')} - {review.end_date.strftime('%B %d, %Y')}</p>
                <p>Generated on: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
            </div>
            
            {html_content}
            
            <div class="footer">
                <p><strong>Generated by Equities4Wealth Portfolio Management System</strong></p>
                <p>This review was automatically generated and sent to anshul@equities4wealth.com</p>
                <p>For any questions or concerns, please contact the portfolio management team.</p>
            </div>
        </body>
        </html>
        """
        
        # Send email
        success = send_email_to_anshul(subject, email_html)
        
        if success:
            # Log the email sending
            review.review_data = review.review_data or {}
            review.review_data['emailed_at'] = datetime.now().isoformat()
            review.review_data['emailed_to'] = 'anshul@equities4wealth.com'
            db.session.commit()
            
            return jsonify({'success': True, 'message': 'Review sent successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to send email'}), 500
        
    except Exception as e:
        logger.error(f"Error emailing review: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

def send_email_to_anshul(subject, html_content):
    """Send email to anshul@equities4wealth.com. Uses MAIL_* env vars (same as main app); falls back to SMTP_*."""
    try:
        # Use same env vars as main app (MAIL_*) so one .env works; fallback to SMTP_* for backwards compatibility
        sender_email = (os.getenv('MAIL_USERNAME') or os.getenv('SMTP_USERNAME') or 'noreply@equities4wealth.com').strip()
        raw_pwd = os.getenv('MAIL_PASSWORD') or os.getenv('SMTP_PASSWORD') or ''
        sender_password = str(raw_pwd).strip().strip('"\'').replace(' ', '').replace('\r', '').replace('\n', '')
        smtp_server = os.getenv('MAIL_SERVER') or os.getenv('SMTP_SERVER') or 'smtp.gmail.com'
        smtp_port = int(os.getenv('MAIL_PORT') or os.getenv('SMTP_PORT') or '587')
        
        # Create message
        msg = MIMEMultipart('alternative')
        msg['From'] = sender_email
        msg['To'] = 'anshul@equities4wealth.com'
        msg['Subject'] = subject
        
        # Attach HTML content
        html_part = MIMEText(html_content, 'html')
        msg.attach(html_part)
        
        # Send email
        if not sender_password:
            logger.error("send_email_to_anshul: MAIL_PASSWORD (or SMTP_PASSWORD) not set in .env")
            return False
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        text = msg.as_string()
        server.sendmail(sender_email, 'anshul@equities4wealth.com', text)
        server.quit()
        
        logger.info(f"Review email sent successfully to anshul@equities4wealth.com")
        return True
        
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}")
        return False

@review_bp.route('/reviews/portfolio-construction')
@login_required
def portfolio_construction():
    """Portfolio construction utility"""
    try:
        # Get accessible clients
        from access_control import get_accessible_clients
        clients = get_accessible_clients()
        
        return render_template('reviews/portfolio_construction.html', clients=clients)
        
    except Exception as e:
        logger.error(f"Error loading portfolio construction: {str(e)}")
        flash(f'Error loading portfolio construction: {str(e)}', 'error')
        return redirect(url_for('enhanced_review.period_analysis_v2'))

@review_bp.route('/reviews/position-construction')
@login_required
def position_construction():
    """Position construction utility - shows forward calculation log"""
    try:
        # Show all clients to all users (similar to /clients route)
        # This is a utility tool that should be accessible to all logged-in users
        from access_control import get_accessible_clients_ordered
        clients = get_accessible_clients_ordered()
        
        # Get all securities
        from models import Security
        securities = Security.query.order_by(Security.symbol).all()
        
        return render_template('reviews/position_construction.html', clients=clients, securities=securities)
        
    except Exception as e:
        logger.error(f"Error loading position construction: {str(e)}")
        flash(f'Error loading position construction: {str(e)}', 'error')
        return redirect(url_for('enhanced_review.period_analysis_v2'))

@review_bp.route('/reviews/performance-timeline')
@login_required
def performance_timeline_test():
    """Moved to Intelligence Hub → Investment (/hub/investment)."""
    return redirect(url_for('hub.investment_dashboard'))

@review_bp.route('/api/portfolio-construction/<int:client_id>')
@login_required
def api_portfolio_construction(client_id):
    """API endpoint for portfolio construction using forward calculation"""
    try:
        # Check access
        from access_control import get_accessible_clients
        accessible_clients = get_accessible_clients()
        if not any(c.id == client_id for c in accessible_clients):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        
        # Get as_of_date from query parameters
        as_of_date_str = request.args.get('as_of_date')
        if not as_of_date_str:
            return jsonify({'success': False, 'error': 'as_of_date parameter is required'}), 400
        
        try:
            as_of_date = datetime.strptime(as_of_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        logger.info(f"Portfolio construction request: Client {client_id}, Date {as_of_date}")
        
        # Use forward calculation service for accurate portfolio construction
        from services.forward_holding_calculation_service import get_client_portfolio_by_date
        
        portfolio = get_client_portfolio_by_date(client_id, as_of_date)
        
        # Format response
        return jsonify({
            'success': True,
            'client_id': client_id,
            'as_of_date': as_of_date_str,
            'portfolio': portfolio
        })
        
    except Exception as e:
        logger.error(f"Error in portfolio construction API: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@review_bp.route('/api/position-construction/<int:client_id>/<int:security_id>')
@login_required
def api_position_construction(client_id, security_id):
    """API endpoint for position construction with forward calculation log"""
    try:
        # Verify client exists (all logged-in users can access this utility)
        client = Client.query.get_or_404(client_id)
        
        # Get as_of_date from query parameters
        as_of_date_str = request.args.get('as_of_date')
        if not as_of_date_str:
            return jsonify({'success': False, 'error': 'as_of_date parameter is required'}), 400
        
        try:
            as_of_date = datetime.strptime(as_of_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        logger.info(f"Position construction request: Client {client_id}, Security {security_id}, Date {as_of_date}")
        
        # Use forward calculation service with steps - this ensures both use the same codebase
        from services.forward_holding_calculation_service import get_holding_quantity_by_date_with_steps
        from models import Security
        
        # Get security info
        security = Security.query.get_or_404(security_id)
        
        # Get result with detailed steps from forward calculation service
        try:
            result = get_holding_quantity_by_date_with_steps(client_id, security_id, as_of_date)
            
            # Ensure result has all required keys
            if not result:
                logger.warning(f"Unexpected result structure from get_holding_quantity_by_date_with_steps: {result}")
                result = {
                    'quantity': 0.0,
                    'average_price': 0.0,
                    'total_cost': 0.0,
                    'transactions_processed': 0,
                    'corporate_actions_applied': 0,
                    'steps': []
                }
            
            # Format response
            return jsonify({
                'success': True,
                'client_id': client_id,
                'security_id': security_id,
                'security_symbol': security.symbol,
                'as_of_date': as_of_date_str,
                'final_quantity': result.get('quantity', 0.0),
                'final_average_price': result.get('average_price', 0.0),
                'final_total_cost': result.get('total_cost', 0.0),
                'summary': {
                    'transactions_processed': result.get('transactions_processed', 0),
                    'corporate_actions_applied': result.get('corporate_actions_applied', 0)
                },
                'steps': result.get('steps', []),
                'position': {
                    'final_quantity': result.get('quantity', 0.0),
                    'final_average_price': result.get('average_price', 0.0),
                    'final_total_cost': result.get('total_cost', 0.0)
                }
            })
            
        except Exception as calc_error:
            logger.error(f"Error in get_holding_quantity_by_date_with_steps: {str(calc_error)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': str(calc_error),
                'client_id': client_id,
                'security_id': security_id,
                'security_symbol': security.symbol if 'security' in locals() else 'N/A',
                'as_of_date': as_of_date_str,
                'final_quantity': 0.0,
                'final_average_price': 0.0,
                'final_total_cost': 0.0,
                'summary': {
                    'transactions_processed': 0,
                    'corporate_actions_applied': 0
                },
                'steps': [],
                'position': {
                    'final_quantity': 0.0,
                    'final_average_price': 0.0,
                    'final_total_cost': 0.0
                }
            }), 500
        
    except Exception as e:
        logger.error(f"Error in position construction API: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'client_id': client_id,
            'security_id': security_id,
            'security_symbol': security.symbol if 'security' in locals() else 'N/A',
            'as_of_date': as_of_date_str if 'as_of_date_str' in locals() else '',
            'final_quantity': 0.0,
            'final_average_price': 0.0,
            'final_total_cost': 0.0,
            'summary': {
                'transactions_processed': 0,
                'corporate_actions_applied': 0
            },
            'steps': [],
            'position': {
                'final_quantity': 0.0,
                'final_average_price': 0.0,
                'final_total_cost': 0.0
            }
        }), 500
