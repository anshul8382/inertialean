from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
import logging

db = SQLAlchemy()
mail = Mail()
login_manager = LoginManager()
csrf = CSRFProtect()
migrate = Migrate()

logger = logging.getLogger(__name__)

# Optional dependency: Flask-Session (server-side sessions)
# In some environments (e.g. local tooling), this package may not be installed.
try:
    from flask_session import Session  # type: ignore
    session_ext = Session()
except Exception as e:  # pragma: no cover
    session_ext = None
    logger.warning(f"Flask-Session not available; falling back to cookie sessions. Reason: {e}")

__all__ = ['db', 'csrf', 'mail', 'migrate', 'login_manager', 'session_ext']