import os
from datetime import timedelta
import pytz

# Load .env before Config reads os.environ. Gunicorn loads .env in wsgi.py, but Airflow/cron
# often import create_app without that step (cwd may be /tmp), leaving MAIL_PASSWORD empty.
try:
    from dotenv import load_dotenv

    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.isfile(_env_path):
        load_dotenv(_env_path)
except ImportError:
    pass

class Config:
    # Flask settings — set SECRET_KEY in .env; dev/test configs supply a local fallback.
    SECRET_KEY = os.environ.get('SECRET_KEY', '')
    # Optional dedicated JWT HMAC key (>=32 bytes); falls back to SECRET_KEY when unset.
    JWT_SECRET = os.environ.get('JWT_SECRET', '') or SECRET_KEY
    
    # Timezone settings
    TIMEZONE = pytz.timezone('Asia/Kolkata')  # Indian Standard Time
    
    # Database settings - Use environment variables
    DB_USER = os.environ.get('DB_USER', 'inertia_admin')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
    DB_HOST = os.environ.get('DB_HOST', '127.0.0.1')
    DB_PORT = os.environ.get('DB_PORT', '3306')
    DB_NAME = os.environ.get('DB_NAME', 'inertia_app2025')
    
    @property
    def SQLALCHEMY_DATABASE_URI(self):
        return f'mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}'
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
        'max_overflow': 20,
        'pool_timeout': 30
    }
    
    # Performance optimizations
    SEND_FILE_MAX_AGE_DEFAULT = 31536000  # 1 year cache for static files
    TEMPLATES_AUTO_RELOAD = os.environ.get('TEMPLATES_AUTO_RELOAD', 'True').lower() == 'true'
    
    # Session settings - Set to 2 hours for better security
    PERMANENT_SESSION_LIFETIME = timedelta(hours=2)

    # All active users must enable TOTP 2FA (set FORCE_2FA_FOR_ALL_USERS=false to disable)
    FORCE_2FA_FOR_ALL_USERS = os.environ.get('FORCE_2FA_FOR_ALL_USERS', 'true').lower() == 'true'

    # DB cutover: comma-separated feature keys to skip until additive migrations run on prod.
    # Default "audit_log" avoids failed commits when audit_log table is missing. See docs/DB_CUTOVER_REGISTRY.md
    # At cutover: set DEFER_DB_FEATURES= (empty) after running migrations/add_audit_log_table.py
    DEFER_DB_FEATURES = os.environ.get('DEFER_DB_FEATURES', 'audit_log')
    
    # File upload settings
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    
    # Email configuration - Gmail SMTP (main.py overwrites these from .env; no hardcoded password)
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True').lower() in ['true', 'on', '1']
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'False').lower() in ['true', 'on', '1']
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', 'anshul@equities4wealth.com')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')  # Must be in .env (Gmail app password)
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'anshul@equities4wealth.com')
    WEBSITE_CONTACT_API_KEY = os.environ.get('WEBSITE_CONTACT_API_KEY', '')

    # Parallel Lean VPS: set EMAIL_SOURCE_TAG=Lean server so DAG/app mail is distinguishable.
    # Leave empty on BigRock production.
    EMAIL_SOURCE_TAG = os.environ.get('EMAIL_SOURCE_TAG', '').strip()
    # When false (default), DataIntegrityIssue does not create OpsTasks — advisor digests instead.
    DI_CREATE_OPS_TASKS = os.environ.get('DI_CREATE_OPS_TASKS', 'false').lower() in (
        '1', 'true', 'yes', 'on',
    )
    # When false (default), ReviewWorkflow does not auto-create OpsTasks — the open review is the
    # work item; users may still create a task for their own tracking.
    REVIEW_CREATE_OPS_TASKS = os.environ.get('REVIEW_CREATE_OPS_TASKS', 'false').lower() in (
        '1', 'true', 'yes', 'on',
    )

    # AI API Keys
    # Perplexity: also used as fallback for hybrid portfolio review prose if Anthropic fails or is unset
    # (see services/portfolio_hybrid_report_generator.py). Optional env: PERPLEXITY_REPORT_MODEL,
    # PERPLEXITY_REPORT_MAX_TOKENS, HYBRID_REPORT_FORCE_PERPLEXITY=true (skip Anthropic).
    PERPLEXITY_API_KEY = os.environ.get('PERPLEXITY_API_KEY', '')
    # Anthropic Messages API (hybrid review report, claude proxy). Loaded from env / .env at import time.
    # Hybrid report code prefers os.environ["ANTHROPIC_API_KEY"] first, then this value via current_app.config.
    # If the API returns "credit balance is too low", add API credits for this key's org in Anthropic Console
    # (Plans & billing); that is not configurable here.
    ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

    # When False, app does not call Ollama, Perplexity, AIInsightsService, or hybrid AI for content.
    # Lean / clean-VPS default: AI off. Set ENABLE_AI_SERVICES=true only if you intend cloud/local LLM.
    ENABLE_AI_SERVICES = os.environ.get('ENABLE_AI_SERVICES', '').lower() in ('true', '1', 'on', 'yes')

    # Local Ollama HTTP API — empty by default in lean builds (no VPS Ollama).
    # Also requires OLLAMA_ENABLED=true in services.ollama_service.
    OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', '')

    # When Anthropic/OpenAI fail, do NOT fall back to Ollama on clean VPS (default false).
    LLM_FALLBACK_TO_OLLAMA = os.environ.get('LLM_FALLBACK_TO_OLLAMA', 'false').lower() in (
        'true',
        '1',
        'on',
        'yes',
    )

    # Hybrid portfolio review: skip Anthropic/Perplexity entirely; emit “[Populate with narrative]” blocks.
    # Audit engine + tables stay fully local. Set PORTFOLIO_REVIEW_HYBRID_SKIP_LLM=true (env or override in subclass).
    PORTFOLIO_REVIEW_HYBRID_SKIP_LLM = os.environ.get('PORTFOLIO_REVIEW_HYBRID_SKIP_LLM', '').lower() in (
        'true',
        '1',
        'on',
        'yes',
    )

    # Session Configuration — secure cookies default ON for production/staging (HTTPS)
    _flask_env = os.environ.get('FLASK_ENV', 'production')
    _secure_cookies_default = 'true' if _flask_env in ('production', 'staging') else 'false'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', _secure_cookies_default).lower() == 'true'
    REMEMBER_COOKIE_SECURE = os.environ.get('REMEMBER_COOKIE_SECURE', _secure_cookies_default).lower() == 'true'
    SESSION_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_DURATION = timedelta(hours=8)  # Remember me for 8 hours only
    SESSION_REFRESH_EACH_REQUEST = False  # Disable session refresh to avoid conflicts with Flask-Login
    SESSION_COOKIE_SAMESITE = 'Lax'  # Allow cross-site requests
    SESSION_COOKIE_PATH = '/'  # Cookie available for all paths
    SESSION_COOKIE_DOMAIN = os.environ.get('SESSION_COOKIE_DOMAIN', None)
    REMEMBER_COOKIE_DOMAIN = os.environ.get('REMEMBER_COOKIE_DOMAIN', None)
    SESSION_COOKIE_NAME = 'inertia_session'  # Custom session cookie name
    SESSION_COOKIE_MAX_AGE = 28800  # 8 hours in seconds

    # Flask-Login session fingerprinting behind reverse proxy (Apache :5003 → Gunicorn).
    # 'strong' compares IP; X-Forwarded-For can differ between requests → empty session → CSRF missing.
    # Default basic (User-Agent only); set LOGIN_SESSION_PROTECTION=strong if you need stricter checks.
    LOGIN_SESSION_PROTECTION = os.environ.get('LOGIN_SESSION_PROTECTION', 'basic')
    
    # CSRF Configuration - Use environment variables
    WTF_CSRF_ENABLED = os.environ.get('WTF_CSRF_ENABLED', 'True').lower() == 'true'
    WTF_CSRF_SECRET_KEY = SECRET_KEY
    # None = token valid for the browser session (see Flask-WTF). A short TTL breaks
    # long-open tabs and modals that embed {{ csrf_token() }} once at page load.
    WTF_CSRF_TIME_LIMIT = None
    WTF_CSRF_SSL_STRICT = os.environ.get('WTF_CSRF_SSL_STRICT', 'False').lower() == 'true'
    WTF_CSRF_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE']
    WTF_CSRF_CHECK_DEFAULT = True
    WTF_CSRF_HEADERS = ['X-CSRFToken', 'X-CSRF-Token']
    WTF_CSRF_FIELD_NAME = 'csrf_token'
    
    # WhatsApp Business API Configuration
    WHATSAPP_API_URL = os.environ.get('WHATSAPP_API_URL', 'https://graph.facebook.com/v18.0')
    WHATSAPP_ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
    WHATSAPP_PHONE_NUMBER_ID = os.environ.get('WHATSAPP_PHONE_NUMBER_ID')
    WHATSAPP_VERIFY_TOKEN = os.environ.get('WHATSAPP_VERIFY_TOKEN')
    WHATSAPP_APP_SECRET = os.environ.get('WHATSAPP_APP_SECRET', '')
    WHATSAPP_WEBHOOK_URL = os.environ.get('WHATSAPP_WEBHOOK_URL')
    # Meta WhatsApp Cloud API — Groups (micro groups). Enable after token has group scopes and Graph version supports Groups.
    WHATSAPP_GROUPS_ENABLED = os.environ.get('WHATSAPP_GROUPS_ENABLED', 'false').lower() in ('1', 'true', 'yes')
    _join_mode = (os.environ.get('WHATSAPP_GROUPS_DEFAULT_JOIN_APPROVAL') or 'auto_approve').strip()
    WHATSAPP_GROUPS_DEFAULT_JOIN_APPROVAL = (
        _join_mode if _join_mode in ('auto_approve', 'approval_required') else 'auto_approve'
    )

    # Zoho Books/Invoice push (optional). India DC — Books API (Finance suite).
    # Many orgs invoice from Zoho Books; set ZOHO_API_BASE to invoice/v3 only for standalone Invoice.
    ZOHO_CLIENT_ID = os.environ.get('ZOHO_CLIENT_ID', '')
    ZOHO_CLIENT_SECRET = os.environ.get('ZOHO_CLIENT_SECRET', '')
    ZOHO_REFRESH_TOKEN = os.environ.get('ZOHO_REFRESH_TOKEN', '')
    ZOHO_ORGANIZATION_ID = os.environ.get('ZOHO_ORGANIZATION_ID', '')
    ZOHO_ACCOUNTS_URL = os.environ.get('ZOHO_ACCOUNTS_URL', 'https://accounts.zoho.in')
    ZOHO_BOOKS_API_BASE = os.environ.get(
        'ZOHO_API_BASE',
        os.environ.get('ZOHO_BOOKS_API_BASE', 'https://www.zohoapis.in/books/v3'),
    )
    ZOHO_REDIRECT_URI = os.environ.get(
        'ZOHO_REDIRECT_URI',
        'http://127.0.0.1:5001/settings/zoho/callback',
    )
    ZOHO_OAUTH_SCOPE = os.environ.get(
        'ZOHO_OAUTH_SCOPE',
        'ZohoBooks.fullaccess.all',
    )

    # PaRRVA API (portfolio submission)
    PARRVA_API_URL = os.environ.get('PARRVA_API_URL')
    PARRVA_API_KEY = os.environ.get('PARRVA_API_KEY')

    # Leegality e-sign (Document Execution API)
    LEEGALITY_AUTH_TOKEN = (os.environ.get('LEEGALITY_AUTH_TOKEN') or '').strip()
    LEEGALITY_PRIVATE_SALT = (os.environ.get('LEEGALITY_PRIVATE_SALT') or '').strip()
    LEEGALITY_PROFILE_ID = (os.environ.get('LEEGALITY_PROFILE_ID') or '').strip()
    LEEGALITY_API_BASE = (
        os.environ.get('LEEGALITY_API_BASE') or 'https://app1.leegality.com/api'
    ).strip().rstrip('/')
    # Optional firm/Inertia signer when workflow uses API invitees for both parties.
    # Recommended order with fixed signing: firm_first (Anshul checks, then client).
    LEEGALITY_FIRM_SIGNER_NAME = (os.environ.get('LEEGALITY_FIRM_SIGNER_NAME') or '').strip()
    LEEGALITY_FIRM_SIGNER_EMAIL = (os.environ.get('LEEGALITY_FIRM_SIGNER_EMAIL') or '').strip()
    LEEGALITY_FIRM_SIGNER_PHONE = (os.environ.get('LEEGALITY_FIRM_SIGNER_PHONE') or '').strip()
    LEEGALITY_INVITEE_ORDER = (
        os.environ.get('LEEGALITY_INVITEE_ORDER') or 'firm_first'
    ).strip().lower()

    # Airflow metadata (SQLite) for Intelligence Hub / scheduled-job dashboards
    _app_dir = os.path.dirname(os.path.abspath(__file__))
    _default_airflow_home = os.path.join(_app_dir, 'airflow')
    AIRFLOW_HOME = os.environ.get('AIRFLOW_HOME', _default_airflow_home)
    AIRFLOW_METADATA_DB_PATH = os.environ.get(
        'AIRFLOW_METADATA_DB_PATH',
        os.path.join(AIRFLOW_HOME, 'airflow.db'),
    )

    # Apps Script / automation: spike ack + bulk historical corrections (hub price-accuracy)
    PRICE_ACCURACY_API_TOKEN = (os.environ.get('PRICE_ACCURACY_API_TOKEN') or '').strip()
    # Hub "verify from Google Sheet": max relative diff between DB close and GOOGLEFINANCE (via Sheets) to auto-ack
    PRICE_ACCURACY_SHEETS_MATCH_TOLERANCE_PCT = float(
        os.environ.get('PRICE_ACCURACY_SHEETS_MATCH_TOLERANCE_PCT', '1') or '1'
    )
    # Hub “Clean sheets”: comma-separated tab names whose data rows (below header) are deleted (never Prices/Nifty).
    GOOGLE_SHEETS_CLEANUP_WORKSHEETS = (os.environ.get('GOOGLE_SHEETS_CLEANUP_WORKSHEETS') or 'HistoricalPrices').strip()

    # Google Calendar + Tasks (user OAuth): same client id/secret; assignee’s Calendar + default Tasks list
    GOOGLE_CALENDAR_CLIENT_ID = (os.environ.get('GOOGLE_CALENDAR_CLIENT_ID') or '').strip()
    GOOGLE_CALENDAR_CLIENT_SECRET = (os.environ.get('GOOGLE_CALENDAR_CLIENT_SECRET') or '').strip()
    # Optional link from Hub to Airflow UI (e.g. https://airflow.example.com)
    AIRFLOW_UI_BASE_URL = os.environ.get('AIRFLOW_UI_BASE_URL', '').rstrip('/')

    # Local codebase tar backups (see services/codebase_backup_service.py, scripts/codebase_backup.py)
    CODEBASE_BACKUP_ROOT = os.environ.get('CODEBASE_BACKUP_ROOT', '')
    CODEBASE_BACKUP_SOURCE = os.environ.get('CODEBASE_BACKUP_SOURCE', '')
    # Optional Google Drive copy (share folder with service_account.json client_email)
    CODEBASE_BACKUP_DRIVE_FOLDER_ID = os.environ.get('CODEBASE_BACKUP_DRIVE_FOLDER_ID', '').strip()
    CODEBASE_BACKUP_DRIVE_ENABLED = os.environ.get('CODEBASE_BACKUP_DRIVE_ENABLED', '').strip()
    GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get(
        'GOOGLE_SERVICE_ACCOUNT_FILE',
        os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', ''),
    ).strip()

    @staticmethod
    def init_app(app):
        pass

class DevelopmentConfig(Config):
    DEBUG = True
    TEMPLATES_AUTO_RELOAD = True  # Enable template auto-reload in development
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-key-please-change-in-production'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
    REMEMBER_COOKIE_SECURE = os.environ.get('REMEMBER_COOKIE_SECURE', 'false').lower() == 'true'
    DB_NAME = os.environ.get('DB_NAME', 'inertia_app2025_dev')
    # Smaller pool for development
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 5,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
        'max_overflow': 10,
        'pool_timeout': 30
    }
    WHATSAPP_WEBHOOK_URL = os.environ.get('WHATSAPP_WEBHOOK_URL', 'http://localhost:5001/api/v1/whatsapp/webhook')

class StagingConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    WTF_CSRF_SSL_STRICT = True
    DB_NAME = os.environ.get('DB_NAME', 'inertia_app2025_staging')
    TEMPLATES_AUTO_RELOAD = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 8,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
        'max_overflow': 15,
        'pool_timeout': 30
    }
    SERVER_NAME = os.environ.get('SERVER_NAME', '66.116.199.231:5000')
    WHATSAPP_WEBHOOK_URL = os.environ.get('WHATSAPP_WEBHOOK_URL', 'https://66.116.199.231:5000/api/v1/whatsapp/webhook')

    @staticmethod
    def init_app(app):
        if not app.config.get('SECRET_KEY'):
            raise RuntimeError(
                'SECRET_KEY must be set in the environment for staging deployments.'
            )

class ProductionConfig(Config):
    DEBUG = False
    # Allow HTTP IP smoke before certbot: set SESSION_COOKIE_SECURE=false in .env
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'true').lower() == 'true'
    REMEMBER_COOKIE_SECURE = os.environ.get('REMEMBER_COOKIE_SECURE', 'true').lower() == 'true'
    WTF_CSRF_SSL_STRICT = os.environ.get('WTF_CSRF_SSL_STRICT', 'false').lower() == 'true'
    DB_NAME = os.environ.get('DB_NAME', 'inertia_app2025')
    TEMPLATES_AUTO_RELOAD = False
    # Only set when explicitly configured — default None so IP access works
    SERVER_NAME = os.environ.get('SERVER_NAME') or None
    WHATSAPP_WEBHOOK_URL = os.environ.get(
        'WHATSAPP_WEBHOOK_URL',
        'https://inertiainvest.in/api/v1/whatsapp/webhook',
    )

    @staticmethod
    def init_app(app):
        if not app.config.get('SECRET_KEY'):
            raise RuntimeError(
                'SECRET_KEY must be set in the environment for production deployments.'
            )
        if len(app.config['SECRET_KEY']) < 32:
            import logging
            logging.getLogger(__name__).warning(
                'SECRET_KEY is shorter than 32 bytes; generate with '
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )

class TestConfig(Config):
    TESTING = True
    DEBUG = True
    FORCE_2FA_FOR_ALL_USERS = False
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'test-secret-key-for-pytest-only-min-32-chars!!'
    TEMPLATES_AUTO_RELOAD = True
    DB_NAME = os.environ.get('DB_NAME', 'inertia_app2025_test')
    # Use smaller pool for testing
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 3,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
        'max_overflow': 5,
        'pool_timeout': 30
    }
    # Disable CSRF for testing
    WTF_CSRF_ENABLED = False
    # Test-specific session settings
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    WHATSAPP_WEBHOOK_URL = os.environ.get('WHATSAPP_WEBHOOK_URL', 'http://localhost:5002/api/v1/whatsapp/webhook')
    # Preserve session data during testing
    PRESERVE_CONTEXT_ON_EXCEPTION = False

config = {
    'development': DevelopmentConfig,
    'staging': StagingConfig,
    'production': ProductionConfig,
    'test': TestConfig,
    'default': ProductionConfig
}
