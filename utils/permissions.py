"""
Permission utility functions for role-based access control
"""
from functools import wraps
from flask import redirect, url_for, flash, request
from flask_login import current_user
from models import Role, RolePermission

def get_all_routes():
    """
    Returns a dictionary of all route endpoints organized by category.
    Derived from nav_registry so /roles UI matches the navbar.
    """
    from nav_registry import get_permission_catalog

    return get_permission_catalog()

def has_route_access(route_endpoint):
    """
    Check if current user has access to a route endpoint.
    Returns True if user is admin or has permission through their role.
    """
    if not current_user.is_authenticated:
        return False
    
    if current_user.is_admin:
        return True
    
    return current_user.has_route_access(route_endpoint)

def require_route_access(route_endpoint):
    """
    Decorator to require access to a specific route endpoint.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not has_route_access(route_endpoint):
                flash('You do not have permission to access this page.', 'error')
                return redirect(url_for('main.dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

