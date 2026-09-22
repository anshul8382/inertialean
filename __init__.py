from datetime import datetime
import os

from flask import Flask, session, redirect, url_for, flash, request
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_login import current_user, login_required
from extensions import db, mail, login_manager, csrf, migrate, session_ext
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
    from utils.wsgi_path_normalizer import DoubleSlashPathNormalizer

    app.wsgi_app = DoubleSlashPathNormalizer(app.wsgi_app)
    # Trust X-Forwarded-* from Apache/nginx so request.is_secure, Host, and URLs match the public site.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)
    # Handle class-level @property configs (e.g. SQLALCHEMY_DATABASE_URI) safely.
    try:
        uri_val = app.config.get('SQLALCHEMY_DATABASE_URI')
        if isinstance(uri_val, property):
            cfg_obj = config_class() if isinstance(config_class, type) else config_class
            app.config['SQLALCHEMY_DATABASE_URI'] = cfg_obj.SQLALCHEMY_DATABASE_URI
    except Exception:
        pass
    
    # Set timezone to IST
    app.config['TIMEZONE'] = pytz.timezone('Asia/Kolkata')
    
    # Add timezone template filters
    @app.template_filter('ist_datetime')
    def ist_datetime_filter(dt):
        """Format datetime in app local time (TIMEZONE, default IST).

        Naive datetimes are treated as wall clock in that zone (user-entered
        form/meeting times). For DB columns filled with datetime.utcnow(), use
        ``utc_to_ist`` instead.
        """
        if dt is None:
            return 'Not Set'
        tz = app.config['TIMEZONE']
        if dt.tzinfo is None:
            ist_dt = tz.localize(dt)
        else:
            ist_dt = dt.astimezone(tz)
        return ist_dt.strftime('%Y-%m-%d %H:%M')

    @app.template_filter('utc_to_ist')
    def utc_to_ist_filter(dt):
        """Display naive-UTC (or aware) datetime as Asia/Kolkata wall clock."""
        if dt is None:
            return 'Not Set'
        tz = app.config['TIMEZONE']
        if dt.tzinfo is None:
            ist_dt = pytz.UTC.localize(dt).astimezone(tz)
        else:
            ist_dt = dt.astimezone(tz)
        return ist_dt.strftime('%Y-%m-%d %H:%M')

    @app.template_filter('ist_date')
    def ist_date_filter(dt):
        """Format date in app local time (TIMEZONE, default IST). See ist_datetime_filter."""
        if dt is None:
            return 'Not Set'
        tz = app.config['TIMEZONE']
        if dt.tzinfo is None:
            ist_dt = tz.localize(dt)
        else:
            ist_dt = dt.astimezone(tz)
        return ist_dt.strftime('%Y-%m-%d')

    @app.template_filter('utc_to_ist_date')
    def utc_to_ist_date_filter(dt):
        """Date portion of a naive-UTC datetime in Asia/Kolkata."""
        if dt is None:
            return 'Not Set'
        tz = app.config['TIMEZONE']
        if dt.tzinfo is None:
            ist_dt = pytz.UTC.localize(dt).astimezone(tz)
        else:
            ist_dt = dt.astimezone(tz)
        return ist_dt.strftime('%Y-%m-%d')

    @app.template_filter('age_days')
    def age_days(dt):
        """Return full days from a datetime to now (UTC)."""
        if not dt:
            return 0
        return max(0, (datetime.utcnow() - dt).days)

    # Initialize database FIRST (needed for Flask-Session)
    db.init_app(app)
    
    # Configure Flask-Session for server-side session storage (solves cookie size limit issue)
    # This stores session data in the database instead of cookies.
    # sessions.data should be MEDIUMBLOB (see migrations/widen_flask_sessions_data_column.py);
    # recommendation pending state is also slimmed before write (routes/unified_recommendations.py).
    # Must be initialized BEFORE login_manager and other session-using extensions.
    import logging
    logger = logging.getLogger(__name__)
    if session_ext is not None:
        app.config.setdefault('SESSION_TYPE', 'sqlalchemy')
        app.config.setdefault('SESSION_SQLALCHEMY', db)
        app.config.setdefault('SESSION_SQLALCHEMY_TABLE', 'sessions')
        app.config.setdefault('SESSION_PERMANENT', True)
        app.config.setdefault('SESSION_USE_SIGNER', True)
        app.config.setdefault('SESSION_KEY_PREFIX', 'inertia_session:')
        app.config.setdefault('SESSION_COOKIE_HTTPONLY', True)
        app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')

        # Canonical init; Flask-Session will set app.session_interface internally.
        session_ext.init_app(app)
        logger.info(f"Flask-Session initialized. session_interface={type(app.session_interface).__name__}")
    else:
        logger.warning("Flask-Session not installed; using Flask signed cookie sessions.")
    
    # Now initialize other extensions that use sessions
    mail.init_app(app)

    # Lean / parallel VPS: prefix subjects + HTML banner when EMAIL_SOURCE_TAG is set
    _orig_mail_send = mail.send

    def _mail_send_tagged(message, *args, **kwargs):
        try:
            from services.email_source_tag import apply_to_flask_message

            apply_to_flask_message(message)
        except Exception:
            pass
        return _orig_mail_send(message, *args, **kwargs)

    mail.send = _mail_send_tagged  # type: ignore[method-assign]

    login_manager.init_app(app)
    migrate.init_app(app, db)
    
    # Configure Flask-Login
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'warning'
    login_manager.session_protection = app.config.get('LOGIN_SESSION_PROTECTION', 'basic')
    login_manager.refresh_view = 'auth.login'
    login_manager.needs_refresh_message = 'Please log in again to access this page.'
    login_manager.needs_refresh_message_category = 'warning'

    @login_manager.request_loader
    def load_user_from_bearer(_request):
        """Authenticate /api/* via Authorization: Bearer <JWT> (mobile apps)."""
        auth_header = _request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        from services.jwt_service import user_from_token

        return user_from_token(auth_header[7:].strip())
    
    def _login_redirect_with_next():
        """Send user to login with ?next= relative path (auth.login only accepts path-style next)."""
        from utils.internal_next import normalize_internal_next

        flash('Please log in to access this page.', 'warning')
        nx = normalize_internal_next(request.path) or '/'
        if nx.startswith('/auth/login'):
            return redirect(url_for('auth.login'))
        return redirect(url_for('auth.login', next=nx))

    # Add unauthorized handler
    def _api_json_unauthorized():
        from api.core.response import APIResponse
        return APIResponse.error("Unauthorized", status_code=401, error_code="unauthorized")

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith("/api/"):
            return _api_json_unauthorized()
        return _login_redirect_with_next()

    # Add global error handler for 401 Unauthorized
    @app.errorhandler(401)
    def unauthorized_error(error):
        if request.path.startswith("/api/"):
            return _api_json_unauthorized()
        return _login_redirect_with_next()
    
    # Add global error handler for 403 Forbidden
    @app.errorhandler(403)
    def forbidden_error(error):
        flash('You do not have permission to access this page.', 'error')
        return redirect(url_for('main.dashboard'))
    
    # Configure CSRF
    app.config['WTF_CSRF_ENABLED'] = True
    app.config['WTF_CSRF_SECRET_KEY'] = app.config['SECRET_KEY']
    # WTF_CSRF_TIME_LIMIT comes from config (default None = session-scoped tokens).
    app.config['WTF_CSRF_SSL_STRICT'] = False
    app.config['WTF_CSRF_METHODS'] = ['POST', 'PUT', 'PATCH', 'DELETE']
    app.config['WTF_CSRF_CHECK_DEFAULT'] = True
    app.config['WTF_CSRF_HEADERS'] = ['X-CSRFToken', 'X-CSRF-Token']
    app.config['WTF_CSRF_FIELD_NAME'] = 'csrf_token'
    
    # Initialize CSRF after config
    csrf.init_app(app)

    @app.before_request
    def _seed_csrf_in_session_for_get():
        """
        Ensure CSRF token exists in server-side session before any form POST.

        Intermittent 'CSRF session token is missing' on login (fixed on retry) happens when
        the first GET did not persist session/CSRF (new cookie, proxy, or session_protection).
        """
        if request.method != 'GET':
            return
        if request.path.startswith('/api/'):
            return
        if not app.config.get('WTF_CSRF_ENABLED', True):
            return
        try:
            from flask_wtf.csrf import generate_csrf

            if 'csrf_token' not in session:
                generate_csrf()
                session.modified = True
        except Exception:
            pass

    # Register template filters and globals
    app.jinja_env.filters['status_color'] = status_color
    from template_filters import from_json, int_format, nl2br, number_format

    app.add_template_filter(number_format, 'number_format')
    app.add_template_filter(int_format, 'int_format')
    app.add_template_filter(nl2br, 'nl2br')
    app.add_template_filter(from_json, 'from_json')
    app.jinja_env.globals['has_blueprint'] = lambda name: name in app.blueprints
    app.jinja_env.globals['has_endpoint'] = lambda ep: ep in app.view_functions
    from utils.permissions import has_route_access as _has_route_access

    app.jinja_env.globals['has_route_access'] = _has_route_access

    def _nav_can(endpoint, policy="permission"):
        from flask_login import current_user
        from services.permission_service import nav_can as _nav_can_fn

        return _nav_can_fn(current_user, endpoint, policy)

    def _nav_group_visible(group_key):
        from flask import current_app
        from flask_login import current_user
        from services.permission_service import nav_group_visible as _nav_group_visible_fn

        return _nav_group_visible_fn(current_user, group_key, dict(current_app.config))

    def _has_capability(capability):
        from flask_login import current_user
        from services.permission_service import user_has_capability

        return user_has_capability(current_user, capability)

    def _can_view_sensitive_client_data():
        from flask_login import current_user
        from services.permission_service import user_can_view_sensitive_client_data

        return user_can_view_sensitive_client_data(current_user)

    def _can_view_client_contact_pii():
        from flask_login import current_user
        from services.permission_service import user_can_view_client_contact_pii

        return user_can_view_client_contact_pii(current_user)

    app.jinja_env.globals['nav_can'] = _nav_can
    app.jinja_env.globals['nav_group_visible'] = _nav_group_visible
    app.jinja_env.globals['has_capability'] = _has_capability
    app.jinja_env.globals['can_view_sensitive_client_data'] = _can_view_sensitive_client_data
    app.jinja_env.globals['can_view_client_contact_pii'] = _can_view_client_contact_pii

    from services.client_data_redaction import (
        display_email,
        display_phone,
        mask_dob,
        mask_phone,
    )
    from services.pii_masking import mask_client_name, mask_email

    app.jinja_env.filters['mask_email'] = mask_email
    app.jinja_env.filters['mask_phone'] = mask_phone
    app.jinja_env.filters['mask_dob'] = mask_dob
    app.jinja_env.filters['mask_client_name'] = mask_client_name
    app.jinja_env.filters['display_email'] = display_email
    app.jinja_env.filters['display_phone'] = display_phone

    def _user_can_access_incentive_simulator():
        from flask_login import current_user

        if not current_user.is_authenticated:
            return False
        from services.incentive_simulator_service import user_sees_any_incentive_sim_section

        return user_sees_any_incentive_sim_section(current_user)

    app.jinja_env.globals['user_can_access_incentive_simulator'] = (
        _user_can_access_incentive_simulator
    )

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
    from routes.pseudo_trades import pseudo_trades_bp
    from routes.tax_optimiser_enhanced import tax_optimiser_enhanced_bp
    from routes.two_factor import two_factor_bp
    from routes.historical_prices import historical_prices_bp
    from routes.bni_referrals import bni_referrals_bp
    from routes.recommendation_execution import rec_exec_bp
    from routes.attendance import attendance_bp

    app.register_blueprint(main)
    from routes.assistant import assistant_bp
    from routes.advisor_review import advisor_review_bp

    app.register_blueprint(assistant_bp)
    app.register_blueprint(advisor_review_bp)
    app.register_blueprint(auth, url_prefix='/auth')
    app.register_blueprint(leads, url_prefix='/leads')
    from routes.risk_assessment import risk_assessment_bp
    app.register_blueprint(risk_assessment_bp)
    app.register_blueprint(meetings)
    # Prefix required: without it, workflows' / and /dashboard collide with main.dashboard (first match wins).
    app.register_blueprint(workflows, url_prefix="/workflows")
    app.register_blueprint(settings, url_prefix="/settings")
    try:
        from routes.google_calendar import google_calendar_bp as _gcal_bp

        app.register_blueprint(_gcal_bp, url_prefix="/settings/google-calendar")
    except Exception as _gcal_e:
        app.logger.warning("Google Calendar blueprint not registered: %s", _gcal_e)
    app.register_blueprint(clients_bp, url_prefix='/clients')
    try:
        from routes.households import households_bp
        app.register_blueprint(households_bp, url_prefix='/households')
    except Exception as _hh_err:
        import logging as _logging
        _logging.getLogger(__name__).warning("households blueprint not registered: %s", _hh_err)
    app.register_blueprint(alerts, url_prefix='/alerts')
    app.register_blueprint(users, url_prefix='/users')
    app.register_blueprint(recommended_trades_bp, url_prefix='/recommended_trades')
    app.register_blueprint(agreements, url_prefix='/agreements')
    app.register_blueprint(unified_recommendations_bp)
    from routes.unified_recommendations import tojson_safe_filter

    app.jinja_env.filters.setdefault("tojson_safe", tojson_safe_filter)
    from services.content_intelligence_person_source import strip_ic_markers

    app.jinja_env.filters.setdefault("strip_ic_markers", strip_ic_markers)
    from services.client_notes_service import parse_notes_entries

    app.jinja_env.filters.setdefault("parse_client_notes", parse_notes_entries)
    app.register_blueprint(client_status_api)
    app.register_blueprint(billing_api)
    app.register_blueprint(review_bp, url_prefix='/review')
    app.register_blueprint(monthly_investments, url_prefix="/monthly-investments")
    app.register_blueprint(tax_optimiser_enhanced_bp)
    app.register_blueprint(pseudo_trades_bp)
    app.register_blueprint(two_factor_bp)
    app.register_blueprint(historical_prices_bp, url_prefix="/historical-prices")
    app.register_blueprint(bni_referrals_bp)
    app.register_blueprint(rec_exec_bp)
    app.register_blueprint(attendance_bp)
    try:
        from routes.notification_centre import notification_centre_ui_bp

        app.register_blueprint(notification_centre_ui_bp)
    except Exception as _nc_e:
        app.logger.warning("Notification centre UI not registered: %s", _nc_e)
    app.config["NAV_TASKS_ENABLED"] = False
    try:
        from routes.tasks import tasks_bp as _tasks_bp

        app.register_blueprint(_tasks_bp, url_prefix="/tasks")
        app.config["NAV_TASKS_ENABLED"] = True
    except Exception as _tasks_e:
        app.logger.warning("Tasks blueprint not registered (OpsTask / routes.tasks): %s", _tasks_e)

    app.config["NAV_ACCOUNT_MANAGEMENT_ENABLED"] = False
    try:
        from routes.account_management import account_management_bp

        app.register_blueprint(account_management_bp)
        app.config["NAV_ACCOUNT_MANAGEMENT_ENABLED"] = True
    except Exception as _am_e:
        app.logger.warning("Account management blueprint not registered: %s", _am_e)

    app.config["NAV_CAMPAIGN_STUDIO_ENABLED"] = False
    try:
        from routes.campaign_studio import campaign_studio_bp

        app.register_blueprint(campaign_studio_bp)
        app.config["NAV_CAMPAIGN_STUDIO_ENABLED"] = True
    except Exception as _cs_e:
        app.logger.warning("Campaign studio blueprint not registered: %s", _cs_e)

    # Financial planning — separately developed module; not part of core Inertia.
    # Enable only when integrating: FINANCIAL_PLANNING_ENABLED=true in .env
    app.config["NAV_FINANCIAL_PLANNING_ENABLED"] = False
    if os.environ.get("FINANCIAL_PLANNING_ENABLED", "").lower() in ("true", "1", "yes"):
        try:
            from routes.financial_planning import financial_planning_bp

            app.register_blueprint(financial_planning_bp)
            app.config["NAV_FINANCIAL_PLANNING_ENABLED"] = True
        except Exception as _fp_e:
            app.logger.warning("Financial planning blueprint not registered: %s", _fp_e)

    app.config["NAV_PARRVA_ENABLED"] = False
    try:
        from routes.parrva import parrva_bp

        app.register_blueprint(parrva_bp)
        app.config["NAV_PARRVA_ENABLED"] = True
    except Exception as _pv_e:
        app.logger.warning("Parrva blueprint not registered: %s", _pv_e)

    app.config["NAV_PORTFOLIO_PERFORMANCE_ENABLED"] = False
    try:
        from routes.portfolio_performance import portfolio_performance_bp

        app.register_blueprint(portfolio_performance_bp)
        app.config["NAV_PORTFOLIO_PERFORMANCE_ENABLED"] = True
    except Exception as _pp_e:
        app.logger.warning("Portfolio performance blueprint not registered: %s", _pp_e)

    app.config["NAV_TASK_ASSIGNMENT_RULES_ENABLED"] = False
    try:
        from routes.task_assignment_rules import task_assignment_rules_bp

        app.register_blueprint(task_assignment_rules_bp)
        app.config["NAV_TASK_ASSIGNMENT_RULES_ENABLED"] = True
    except Exception as _tar_e:
        app.logger.warning("Task assignment rules blueprint not registered: %s", _tar_e)

    app.config["NAV_ROLES_ENABLED"] = False
    try:
        from routes.roles import roles as roles_blueprint

        app.register_blueprint(roles_blueprint, url_prefix="/admin/roles")
        app.config["NAV_ROLES_ENABLED"] = True
    except Exception as _roles_e:
        app.logger.warning("Roles blueprint not registered: %s", _roles_e)

    app.config["NAV_CLIENTS_V2_ENABLED"] = False
    try:
        from routes.clients_v2 import clients_v2_bp

        app.register_blueprint(clients_v2_bp, url_prefix="/clients-v2")
        app.config["NAV_CLIENTS_V2_ENABLED"] = True
    except Exception as _cv2_e:
        app.logger.warning("Clients v2 blueprint not registered: %s", _cv2_e)

    # Optional UI blueprints: register best-effort and expose NAV_* for templates (avoids url_for BuildError).
    _optional_nav_blueprints = [
        ("routes.financial_analytics", "financial_analytics_bp", None, "NAV_FINANCIAL_ANALYTICS_ENABLED"),
        ("routes.tickets", "tickets_bp", None, "NAV_TICKETS_ENABLED"),
        ("routes.corporate_actions", "corporate_actions_bp", "/corporate-actions", "NAV_CORPORATE_ACTIONS_ENABLED"),
        ("routes.enhanced_review_routes", "enhanced_review_bp", None, "NAV_ENHANCED_REVIEW_ENABLED"),
        ("routes.invoices", "invoices", "/invoices", "NAV_INVOICES_ENABLED"),
        ("routes.agents_dashboard", "agents_dashboard_bp", None, "NAV_AGENTS_DASHBOARD_ENABLED"),
        ("routes.airflow", "airflow_bp", None, "NAV_AIRFLOW_ENABLED"),
        ("routes.data_integrity", "data_integrity_bp", None, "NAV_DATA_INTEGRITY_ENABLED"),
        ("routes.orchestrator_dashboard", "orchestrator_bp", None, "NAV_ORCHESTRATOR_ENABLED"),
        ("routes.intelligence_hub", "hub_bp", None, "NAV_INTELLIGENCE_HUB_ENABLED"),
    ]
    for _mod, _attr, _prefix, _flag in _optional_nav_blueprints:
        app.config[_flag] = False
        try:
            _m = __import__(_mod, fromlist=[_attr])
            _bp = getattr(_m, _attr)
            if _prefix:
                app.register_blueprint(_bp, url_prefix=_prefix)
            else:
                app.register_blueprint(_bp)
            app.config[_flag] = True
        except Exception as _nav_e:
            app.logger.warning(
                "Optional nav blueprint %s.%s not registered: %s",
                _mod,
                _attr,
                _nav_e,
            )

    # Initialize API
    from api import init_app as init_api
    init_api(app)

    # Campaign Studio LLM proxies (routes include full /api/v1/... path)
    for _proxy_mod, _proxy_bp in (
        ("api.v1.claude_proxy", "claude_proxy_bp"),
        ("api.v1.openai_proxy", "openai_proxy_bp"),
        ("api.v1.perplexity_proxy", "perplexity_proxy_bp"),
        ("api.v1.openai_images_proxy", "openai_images_proxy_bp"),
    ):
        try:
            _pm = __import__(_proxy_mod, fromlist=[_proxy_bp])
            app.register_blueprint(getattr(_pm, _proxy_bp))
        except Exception as _proxy_e:
            app.logger.warning("LLM proxy %s not registered: %s", _proxy_mod, _proxy_e)
    # CSRF exempt allowlist (see agents/security_audit_agent.py SEC-EXT-005 intentional_exempts).
    try:
        from api.v1.enhanced_tax_optimiser import enhanced_tax_optimiser_bp
        csrf.exempt(enhanced_tax_optimiser_bp)  # JSON API; session not used for mutations
    except Exception as _e:
        app.logger.warning(f"Could not exempt enhanced_tax_optimiser_bp from CSRF: {_e}")
    try:
        from api.v1.public_contact import public_contact_bp
        csrf.exempt(public_contact_bp)  # public_contact_bp: API-key website form
    except Exception as _e:
        app.logger.warning(f"Could not exempt public_contact_bp from CSRF: {_e}")
    try:
        from api.v1.auth import auth_api_bp
        csrf.exempt(auth_api_bp)  # auth_api_bp: JWT login/2FA only
    except Exception as _e:
        app.logger.warning(f"Could not exempt auth_api_bp from CSRF: {_e}")
    try:
        from api.v1.leegality import leegality_bp
        csrf.exempt(leegality_bp)  # leegality_bp: HMAC mac webhook from Leegality
    except Exception as _e:
        app.logger.warning(f"Could not exempt leegality_bp from CSRF: {_e}")

    # Ensure DB column 'transaction.notes' exists (safe no-op if already present)
    try:
        from sqlalchemy import inspect, text
        with app.app_context():
            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('transaction')]
            if 'notes' not in columns:
                db.session.execute(text("ALTER TABLE `transaction` ADD COLUMN notes VARCHAR(1000) NULL"))
                db.session.commit()
    except Exception as _e:
        # Log but don't block app startup
        app.logger.warning(f"Skipping notes column migration: {_e}")

    # Import models after app is created
    with app.app_context():
        from models import User
        
        @login_manager.user_loader
        def load_user(user_id):
            return User.query.get(int(user_id))
    
    # Request correlation ID + session / 2FA
    from utils.request_context import init_request_id, install_request_id_logging

    install_request_id_logging()

    from services.forward_holding_calculation_service import configure_forward_holding_logging

    configure_forward_holding_logging()

    @app.before_request
    def before_request():
        init_request_id()
        session.permanent = True
        from services.two_factor_enforcement import enforce_session_2fa

        blocked = enforce_session_2fa()
        if blocked is not None:
            return blocked

        from flask_login import current_user
        from nav_registry import MENU_ENDPOINTS
        from services.permission_service import nav_can as _nav_can_fn

        endpoint = request.endpoint or ""
        if (
            current_user.is_authenticated
            and endpoint
            and endpoint != "static"
            and endpoint in MENU_ENDPOINTS
            and not _nav_can_fn(current_user, endpoint)
        ):
            from flask import flash, redirect, url_for

            flash("You do not have permission to access this page.", "error")
            return redirect(url_for("main.dashboard"))

    @app.after_request
    def _attach_request_id(response):
        from flask import g

        rid = getattr(g, "request_id", None)
        if rid:
            response.headers["X-Request-ID"] = rid
        return response

    @app.route('/hub/operational/backup-codebase', methods=['POST'])
    @login_required
    def operational_backup_codebase():
        """
        Codebase tarball from Operational Intelligence Hub.
        Defined on the app (not a blueprint) so the endpoint always exists in create_app().
        """
        from flask import current_app
        from services.codebase_backup_service import run_daily_codebase_backup

        try:
            result = run_daily_codebase_backup(current_app.config)
            if result.get('ok'):
                sz = result.get('size_bytes') or 0
                flash(
                    f"Codebase backup OK: {result.get('archive_name')} ({sz // 1024} KB). "
                    f"Pruned {result.get('prune', {}).get('removed_count', 0)} old daily file(s).",
                    'success',
                )
            else:
                flash(f"Codebase backup failed: {result.get('error', 'unknown')}", 'error')
        except Exception as exc:
            app.logger.error('operational_backup_codebase: %s', exc, exc_info=True)
            flash(f'Backup error: {exc}', 'error')

        try:
            return redirect(url_for('hub.operational_dashboard'))
        except Exception:
            return redirect(url_for('main.dashboard'))

    @app.route('/hub/operational/run-pipeline', methods=['POST'])
    @login_required
    def hub_operational_run_pipeline():
        """
        Alerts/tasks reconcile + optional orchestrator refresh.
        Registered on the app (not only the optional hub blueprint) so url_for always works.
        """
        if not getattr(current_user, 'is_authenticated', False) or not getattr(current_user, 'is_admin', False):
            flash('Admin access required.', 'error')
            try:
                return redirect(url_for('hub.operational_dashboard'))
            except Exception:
                return redirect('/hub/operational')
        try:
            from routes.intelligence_hub import (
                execute_operational_pipeline,
                format_operational_pipeline_flash_message,
            )

            refresh = request.form.get('refresh') == '1'
            stats = execute_operational_pipeline(refresh)
            flash(format_operational_pipeline_flash_message(stats, refresh), 'success')
        except Exception as exc:
            app.logger.error('hub_operational_run_pipeline: %s', exc, exc_info=True)
            flash(f'Pipeline failed: {exc}', 'error')
        try:
            return redirect(url_for('hub.operational_dashboard'))
        except Exception:
            return redirect('/hub/operational')

    @app.route('/hub/operational/run-daily-workflow-report', methods=['POST'])
    @login_required
    def hub_operational_run_daily_workflow_report():
        """
        On-demand daily workflow report email (same job as daily_workflow_report.py).
        Registered on the app so url_for works even if hub blueprint wiring differs.
        """
        allowed = (
            getattr(current_user, 'is_manager', False)
            or getattr(current_user, 'is_advisor', False)
            or getattr(current_user, 'is_ops_manager', False)
            or getattr(current_user, 'is_admin', False)
        )
        if not getattr(current_user, 'is_authenticated', False) or not allowed:
            flash(
                'Manager, advisor, ops manager, or admin access required to send the daily workflow report.',
                'error',
            )
            try:
                return redirect(url_for('hub.operational_dashboard'))
            except Exception:
                return redirect('/hub/operational')
        try:
            from daily_workflow_report import execute_daily_workflow_report_job

            result = execute_daily_workflow_report_job()
            status = (result or {}).get('status')
            message = (result or {}).get('message', 'Unknown result')
            if status == 'sent':
                flash(message, 'success')
            elif status == 'skipped':
                flash(message, 'info')
            else:
                flash(message, 'error')
        except Exception as exc:
            app.logger.error('hub_operational_run_daily_workflow_report: %s', exc, exc_info=True)
            flash(f'Daily workflow report failed: {exc}', 'error')
        try:
            return redirect(url_for('hub.operational_dashboard'))
        except Exception:
            return redirect('/hub/operational')

    @app.context_processor
    def inject_template_user():
        """Ensure current_user and common flags exist in all Jinja templates."""
        from flask_login import current_user as cu

        is_auth = bool(getattr(cu, "is_authenticated", False))
        return {
            "current_user": cu,
            "user_is_admin": bool(is_auth and getattr(cu, "is_admin", False)),
            "user_is_manager": bool(is_auth and getattr(cu, "is_manager", False)),
            "user_is_advisor": bool(is_auth and getattr(cu, "is_advisor", False)),
            "user_is_ops_manager": bool(is_auth and getattr(cu, "is_ops_manager", False)),
            "can_manage_ops": bool(
                is_auth
                and (
                    getattr(cu, "is_manager", False)
                    or getattr(cu, "is_advisor", False)
                    or getattr(cu, "is_ops_manager", False)
                )
            ),
        }

    @app.context_processor
    def inject_pending_cashflow_review():
        """Sticky post–trade-update warning until the user acknowledges cashflow review."""
        from flask import session, url_for

        pending = session.get("pending_cashflow_review") or None
        if not pending or not pending.get("transaction_id"):
            return {"pending_cashflow_review": None}
        tid = pending["transaction_id"]
        cid = pending.get("client_id")
        try:
            ack_url = url_for("main.acknowledge_trade_cashflow_review", transaction_id=tid)
            edit_url = url_for("main.edit_transaction", transaction_id=tid, cashflow_ack=1)
            cashflows_url = url_for("main.list_cashflows", client_id=cid) if cid else url_for("main.list_cashflows")
            guide_url = url_for("main.trade_edit_cashflow_guide")
        except Exception:
            ack_url = f"/transaction/{tid}/acknowledge-cashflow-review"
            edit_url = f"/transaction/{tid}/edit?cashflow_ack=1"
            cashflows_url = f"/cashflows?client_id={cid}" if cid else "/cashflows"
            guide_url = "/help/trade-edit-cashflow-review"
        return {
            "pending_cashflow_review": {
                **pending,
                "ack_url": ack_url,
                "edit_url": edit_url,
                "cashflows_url": cashflows_url,
                "guide_url": guide_url,
            }
        }

    @app.context_processor
    def inject_airflow_runs_url():
        """Prefer /airflow/scheduled-jobs (always when Airflow blueprint loads) over Hub URL."""
        from flask import url_for

        for ep in ("airflow.scheduled_jobs", "hub.airflow_runs_dashboard"):
            try:
                return {"airflow_runs_url": url_for(ep)}
            except Exception:
                continue
        return {"airflow_runs_url": None}

    @app.context_processor
    def inject_operational_backup_url():
        """POST target for Hub backup card; literal path fallback if url_for fails."""
        from flask import url_for

        try:
            u = url_for('operational_backup_codebase')
        except Exception:
            u = '/hub/operational/backup-codebase'
        try:
            pipeline_u = url_for('hub_operational_run_pipeline')
        except Exception:
            pipeline_u = '/hub/operational/run-pipeline'
        try:
            dwf_u = url_for('hub_operational_run_daily_workflow_report')
        except Exception:
            dwf_u = '/hub/operational/run-daily-workflow-report'
        return {
            'operational_backup_post_url': u,
            'hub_operational_pipeline_post_url': pipeline_u,
            'hub_operational_daily_workflow_report_post_url': dwf_u,
        }

    return app 