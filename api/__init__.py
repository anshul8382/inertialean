"""
API Package - Centralized API management
"""
from flask import Blueprint
from .v1 import api_v1

# Main API Blueprint
api = Blueprint('api', __name__, url_prefix='/api')

# Register API versions
api.register_blueprint(api_v1, url_prefix='/v1')

def init_app(app):
    """Initialize API with Flask app"""
    app.register_blueprint(api)
    
    # Register V2 API blueprint
    try:
        from .v2 import api_v2
        app.register_blueprint(api_v2, url_prefix='/api/v2')
    except Exception as e:
        app.logger.warning(f"Skipping V2 API blueprint: {e}")
    
    # Register V2 blueprints directly (for backwards compatibility)
    try:
        from .v2 import transactions_v2_bp
        app.register_blueprint(transactions_v2_bp)
    except Exception as e:
        app.logger.warning(f"Skipping V2 transactions blueprint: {e}")