"""
V2 API Module
Contains improved APIs with better performance and accuracy
"""

from flask import Blueprint

# Import V2 blueprints
from api.v2.transactions import transactions_v2_bp
from api.v2.period_analysis import period_analysis_v2_bp

# Main V2 API Blueprint (no prefix here, will be set during registration)
api_v2 = Blueprint('api_v2', __name__)


@api_v2.before_request
def _v2_require_auth():
    from api.v2.auth_guard import enforce_v2_api_auth
    return enforce_v2_api_auth()


# Register all V2 API modules
api_v2.register_blueprint(period_analysis_v2_bp, url_prefix='/clients')

__all__ = ['api_v2', 'transactions_v2_bp', 'period_analysis_v2_bp']

