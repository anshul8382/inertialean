from flask import Flask, session, redirect, url_for, flash
from flask_login import current_user
from extensions import db, mail, login_manager, csrf, migrate
from config import ProductionConfig
import pytz

def status_color(status):
    if status == 'new':
        return 'primary'
    elif status in ['introduction_call_completed', 'risk_profile_sent', 'risk_profile_received']:
        return 'info'
    elif status in ['proposal_sent', 'proposal_accepted']:
        return 'warning'
    elif status in ['kyc_uploaded', 'agreement_sent', 'agreement_signed']:
        return 'primary'
    elif status == 'onboarding_started':
        return 'secondary'
    elif status == 'onboarding_completed':
        return 'success'
    return 'secondary'

def create_app(config_class=ProductionConfig):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # Set timezone to IST
    app.config['TIMEZONE'] = pytz.timezone('Asia/Kolkata')
    
    # Add timezone template filters
    @app.template_filter('ist_datetime')
    def ist_datetime_filter(dt):
        """Convert UTC datetime to IST for display"""
        if dt is None:
            return 'Not Set'
        
        # If datetime is naive, assume it's UTC
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        
        # Convert to IST
        ist_dt = dt.astimezone(app.config['TIMEZONE'])
        return ist_dt.strftime('%Y-%m-%d %H:%M')
    
    @app.template_filter('ist_date')
    def ist_date_filter(dt):
        """Convert UTC datetime to IST date for display"""
        if dt is None:
            return 'Not Set'
        
        # If datetime is naive, assume it's UTC
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        
        # Convert to IST
        ist_dt = dt.astimezone(app.config['TIMEZONE'])
        return ist_dt.strftime('%Y-%m-%d')

    # Initialize extensions
    db.init_app(app)
    mail.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    
    # Configure Flask-Login
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'warning'
    login_manager.session_protection = 'strong'  # Enable session protection
    login_manager.refresh_view = 'auth.login'
    login_manager.needs_refresh_message = 'Please log in again to access this page.'
    login_manager.needs_refresh_message_category = 'warning'
    
    # Add unauthorized handler
    @login_manager.unauthorized_handler
    def unauthorized():
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('auth.login'))
    
    # Add global error handler for 401 Unauthorized
    @app.errorhandler(401)
    def unauthorized_error(error):
        flash('Please log in to access this page.', 'warning')
        return redirect(url_for('auth.login'))
    
    # Add global error handler for 403 Forbidden
    @app.errorhandler(403)
    def forbidden_error(error):
        flash('You do not have permission to access this page.', 'error')
        return redirect(url_for('main.dashboard'))
    
    # Configure CSRF
    app.config['WTF_CSRF_ENABLED'] = True
    app.config['WTF_CSRF_SECRET_KEY'] = app.config['SECRET_KEY']
    app.config['WTF_CSRF_TIME_LIMIT'] = 3600
    app.config['WTF_CSRF_SSL_STRICT'] = False
    app.config['WTF_CSRF_METHODS'] = ['POST', 'PUT', 'PATCH', 'DELETE']
    app.config['WTF_CSRF_CHECK_DEFAULT'] = True
    app.config['WTF_CSRF_HEADERS'] = ['X-CSRFToken', 'X-CSRF-Token']
    app.config['WTF_CSRF_FIELD_NAME'] = 'csrf_token'
    
    # Initialize CSRF after config
    csrf.init_app(app)

    # Register template filters
    app.jinja_env.filters['status_color'] = status_color

    # Register blueprints
    from routes.main import main
    from routes.auth import auth
    from routes.leads import leads
    from routes.meetings import meetings
    from routes.workflows import workflows
    from routes.settings import settings
    from routes.clients import clients_bp
    from routes.alerts import alerts
    from routes.users import users
    from routes.recommended_trades import recommended_trades_bp
    from routes.agreements import agreements
    from routes.unified_recommendations import unified_recommendations_bp
    from routes.client_status_api import client_status_api
    from routes.billing_api import billing_api
    from review_routes import review_bp
    from monthly_investments import monthly_investments

    app.register_blueprint(main)
    app.register_blueprint(auth, url_prefix='/auth')
    app.register_blueprint(leads, url_prefix='/leads')
    app.register_blueprint(meetings)
    app.register_blueprint(workflows)
    app.register_blueprint(settings)
    app.register_blueprint(clients_bp, url_prefix='/clients')
    app.register_blueprint(alerts, url_prefix='/alerts')
    app.register_blueprint(users, url_prefix='/users')
    app.register_blueprint(recommended_trades_bp, url_prefix='/recommended_trades')
    app.register_blueprint(agreements, url_prefix='/agreements')
    app.register_blueprint(unified_recommendations_bp)
    app.register_blueprint(client_status_api)
    app.register_blueprint(billing_api)
    app.register_blueprint(review_bp, url_prefix='/review')
    app.register_blueprint(monthly_investments)

    # Initialize API
    from api import init_app as init_api
    init_api(app)

    # Import models after app is created
    with app.app_context():
        from models import User
        
        @login_manager.user_loader
        def load_user(user_id):
            return User.query.get(int(user_id))
    
    # Add session management
    @app.before_request
    def before_request():
        # Set session as permanent and mark as modified
        session.permanent = True
        session.modified = True

    return app 