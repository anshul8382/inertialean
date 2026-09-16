"""
Standardized API Response Format
"""
from flask import jsonify, current_app
from datetime import datetime
from typing import Any, Dict, Optional, Union
import traceback
import json
import logging

logger = logging.getLogger(__name__)

class APIResponse:
    """Standardized API response format"""
    
    @staticmethod
    def success(
        data: Any = None,
        message: str = "Success",
        status_code: int = 200,
        meta: Optional[Dict] = None
    ) -> tuple:
        """
        Create a successful API response
        
        Args:
            data: Response data
            message: Success message
            status_code: HTTP status code
            meta: Additional metadata (pagination, etc.)
            
        Returns:
            Tuple of (response_dict, status_code)
        """
        response = {
            "success": True,
            "message": message,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
            "version": "v1"
        }
        
        if meta:
            response["meta"] = meta
        
        # Ensure data is JSON serializable
        try:
            if data is not None:
                # Test serialization
                json.dumps(data, default=str)
        except (TypeError, ValueError) as json_error:
            logger.error(f"Data contains non-serializable objects: {str(json_error)}")
            # Try to convert problematic data to string representation
            response["data"] = str(data) if data is not None else None
            response["serialization_warning"] = "Some data was converted to string due to serialization issues"
            
        return jsonify(response), status_code
    
    @staticmethod
    def error(
        message: str = "An error occurred",
        status_code: int = 400,
        error_code: Optional[str] = None,
        details: Optional[Dict] = None,
        trace_id: Optional[str] = None
    ) -> tuple:
        """
        Create an error API response
        
        Args:
            message: Error message
            status_code: HTTP status code
            error_code: Application-specific error code
            details: Additional error details
            trace_id: Request trace ID for debugging
            
        Returns:
            Tuple of (response_dict, status_code)
        """
        response = {
            "success": False,
            "message": message,
            "error_code": error_code,
            "timestamp": datetime.utcnow().isoformat(),
            "version": "v1"
        }
        
        if details:
            response["details"] = details
            
        if trace_id:
            response["trace_id"] = trace_id
        else:
            try:
                from utils.request_context import get_request_id

                rid = get_request_id()
                if rid:
                    response["trace_id"] = rid
            except Exception:
                pass
            
        # Add stack trace in development (but don't break if traceback fails)
        try:
            from flask import has_request_context
            if has_request_context():
                try:
                    if current_app.config.get('DEBUG', False):
                        import traceback as tb
                        response["stack_trace"] = tb.format_exc()
                except (RuntimeError, AttributeError):
                    pass  # current_app not available or config not accessible
        except Exception:
            pass  # Don't fail if we can't add stack trace
            
        return jsonify(response), status_code
    
    @staticmethod
    def paginated(
        data: list,
        page: int,
        per_page: int,
        total: int,
        message: str = "Success"
    ) -> tuple:
        """
        Create a paginated API response
        
        Args:
            data: List of items
            page: Current page number
            per_page: Items per page
            total: Total number of items
            message: Success message
            
        Returns:
            Tuple of (response_dict, status_code)
        """
        total_pages = (total + per_page - 1) // per_page
        
        meta = {
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
        }
        
        return APIResponse.success(
            data=data,
            message=message,
            meta=meta
        )

def api_response(func):
    """
    Decorator to automatically format API responses
    
    Usage:
        @api_response
        def my_api_function():
            return {"key": "value"}  # Will be wrapped in success response
            
        @api_response
        def my_error_function():
            raise APIException("Something went wrong", 400)
    """
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            
            # If result is already a tuple (response, status_code), return as is
            if isinstance(result, tuple) and len(result) == 2:
                return result
                
            # Otherwise, wrap in success response
            return APIResponse.success(data=result)
            
        except Exception as e:
            # Handle different types of exceptions
            if hasattr(e, 'status_code'):
                return APIResponse.error(
                    message=str(e),
                    status_code=e.status_code,
                    error_code=getattr(e, 'error_code', None)
                )
            else:
                return APIResponse.error(
                    message="Internal server error",
                    status_code=500,
                    error_code="INTERNAL_ERROR"
                )
    
    wrapper.__name__ = func.__name__
    return wrapper