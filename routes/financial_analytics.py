"""
Financial Analytics Routes
UI routes for financial analytics dashboard
"""

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from models import Client
from datetime import date, datetime, timedelta
from services.financial_analytics_service import FinancialAnalyticsService
import json
import logging

logger = logging.getLogger(__name__)

financial_analytics_bp = Blueprint('financial_analytics', __name__, url_prefix='/financial-analytics')


@financial_analytics_bp.before_request
def _enforce_financial_analytics_client_scope():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


@financial_analytics_bp.route('/')
@login_required
def dashboard():
    """Financial Analytics Dashboard"""
    from access_control import get_accessible_clients_ordered
    clients = get_accessible_clients_ordered()
    return render_template(
        'financial_analytics/dashboard.html',
        clients=clients
    )

@financial_analytics_bp.route('/client/<int:client_id>')
@login_required
def client_analytics(client_id):
    """Financial Analytics for a specific client"""
    client = Client.query.get_or_404(client_id)
    return render_template(
        'financial_analytics/dashboard.html',
        clients=[client],
        selected_client_id=client_id
    )

@financial_analytics_bp.route('/calculate', methods=['POST'])
@login_required
def calculate_metrics_ui():
    """Calculate metrics from UI form with validation"""
    try:
        client_id = request.form.get('client_id', type=int)
        if not client_id:
            flash('Please select a client', 'error')
            return redirect(url_for('financial_analytics.dashboard'))
        
        # Check if user wants to override validation
        override_validation = request.form.get('override_validation') == 'on'
        validate_before_calculate = request.form.get('validate_before_calculate') == 'on'
        
        # Build metrics config from form
        metrics_config = {}
        
        # Get all metric configurations
        available_metrics = [
            'beta', 'alpha', 'sharpe_ratio', 'treynor_ratio',
            'sortino_ratio', 'information_ratio', 'calmar_ratio',
            'portfolio_volatility', 'max_drawdown', 'var_95',
            'xirr', 'benchmark_comparison', 'upside_capture_ratio',
            'downside_capture_ratio', 'capture_ratio'
        ]
        
        for metric in available_metrics:
            enabled = request.form.get(f'metric_{metric}_enabled') == 'on'
            if enabled:
                period_type = request.form.get(f'metric_{metric}_period_type', 'days')
                
                metrics_config[metric] = {}
                
                if period_type == 'custom':
                    start_date_str = request.form.get(f'metric_{metric}_start_date')
                    end_date_str = request.form.get(f'metric_{metric}_end_date')
                    
                    if start_date_str:
                        try:
                            metrics_config[metric]['start_date'] = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                        except ValueError:
                            flash(f'Invalid start date for {metric}', 'error')
                            return redirect(url_for('financial_analytics.dashboard'))
                    
                    if end_date_str:
                        try:
                            metrics_config[metric]['end_date'] = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                        except ValueError:
                            flash(f'Invalid end date for {metric}', 'error')
                            return redirect(url_for('financial_analytics.dashboard'))
                else:
                    period_days = request.form.get(f'metric_{metric}_period_days', type=int)
                    if period_days:
                        metrics_config[metric]['period_days'] = period_days
        
        if not metrics_config:
            flash('Please select at least one metric to calculate', 'error')
            return redirect(url_for('financial_analytics.dashboard'))
        
        # Validate data first (optional - for information only)
        validation_result = None
        if validate_before_calculate:
            try:
                validation_result = FinancialAnalyticsService.validate_data_for_metrics(
                    client_id=client_id,
                    metrics_config=metrics_config,
                    strict_mode=False  # Use warnings, not errors
                )
            except Exception as e:
                logger.warning(f"Validation failed but proceeding: {str(e)}")
        
        # Always proceed with calculation (system uses fallbacks)
        results = FinancialAnalyticsService.calculate_multiple_metrics(
            client_id=client_id,
            metrics_config=metrics_config
        )
        
        # Add validation info to results if available
        if validation_result:
            results['validation_info'] = validation_result
        
        # Show validation page only if user explicitly wants to see it and there are issues
        if validate_before_calculate and validation_result and not override_validation:
            # Only show validation page if there are significant issues
            if validation_result.get('summary', {}).get('metrics_with_errors', 0) > 0:
                return render_template(
                    'financial_analytics/validation_results.html',
                    client_id=client_id,
                    validation_result=validation_result,
                    metrics_config=metrics_config
                )
        
        return render_template(
            'financial_analytics/results.html',
            client_id=client_id,
            results=results
        )
        
    except Exception as e:
        logger.error(f"Error calculating metrics: {str(e)}")
        flash(f'Error calculating metrics: {str(e)}', 'error')
        return redirect(url_for('financial_analytics.dashboard'))

@financial_analytics_bp.route('/validate', methods=['POST'])
@login_required
def validate_data_ui():
    """Validate data from UI form"""
    try:
        client_id = request.form.get('client_id', type=int)
        if not client_id:
            return jsonify({'success': False, 'error': 'Client ID required'}), 400
        
        # Build metrics config (same as calculate)
        metrics_config = {}
        available_metrics = [
            'beta', 'alpha', 'sharpe_ratio', 'treynor_ratio',
            'sortino_ratio', 'information_ratio', 'calmar_ratio',
            'portfolio_volatility', 'max_drawdown', 'var_95',
            'xirr', 'benchmark_comparison', 'upside_capture_ratio',
            'downside_capture_ratio', 'capture_ratio'
        ]
        
        for metric in available_metrics:
            enabled = request.form.get(f'metric_{metric}_enabled') == 'on'
            if enabled:
                period_type = request.form.get(f'metric_{metric}_period_type', 'days')
                metrics_config[metric] = {}
                
                if period_type == 'custom':
                    start_date_str = request.form.get(f'metric_{metric}_start_date')
                    end_date_str = request.form.get(f'metric_{metric}_end_date')
                    if start_date_str:
                        metrics_config[metric]['start_date'] = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                    if end_date_str:
                        metrics_config[metric]['end_date'] = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                else:
                    period_days = request.form.get(f'metric_{metric}_period_days', type=int)
                    if period_days:
                        metrics_config[metric]['period_days'] = period_days
        
        if not metrics_config:
            return jsonify({'success': False, 'error': 'No metrics selected'}), 400
        
        # Validate
        validation_result = FinancialAnalyticsService.validate_data_for_metrics(
            client_id=client_id,
            metrics_config=metrics_config,
            strict_mode=True
        )
        
        return jsonify({
            'success': True,
            'validation_result': validation_result
        })
        
    except Exception as e:
        logger.error(f"Error validating data: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

