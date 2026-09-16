"""
Unified error handling utilities for the Inertia Investment Management Platform
"""
from functools import wraps
from flask import redirect, url_for, flash, jsonify, current_app
from extensions import db
import logging
import traceback

logger = logging.getLogger(__name__)


def handle_errors(redirect_to='main.dashboard'):
    """
    Standardized error handling decorator for Flask routes with database rollback
    
    Args:
        redirect_to: Route name to redirect to on error (default: 'main.dashboard')
    
    Usage:
        @routes.route('/some-route')
        @handle_errors(redirect_to='main.dashboard')
        def some_function():
            # Your code here
            pass
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except Exception as e:
                # Rollback database transaction on error
                try:
                    db.session.rollback()
                except Exception as rollback_error:
                    logger.error(f"Error during rollback: {str(rollback_error)}")
                
                # Log error with context
                logger.error(
                    f"Error in {f.__name__}: {str(e)}",
                    extra={
                        'function': f.__name__,
                        'error': str(e),
                        'traceback': traceback.format_exc()
                    },
                    exc_info=True
                )
                flash(f'An error occurred: {str(e)}', 'error')
                return redirect(url_for(redirect_to))
        return decorated_function
    return decorator


def handle_api_errors():
    """
    Standardized error handling decorator for API endpoints with database rollback
    
    Returns JSON responses instead of redirects
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except Exception as e:
                # Rollback database transaction on error
                try:
                    db.session.rollback()
                except Exception as rollback_error:
                    logger.error(f"Error during rollback: {str(rollback_error)}")
                
                logger.error(
                    f"API Error in {f.__name__}: {str(e)}",
                    extra={
                        'function': f.__name__,
                        'error': str(e),
                        'traceback': traceback.format_exc()
                    },
                    exc_info=True
                )
                return jsonify({
                    'success': False,
                    'error': str(e),
                    'message': 'An error occurred processing your request'
                }), 500
        return decorated_function
    return decorator


def handle_db_errors(redirect_to='main.dashboard'):
    """
    Specialized error handler for database operations
    Handles SQLAlchemy-specific errors with database rollback
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except Exception as e:
                # Rollback database transaction
                try:
                    db.session.rollback()
                except Exception as rollback_error:
                    logger.error(f"Error during rollback: {str(rollback_error)}")
                
                error_type = type(e).__name__
                logger.error(
                    f"Database error in {f.__name__}: {str(e)}",
                    extra={
                        'function': f.__name__,
                        'error_type': error_type,
                        'error': str(e),
                        'traceback': traceback.format_exc()
                    },
                    exc_info=True
                )
                
                # User-friendly error messages based on error type
                if 'IntegrityError' in error_type:
                    flash('Database integrity error. The data may already exist.', 'error')
                elif 'OperationalError' in error_type:
                    flash('Database connection error. Please try again.', 'error')
                else:
                    flash(f'An error occurred: {str(e)}', 'error')
                
                return redirect(url_for(redirect_to))
        return decorated_function
    return decorator

