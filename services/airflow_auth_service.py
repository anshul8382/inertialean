"""
Airflow Authentication Service
Handles authentication between Flask and Airflow
"""
import os
import json
import time
import base64
import requests
import logging
from typing import Optional, Dict
from flask import session, current_app
from flask_login import current_user

logger = logging.getLogger(__name__)

class AirflowAuthService:
    """Service for managing Airflow authentication via Flask session"""

    _fab_jwt_token: Optional[str] = None
    _fab_jwt_expires_at: float = 0.0
    
    def __init__(self):
        self.airflow_base_url = os.environ.get('AIRFLOW_BASE_URL', 'http://localhost:8080').rstrip('/')
        self.api_base_url = f'{self.airflow_base_url}/api/v2'
        self.timeout = 10

    def _default_airflow_home(self) -> str:
        if os.environ.get('AIRFLOW_HOME'):
            return os.environ['AIRFLOW_HOME'].rstrip('/')
        try:
            from flask import has_request_context, current_app

            if has_request_context():
                cfg_home = current_app.config.get('AIRFLOW_HOME')
                if cfg_home:
                    return str(cfg_home).rstrip('/')
        except Exception:
            pass
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(app_dir, 'airflow')

    def _password_from_simple_auth_file(self) -> Optional[str]:
        """Plaintext passwords from Airflow Simple Auth Manager (same file Airflow generates)."""
        username = os.environ.get('AIRFLOW_USERNAME', 'admin').strip() or 'admin'
        pw_path = os.environ.get(
            'AIRFLOW_SIMPLE_AUTH_PASSWORDS_FILE',
            os.path.join(self._default_airflow_home(), 'simple_auth_manager_passwords.json.generated'),
        )
        try:
            with open(pw_path, encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            if username in data and data[username] is not None:
                return str(data[username])
            if 'admin' in data and data['admin'] is not None:
                return str(data['admin'])
            if data:
                return str(next(iter(data.values())))
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.debug('Could not read Airflow simple auth passwords file %s: %s', pw_path, exc)
            return None
        return None

    def _resolve_airflow_password(self) -> str:
        """AIRFLOW_PASSWORD env wins; else Simple Auth Manager JSON; else legacy default."""
        env_p = os.environ.get('AIRFLOW_PASSWORD', '').strip()
        if env_p:
            return env_p
        file_p = self._password_from_simple_auth_file()
        if file_p:
            return file_p.strip()
        return 'inertia2025'

    def create_airflow_session(self) -> Optional[str]:
        """
        Create an Airflow session for the current Flask user
        We simulate web UI login
        Returns session cookie value if successful
        """
        try:
            airflow_username = os.environ.get('AIRFLOW_USERNAME', 'admin').strip() or 'admin'
            airflow_password = self._resolve_airflow_password()
            
            # Airflow 3.x with Simple Auth Manager uses web UI authentication
            # We need to simulate the browser login flow
            airflow_session = requests.Session()
            
            # Step 1: Get the login page to get any CSRF tokens or session cookies
            login_page_response = airflow_session.get(
                f'{self.airflow_base_url}/login',
                timeout=self.timeout,
                allow_redirects=True
            )
            
            # Step 2: Try to authenticate via the web UI
            # Airflow 3.x Simple Auth Manager may use form-based auth
            login_data = {
                'username': airflow_username,
                'password': airflow_password
            }
            
            # Try POST to /login (some Airflow versions use this)
            response = airflow_session.post(
                f'{self.airflow_base_url}/login',
                data=login_data,
                timeout=self.timeout,
                allow_redirects=True,
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Referer': f'{self.airflow_base_url}/login'
                }
            )
            
            # Check if we got redirected to home (successful login)
            if response.status_code == 200 and ('home' in response.url.lower() or 'dags' in response.url.lower()):
                # Extract session cookie
                cookies = airflow_session.cookies.get_dict()
                # Airflow cookie name: airflow_session (webserver_config) or session (default)
                session_cookie = cookies.get('airflow_session') or cookies.get('session') or cookies.get('_session') or (list(cookies.values())[0] if cookies else None)
                
                if session_cookie:
                    # Store in Flask session
                    session['airflow_session'] = session_cookie
                    session['airflow_authenticated'] = True
                    logger.info("Airflow session created successfully")
                    return session_cookie
            
            # Alternative: For Simple Auth Manager, we might need to just use the password file directly
            # Since Simple Auth Manager stores plaintext, we can skip API auth and just proxy
            # Mark as authenticated anyway (Airflow will handle auth on its side)
            logger.warning("Could not create Airflow session via API, will use proxy without pre-auth")
            session['airflow_authenticated'] = True  # Allow proxy to handle auth
            return 'proxy_auth'  # Placeholder to indicate proxy should handle auth
            
        except Exception as e:
            logger.error(f"Error creating Airflow session: {str(e)}")
            # Don't fail completely - allow proxy to handle authentication
            session['airflow_authenticated'] = True
            return 'proxy_auth'
    
    def get_airflow_session(self) -> Optional[str]:
        """Get existing Airflow session from Flask session"""
        return session.get('airflow_session')
    
    def is_airflow_authenticated(self) -> bool:
        """Check if Flask user has valid Airflow session"""
        return session.get('airflow_authenticated', False) and self.get_airflow_session() is not None
    
    def clear_airflow_session(self):
        """Clear Airflow session from Flask session"""
        session.pop('airflow_session', None)
        session.pop('airflow_authenticated', None)
    
    def get_proxy_headers(self) -> Dict[str, str]:
        """Get headers for proxying requests to Airflow"""
        session_cookie = self.get_airflow_session()
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
        }
        
        if session_cookie and session_cookie != 'proxy_auth':
            headers['Cookie'] = f'airflow_session={session_cookie}'
        
        return headers
    
    def get_basic_auth_headers(self) -> Dict[str, str]:
        """HTTP Basic (rarely accepted by Airflow 3 FAB API; prefer get_api_auth_headers)."""
        user = os.environ.get('AIRFLOW_USERNAME', 'admin').strip() or 'admin'
        password = self._resolve_airflow_password()
        token = base64.b64encode(f'{user}:{password}'.encode()).decode()
        return {
            'Authorization': f'Basic {token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    @classmethod
    def invalidate_jwt_cache(cls) -> None:
        """Drop cached JWT (e.g. after API 401 or password rotation)."""
        cls._fab_jwt_token = None
        cls._fab_jwt_expires_at = 0.0

    def get_api_auth_headers(self) -> Dict[str, str]:
        """
        Headers for Airflow 3 REST: JWT from env, /auth/token using AIRFLOW_PASSWORD or the
        Simple Auth passwords file under AIRFLOW_HOME, then Basic fallback.
        Optional: AIRFLOW_API_JWT, AIRFLOW_SIMPLE_AUTH_PASSWORDS_FILE.
        """
        base = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        env_jwt = os.environ.get('AIRFLOW_API_JWT', '').strip()
        if env_jwt:
            base['Authorization'] = f'Bearer {env_jwt}'
            return base

        now = time.time()
        if self._fab_jwt_token and now < self._fab_jwt_expires_at:
            base['Authorization'] = f'Bearer {self._fab_jwt_token}'
            return base

        password = self._resolve_airflow_password()
        username = os.environ.get('AIRFLOW_USERNAME', 'admin').strip() or 'admin'
        if password:
            try:
                r = requests.post(
                    f'{self.airflow_base_url}/auth/token',
                    json={'username': username, 'password': password},
                    timeout=self.timeout,
                )
                if r.status_code == 200:
                    data = r.json()
                    tok = data.get('access_token')
                    if tok:
                        self.__class__._fab_jwt_token = tok
                        # Default FAB JWT lifetime is typically 24h; refresh early.
                        self.__class__._fab_jwt_expires_at = now + float(
                            os.environ.get('AIRFLOW_JWT_CACHE_SECONDS', '82800')
                        )
                        base['Authorization'] = f'Bearer {tok}'
                        return base
                logger.warning(
                    'Airflow JWT auth endpoint failed: status=%s', r.status_code
                )
            except Exception as exc:
                logger.warning('Airflow JWT login error: %s', exc)

        h = self.get_basic_auth_headers()
        base.update(h)
        return base

# Global instance
airflow_auth_service = AirflowAuthService()
