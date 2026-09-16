"""
Email Configuration for Alert System
Prefers MAIL_* env vars (same as main app). Update defaults here or set in .env.
"""

import os

def _env(key: str, default: str = '') -> str:
    v = os.environ.get(key, default)
    return (v or '').strip().strip('"\'').replace('\r', '').replace('\n', '')

# Prefer MAIL_* so one .env works for app and alerts; fallback to config file defaults
EMAIL_CONFIG = {
    'smtp_server': _env('MAIL_SERVER') or _env('SMTP_SERVER') or 'smtp.gmail.com',
    'smtp_port': int(_env('MAIL_PORT') or _env('SMTP_PORT') or '587'),
    'sender_email': _env('MAIL_USERNAME') or _env('SMTP_USERNAME') or 'alerts@equities4wealth.com',
    'sender_password': _env('MAIL_PASSWORD') or _env('SMTP_PASSWORD') or '',
    'recipients': [
        'anshul@equities4wealth.com',
        'service@equities4wealth.com'
    ],
    'use_tls': True,
    'timeout': 30,
    'send_email_alerts': True,
    'log_email_errors': True,
}

# Instructions for setup:
"""
1. For Gmail:
   - Enable 2-factor authentication
   - Generate an app password
   - Use the app password in 'sender_password'

2. For other email providers:
   - Update smtp_server and smtp_port
   - Use appropriate authentication method

3. Test configuration:
   - Run: python test_email_config.py
   - Check logs for any errors

4. Security:
   - Never commit passwords to version control
   - Use environment variables for sensitive data
   - Consider using email services like SendGrid, AWS SES
"""





