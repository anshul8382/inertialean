"""
Access Control Module for Role-Based Access Control
"""
from functools import wraps
from flask import abort, flash, redirect, url_for
from flask_login import current_user
from models import Client


def _normalized_role_name(user):
    """Role name from Role table or legacy User.role, lowercased/stripped."""
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
    """Role as slug: spaces/hyphens → underscores (e.g. 'Ops Manager' → ops_manager)."""
    n = _normalized_role_name(user)
    if not n:
        return ""
    return n.replace(" ", "_").replace("-", "_")


# Ops workflows + internal incentives: core ops, or combined sales+ops role (assign in Users / role table).
_OPS_MANAGER_SLUGS = frozenset({"ops_manager", "sales_ops_manager"})

# External incentive simulator: explicit sales-type roles (assign users in DB); not plain ops_manager.
_INCENTIVE_SIMULATOR_SALES_ROLE_SLUGS = frozenset(
    {
        "sales",
        "sales_executive",
        "bd",
        "business_development",
        "sales_ops_manager",
    }
)


def user_is_sharveen_legacy(user):
    """Deprecated: use capability:incentive_simulator via permission_service. Kept for unmigrated DBs."""
    if not user:
        return False
    un = (getattr(user, "username", None) or "").strip().lower()
    if un == "sharveen":
        return True
    em = (getattr(user, "email", None) or "").strip().lower()
    return em.startswith("sharveen@")


# Backward-compatible alias
user_is_sharveen = user_is_sharveen_legacy


def user_is_ops_manager(user):
    """True for ops_manager or sales_ops_manager (combined sales + ops hat)."""
    if not user:
        return False
    return _normalized_role_slug(user) in _OPS_MANAGER_SLUGS


def user_sees_incentive_sales_simulator(user):
    """External sales incentive UI — capability + role slugs (+ legacy user fallback)."""
    from services.permission_service import user_sees_incentive_sales_simulator as _fn

    return _fn(user)


def user_is_advisor_or_manager_or_ops(user):
    """Advisor/manager/admin/ops-manager access for ops workflows."""
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
            flash('Access denied. Advisor, Manager, or Ops Manager privileges required.', 'error')
            return redirect(url_for('main.dashboard'))
        
        return f(*args, **kwargs)
    return decorated_function


def user_can_access_holdings_report(user):
    """Advisors/managers plus ops — uses capability when seeded."""
    from services.permission_service import user_can_access_holdings_report as _fn

    return _fn(user)


def require_holdings_report_access(f):
    """Advisor, manager, admin, or ops (including sales_ops_manager)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not user_can_access_holdings_report(current_user):
            flash("Access denied. You do not have access to the holdings report.", "error")
            return redirect(url_for("main.dashboard"))
        return f(*args, **kwargs)
    return decorated_function


def user_can_access_account_management(user):
    """Bank statements / heads / analysis: any user with is_admin."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return bool(getattr(user, "is_admin", False))


def require_account_management_access(f):
    """Decorator: login + admin (same rule as account_management blueprint)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not user_can_access_account_management(current_user):
            flash("Access denied. Account management requires admin privileges.", "error")
            return redirect(url_for("main.dashboard"))
        return f(*args, **kwargs)
    return decorated_function


def require_admin(f):
    """Decorator: login + is_admin (bulk exports, sensitive maintenance)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not getattr(current_user, "is_admin", False):
            flash("Access denied. Admin privileges are required.", "error")
            return redirect(url_for("main.dashboard"))
        return f(*args, **kwargs)
    return decorated_function


def require_tasks_access(f):
    """Decorator to allow any authenticated user to access tasks (restricted to own tasks for non-advisor/manager)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
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

def user_can_view_all_clients(user=None):
    """
    True for admin / manager / ops_manager (full client book visibility).
    Advisors and other roles see only clients assigned via advisor_id.
    """
    if user is None:
        user = current_user if current_user and getattr(current_user, "is_authenticated", False) else None
    if not user:
        return False
    return bool(
        getattr(user, "is_admin", False)
        or getattr(user, "is_manager", False)
        or user_is_ops_manager(user)
    )


def scope_clients_query(query, user=None):
    """
    Restrict a Client SQLAlchemy query to the caller's assigned book.

    - Admin / manager: unchanged (see all clients)
    - Authenticated advisor (and other non-manager roles): advisor_id == user.id
    - No request / anonymous: unchanged (batch jobs / scripts keep full query;
      HTTP routes must require login separately)
    """
    if user is None:
        try:
            if not current_user or not getattr(current_user, "is_authenticated", False):
                return query
            user = current_user
        except Exception:
            return query

    if user_can_view_all_clients(user):
        return query
    return query.filter(Client.advisor_id == user.id)


def get_accessible_clients():
    """Helper function to get clients accessible to current user"""
    if not current_user or not current_user.is_authenticated:
        return []
    
    try:
        clients = current_user.get_accessible_clients()
        # Ensure we always return a list, never None
        if clients is None:
            return []
        # Convert query result to list if needed
        if hasattr(clients, '__iter__') and not isinstance(clients, list):
            clients = list(clients)
        return clients if isinstance(clients, list) else []
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error getting accessible clients for user {current_user.id if current_user else 'unknown'}: {str(e)}", exc_info=True)
        return []


def get_accessible_clients_ordered():
    """Accessible clients sorted by name (for dropdowns / pickers)."""
    return sorted(
        get_accessible_clients(),
        key=lambda c: (getattr(c, "name", None) or "").lower(),
    )


def accessible_client_ids():
    """
    Client IDs the current user may see.

    Returns None when:
    - Admin / manager / ops_manager (view all), or
    - No authenticated user (batch jobs / email cron — leave queries unscoped;
      HTTP routes must require login separately).

    Returns a (possibly empty) list for advisors and other scoped roles.
    """
    try:
        if not current_user or not getattr(current_user, "is_authenticated", False):
            return None
    except Exception:
        return None
    if user_can_view_all_clients():
        return None
    return [c.id for c in get_accessible_clients()]


def scope_query_to_accessible_clients(query, client_id_column):
    """
    Restrict a SQLAlchemy query whose rows belong to a client.

    Admin/manager: unchanged. Advisors: client_id IN assigned ids
    (empty book → no rows via IN (-1)).
    """
    ids = accessible_client_ids()
    if ids is None:
        return query
    if not ids:
        return query.filter(client_id_column.in_([-1]))
    return query.filter(client_id_column.in_(ids))


def can_access_client(client_id):
    """Helper function to check if current user can access a client"""
    if not current_user or not current_user.is_authenticated:
        return False
    
    if user_can_view_all_clients(current_user):
        return True
    
    client = Client.query.get(client_id)
    if not client:
        return False
    
    return client.advisor_id == current_user.id


def enforce_client_id_from_view_args():
    """
    Blueprint before_request hook: abort/redirect when URL contains client_id
    the current user may not access. Register on client-scoped blueprints.
    """
    from flask import request

    if not current_user.is_authenticated:
        return None
    if not request.view_args:
        return None
    client_id = request.view_args.get("client_id")
    if client_id is None:
        return None
    if not can_access_client(int(client_id)):
        flash("Access denied. You can only access your assigned clients.", "error")
        return redirect(url_for("clients_v2.list_clients"))
    return None