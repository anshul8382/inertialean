"""
Review Routes
Flask routes for review generation and management
"""
from flask import Blueprint, request, jsonify, render_template, flash, redirect, url_for
from flask_login import login_required, current_user
from models import Client, User, Review, ReviewSection, ReviewShare, ReviewTemplate
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
    """List all reviews"""
    try:
        # Get reviews for accessible clients
        from access_control import get_accessible_clients
        accessible_clients = get_accessible_clients()
        client_ids = [c.id for c in accessible_clients]
        
        reviews = Review.query.filter(
            Review.client_id.in_(client_ids)
        ).order_by(Review.generated_at.desc()).all()
        
        return render_template('reviews/list.html', reviews=reviews)
        
    except Exception as e:
        logger.error(f"Error listing reviews: {str(e)}")
        flash(f'Error loading reviews: {str(e)}', 'error')
        return redirect(url_for('main.index'))

@review_bp.route('/reviews/generate', methods=['GET', 'POST'])
@login_required
def generate_review():
    """Generate a new review"""
    if request.method == 'GET':
        try:
            # Get accessible clients
            from access_control import get_accessible_clients
            clients = get_accessible_clients()
            
            # Get review templates
            templates = ReviewTemplate.query.filter_by(is_active=True).all()
            
            return render_template('reviews/generate.html', 
                                 clients=clients, 
                                 templates=templates)
                                 
        except Exception as e:
            logger.error(f"Error loading review generation page: {str(e)}")
            flash(f'Error loading page: {str(e)}', 'error')
            return redirect(url_for('review.review_list'))
    
    elif request.method == 'POST':
        try:
            # Get form data
            client_id = request.form.get('client_id', type=int)
            start_date = request.form.get('start_date')
            end_date = request.form.get('end_date')
            review_type = request.form.get('review_type', 'comprehensive')
            
            # Validate inputs
            if not client_id or not start_date or not end_date:
                flash('Please fill in all required fields', 'error')
                return redirect(url_for('review.generate_review'))
            
            # Parse dates
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
            
            # Validate date range
            if start_dt >= end_dt:
                flash('End date must be after start date', 'error')
                return redirect(url_for('review.generate_review'))
            
            # Check if client is accessible
            from access_control import get_accessible_clients
            accessible_clients = get_accessible_clients()
            if not any(c.id == client_id for c in accessible_clients):
                flash('Access denied to this client', 'error')
                return redirect(url_for('review.generate_review'))
            
            # Create review record
            review = Review(
                client_id=client_id,
                review_type=review_type,
                start_date=start_dt,
                end_date=end_dt,
                status='generating',
                requested_by=current_user.id
            )
            
            db.session.add(review)
            db.session.commit()
            
            # Start review generation in background
            try:
                review_service = ReviewService()
                review_service.generate_review_async(review.id)
                flash('Review generation started successfully', 'success')
            except Exception as e:
                logger.error(f"Error starting review generation: {str(e)}")
                review.status = 'failed'
                review.error_message = str(e)
                db.session.commit()
                flash('Error starting review generation', 'error')
            
            return redirect(url_for('review.review_list'))
            
        except Exception as e:
            logger.error(f"Error generating review: {str(e)}")
            flash(f'Error generating review: {str(e)}', 'error')
            return redirect(url_for('review.generate_review'))

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
            return redirect(url_for('review.review_list'))
        
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
        
        # Calculate performance data for the review period
        # For now, we'll use the overall performance data
        # In a real implementation, you'd calculate this for the specific review period
        performance_data = {
            'start_value': float(total_invested),  # This should be the value at review start date
            'end_value': float(current_value),
            'total_return_percent': float(absolute_return) if absolute_return else 0.0,
            'xirr': float(xirr * 100) if xirr else None,
            'benchmark_return': float(nifty_absolute_return) if nifty_absolute_return else None,
            'excess_return': float(absolute_return - nifty_absolute_return) if absolute_return and nifty_absolute_return else None
        }
        
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
                             holdings_data=holdings_data)
        
    except Exception as e:
        logger.error(f"Error viewing review: {str(e)}")
        flash(f'Error loading review: {str(e)}', 'error')
        return redirect(url_for('review.review_list'))

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
            return redirect(url_for('review.review_list'))
        
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
        return redirect(url_for('review.review_list'))

@review_bp.route('/reviews/<int:review_id>/market-commentary')
@login_required
def market_commentary(review_id):
    """View and edit market commentary for a review"""
    try:
        review = Review.query.get_or_404(review_id)
        
        # Check access permissions
        if not current_user.has_role('admin') and review.user_id != current_user.id:
            flash('Access denied', 'error')
            return redirect(url_for('review.review_list'))
        
        # Get market commentary
        commentary_service = MarketCommentaryService()
        commentary = commentary_service.generate_market_commentary(review_id)
        
        return render_template('reviews/market_commentary.html', 
                             review=review, 
                             commentary=commentary)
        
    except Exception as e:
        logger.error(f"Error loading market commentary: {str(e)}")
        flash('Error loading market commentary', 'error')
        return redirect(url_for('review.review_list'))

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
    """Send email to anshul@equities4wealth.com"""
    try:
        # Email configuration
        sender_email = os.getenv('SMTP_USERNAME', 'noreply@equities4wealth.com')
        sender_password = os.getenv('SMTP_PASSWORD', '')
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        
        # Create message
        msg = MIMEMultipart('alternative')
        msg['From'] = sender_email
        msg['To'] = 'anshul@equities4wealth.com'
        msg['Subject'] = subject
        
        # Attach HTML content
        html_part = MIMEText(html_content, 'html')
        msg.attach(html_part)
        
        # Send email
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
