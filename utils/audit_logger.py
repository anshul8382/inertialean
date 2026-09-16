"""
API Audit Logger
Records input/output of all API calls for comprehensive audit trails
"""
import json
import logging
import os
from datetime import datetime
from functools import wraps
from flask import request, g
import traceback

# Configure audit logger
audit_logger = logging.getLogger('api_audit')
audit_logger.setLevel(logging.INFO)

_log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
os.makedirs(_log_dir, exist_ok=True)
_audit_log_path = os.environ.get('API_AUDIT_LOG_PATH', os.path.join(_log_dir, 'api_audit.log'))

# Create file handler for audit logs (repo logs/ locally; override with API_AUDIT_LOG_PATH)
audit_handler = logging.FileHandler(_audit_log_path)
audit_handler.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
audit_handler.setFormatter(formatter)

# Add handler to logger
audit_logger.addHandler(audit_handler)

def audit_api_call(func):
    """
    Decorator to audit API calls with input/output logging
    
    Usage:
    @audit_api_call
    def my_api_function():
        return {"result": "success"}
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Generate audit ID
        audit_id = f"AUDIT_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        # Skip logging for transaction APIs to reduce noise
        skip_apis = [
            'get_transactions',
            'get_transaction',
            'create_transaction',
            'update_transaction',
            'delete_transaction',
            'bulk_transactions',
            'process_transaction_file',
            'get_client_transactions',
            'upload_transactions',
            'preview_transactions',
            'process_transactions'
        ]
        
        # Also skip if module is transactions API
        skip_modules = [
            'api.v1.transactions',
            'routes.transactions'
        ]
        
        should_skip = (func.__name__ in skip_apis or 
                      func.__module__ in skip_modules or
                      'transaction' in func.__name__.lower())
        
        if not should_skip:
            # Log input
            input_data = {
                'audit_id': audit_id,
                'function': func.__name__,
                'module': func.__module__,
                'timestamp': datetime.now().isoformat(),
                'request_method': getattr(request, 'method', 'N/A'),
                'request_url': getattr(request, 'url', 'N/A'),
                'request_args': dict(request.args) if hasattr(request, 'args') else {},
                'request_json': request.get_json(silent=True) if hasattr(request, 'get_json') else None,
                'function_args': args,
                'function_kwargs': kwargs
            }
            
            audit_logger.info(f"API_INPUT: {json.dumps(input_data, default=str)}")
        
        try:
            # Execute function
            result = func(*args, **kwargs)
            
            if not should_skip:
                # Log output
                output_data = {
                    'audit_id': audit_id,
                    'function': func.__name__,
                    'timestamp': datetime.now().isoformat(),
                    'status': 'SUCCESS',
                    'result_type': type(result).__name__,
                    'result_size': len(str(result)) if result else 0,
                    'result_preview': str(result)[:500] if result else None  # First 500 chars
                }
                
                audit_logger.info(f"API_OUTPUT: {json.dumps(output_data, default=str)}")
            
            return result
            
        except Exception as e:
            if not should_skip:
                # Log error
                error_data = {
                    'audit_id': audit_id,
                    'function': func.__name__,
                    'timestamp': datetime.now().isoformat(),
                    'status': 'ERROR',
                    'error_type': type(e).__name__,
                    'error_message': str(e),
                    'traceback': traceback.format_exc()
                }
                
                audit_logger.error(f"API_ERROR: {json.dumps(error_data, default=str)}")
            
            raise
    
    return wrapper

def log_api_call(api_name, input_data, output_data, status='SUCCESS', error=None):
    """
    Manual API call logging for complex scenarios
    
    Args:
        api_name: Name of the API call
        input_data: Input parameters
        output_data: Output result
        status: 'SUCCESS' or 'ERROR'
        error: Error details if status is 'ERROR'
    """
    audit_id = f"AUDIT_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    
    log_data = {
        'audit_id': audit_id,
        'api_name': api_name,
        'timestamp': datetime.now().isoformat(),
        'status': status,
        'input': input_data,
        'output': output_data,
        'error': error
    }
    
    if status == 'SUCCESS':
        audit_logger.info(f"API_CALL: {json.dumps(log_data, default=str)}")
    else:
        audit_logger.error(f"API_CALL: {json.dumps(log_data, default=str)}")

def get_audit_summary(start_date=None, end_date=None, api_name=None):
    """
    Get audit summary for a date range
    
    Args:
        start_date: Start date for filtering
        end_date: End date for filtering
        api_name: Specific API name to filter
    
    Returns:
        List of audit entries
    """
    # This would typically query a database or parse log files
    # For now, return a placeholder
    return {
        'message': 'Audit summary functionality to be implemented',
        'start_date': start_date,
        'end_date': end_date,
        'api_name': api_name
    }
