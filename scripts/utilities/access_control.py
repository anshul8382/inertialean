"""
Access Control Module for Role-Based Access Control
(Kept aligned with project-root access_control.py for scripts run from utilities/.)
"""
from functools import wraps
from flask import abort, flash, redirect, url_for
from flask_login import current_user
from models import Client


def _normalized_role_name(user):
    if not user:
        return ""
    ro = getattr(user, "role_obj", None)
    if ro is not None and getattr(ro, "name", None):
        return (ro.name or "").strip().lower()
    leg = getattr(user, "role", None)
    if isinstance(leg, str):
        return leg.strip().lower()
    return ""


def _normalized_role_slug(user):
    n = _normalized_role_name(user)
    if not n:
        return ""
    return n.replace(" ", "_").replace("-", "_")


_OPS_MANAGER_SLUGS = frozenset({"ops_manager", "sales_ops_manager"})


def user_is_ops_manager(user):
    if not user:
        return False
    return _normalized_role_slug(user) in _OPS_MANAGER_SLUGS


def user_is_advisor_or_manager_or_ops(user):
    if not user:
        return False
    return bool(
        getattr(user, "is_admin", False)
        or getattr(user, "is_manager", False)
        or getattr(user, "is_advisor", False)
        or user_is_ops_manager(user)
    )


def require_manager(f):
    """Decorator to require manager role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        
        if not current_user.is_manager:
            flash('Access denied. Manager privileges required.', 'error')
            return redirect(url_for('main.dashboard'))
        
        return f(*args, **kwargs)
    return decorated_function

def require_advisor_or_manager(f):
    """Decorator to require advisor, manager, or ops manager role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))

        if not user_is_advisor_or_manager_or_ops(current_user):
            flash(
                "Access denied. Advisor, Manager, or Ops Manager privileges required.",
                "error",
            )
            return redirect(url_for("main.dashboard"))

        return f(*args, **kwargs)

    return decorated_function

def require_ai_models_access(f):
    """Decorator to require AI models access (admin or manager only)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        
        if not (current_user.is_admin or current_user.is_manager):
            flash('Access denied. AI Models section requires Admin or Manager privileges.', 'error')
            return redirect(url_for('main.dashboard'))
        
        return f(*args, **kwargs)
    return decorated_function

def client_access_required(f):
    """Decorator to check if user can access a specific client"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        
        # Get client_id from kwargs
        client_id = kwargs.get('client_id')
        if not client_id:
            # Try to get from request args
            from flask import request
            client_id = request.args.get('client_id') or request.form.get('client_id')
        
        if client_id:
            client = Client.query.get_or_404(client_id)
            if not current_user.can_access_client(client):
                flash('Access denied. You can only access your assigned clients.', 'error')
                return redirect(url_for('main.clients'))
        
        return f(*args, **kwargs)
    return decorated_function

def get_accessible_clients():
    """Helper function to get clients accessible to current user"""
    if not current_user or not current_user.is_authenticated:
        return []
    
    return current_user.get_accessible_clients()

def can_access_client(client_id):
    """Helper function to check if current user can access a client"""
    if not current_user or not current_user.is_authenticated:
        return False
    
    if current_user.is_manager:
        return True
    
    client = Client.query.get(client_id)
    if not client:
        return False
    
    return client.advisor_id == current_user.id