"""
API Decorators for authentication, authorization, and rate limiting
"""
from functools import wraps
from flask import request, current_app, g
from .exceptions import UnauthorizedError, ForbiddenError, RateLimitError
import time
from collections import defaultdict

# Simple in-memory rate limiting (use Redis in production)
rate_limit_storage = defaultdict(list)

def api_auth_required(f):
    """Decorator to require API authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # For now, we'll use session-based auth (same as web interface)
        # Later this can be enhanced with API keys or JWT tokens
        from flask_login import current_user
        from .response import APIResponse
        
        if not current_user.is_authenticated:
            return APIResponse.error(
                message="Authentication required",
                status_code=401,
                error_code="UNAUTHORIZED"
            )
        
        # Store user info in g for use in the function
        g.api_user = current_user
        
        return f(*args, **kwargs)
    return decorated_function

def api_permission_required(permission: str):
    """Decorator to require specific permissions"""
    def permission_decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Check if user has required permission
            # This is a simplified version - implement based on your auth system
            user_permissions = getattr(g, 'user_permissions', [])
            
            if permission not in user_permissions:
                raise ForbiddenError(f"Permission '{permission}' required")
            
            return f(*args, **kwargs)
        return decorated_function
    return permission_decorator

def api_rate_limit(requests_per_minute: int = 60):
    """Decorator to implement rate limiting"""
    def rate_limit_decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            from .response import APIResponse
            
            # Get client identifier (IP address or user ID)
            client_id = str(g.api_user.id) if hasattr(g, 'api_user') else request.remote_addr
            
            current_time = time.time()
            minute_ago = current_time - 60
            
            # Clean old entries
            rate_limit_storage[client_id] = [
                timestamp for timestamp in rate_limit_storage[client_id]
                if timestamp > minute_ago
            ]
            
            # Check rate limit
            if len(rate_limit_storage[client_id]) >= requests_per_minute:
                return APIResponse.error(
                    message=f"Rate limit exceeded: {requests_per_minute} requests per minute",
                    status_code=429,
                    error_code="RATE_LIMIT_EXCEEDED"
                )
            
            # Add current request
            rate_limit_storage[client_id].append(current_time)
            
            return f(*args, **kwargs)
        return decorated_function
    return rate_limit_decorator