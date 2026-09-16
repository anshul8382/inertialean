"""
Financial Analytics API
RESTful API for financial metrics calculation with configurable periods
"""

from flask import Blueprint, request
from flask_login import login_required
from datetime import date, datetime
from api.core.response import APIResponse
from services.financial_analytics_service import FinancialAnalyticsService
from models import Client
import logging

logger = logging.getLogger(__name__)

financial_analytics_bp = Blueprint('financial_analytics', __name__)

@financial_analytics_bp.route('/<int:client_id>/metrics', methods=['POST'])
@login_required
def calculate_metrics(client_id):
    """
    Calculate financial metrics with custom time periods
    
    Request Body:
    {
        "metrics": {
            "beta": {
                "period_days": 365,
                "start_date": "2023-01-01",  // optional
                "end_date": "2024-01-01"    // optional
            },
            "alpha": {
                "period_days": 180
            }
        }
    }
    """
    try:
        client = Client.query.get_or_404(client_id)
        data = request.get_json()
        
        if not data or 'metrics' not in data:
            return APIResponse.error(
                message="Missing 'metrics' in request body",
                status_code=400
            )
        
        metrics_config = data['metrics']
        
        # Validate and parse dates
        for metric_type, config in metrics_config.items():
            if 'start_date' in config:
                try:
                    config['start_date'] = datetime.strptime(
                        config['start_date'], '%Y-%m-%d'
                    ).date()
                except ValueError:
                    return APIResponse.error(
                        message=f"Invalid start_date format for {metric_type}. Use YYYY-MM-DD",
                        status_code=400
                    )
            
            if 'end_date' in config:
                try:
                    config['end_date'] = datetime.strptime(
                        config['end_date'], '%Y-%m-%d'
                    ).date()
                except ValueError:
                    return APIResponse.error(
                        message=f"Invalid end_date format for {metric_type}. Use YYYY-MM-DD",
                        status_code=400
                    )
        
        # Calculate metrics
        results = FinancialAnalyticsService.calculate_multiple_metrics(
            client_id=client_id,
            metrics_config=metrics_config
        )
        
        return APIResponse.success(
            data=results,
            message=f"Financial metrics calculated for client {client_id}"
        )
        
    except Exception as e:
        logger.error(f"Error calculating metrics for client {client_id}: {str(e)}")
        return APIResponse.error(
            message=str(e),
            status_code=500
        )

@financial_analytics_bp.route('/<int:client_id>/metrics/<metric_type>', methods=['GET', 'POST'])
@login_required
def calculate_single_metric(client_id, metric_type):
    """
    Calculate a single metric
    
    GET Parameters or POST Body:
    - period_days: int (default: metric default)
    - start_date: YYYY-MM-DD (optional)
    - end_date: YYYY-MM-DD (optional, defaults to today)
    """
    try:
        client = Client.query.get_or_404(client_id)
        
        if request.method == 'POST':
            data = request.get_json() or {}
            period_days = data.get('period_days')
            start_date_str = data.get('start_date')
            end_date_str = data.get('end_date')
        else:
            period_days = request.args.get('period_days', type=int)
            start_date_str = request.args.get('start_date')
            end_date_str = request.args.get('end_date')
        
        start_date = None
        if start_date_str:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        
        end_date = None
        if end_date_str:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        
        result = FinancialAnalyticsService.calculate_metric_with_period(
            client_id=client_id,
            metric_type=metric_type,
            start_date=start_date,
            end_date=end_date,
            period_days=period_days
        )
        
        if result.get('success', False):
            return APIResponse.success(
                data=result,
                message=f"{metric_type} calculated successfully"
            )
        else:
            return APIResponse.error(
                message=result.get('error', 'Calculation failed'),
                data=result,
                status_code=400
            )
            
    except Exception as e:
        logger.error(f"Error calculating {metric_type} for client {client_id}: {str(e)}")
        return APIResponse.error(
            message=str(e),
            status_code=500
        )

@financial_analytics_bp.route('/<int:client_id>/validate', methods=['POST'])
@login_required
def validate_data(client_id):
    """
    Validate data availability for metrics before calculation
    
    Request Body:
    {
        "metrics": {
            "beta": {"period_days": 365},
            "alpha": {"period_days": 180}
        },
        "strict_mode": true  // Optional, default: true
    }
    """
    try:
        client = Client.query.get_or_404(client_id)
        data = request.get_json() or {}
        
        if 'metrics' not in data:
            return APIResponse.error(
                message="Missing 'metrics' in request body",
                status_code=400
            )
        
        metrics_config = data['metrics']
        strict_mode = data.get('strict_mode', True)
        
        # Parse dates
        for metric_type, config in metrics_config.items():
            if 'start_date' in config and isinstance(config['start_date'], str):
                try:
                    config['start_date'] = datetime.strptime(config['start_date'], '%Y-%m-%d').date()
                except ValueError:
                    return APIResponse.error(
                        message=f"Invalid start_date format for {metric_type}",
                        status_code=400
                    )
            if 'end_date' in config and isinstance(config['end_date'], str):
                try:
                    config['end_date'] = datetime.strptime(config['end_date'], '%Y-%m-%d').date()
                except ValueError:
                    return APIResponse.error(
                        message=f"Invalid end_date format for {metric_type}",
                        status_code=400
                    )
        
        # Validate data
        validation_result = FinancialAnalyticsService.validate_data_for_metrics(
            client_id=client_id,
            metrics_config=metrics_config,
            strict_mode=strict_mode
        )
        
        return APIResponse.success(
            data=validation_result,
            message="Data validation completed"
        )
        
    except Exception as e:
        logger.error(f"Error validating data for client {client_id}: {str(e)}")
        return APIResponse.error(
            message=str(e),
            status_code=500
        )

@financial_analytics_bp.route('/metrics/available', methods=['GET'])
@login_required
def get_available_metrics():
    """Get list of available metrics with their default periods"""
    return APIResponse.success(
        data={
            'available_metrics': list(FinancialAnalyticsService.DEFAULT_PERIODS.keys()),
            'default_periods': FinancialAnalyticsService.DEFAULT_PERIODS,
            'min_required_days': FinancialAnalyticsService.MIN_REQUIRED_DAYS
        },
        message="Available metrics retrieved"
    )

