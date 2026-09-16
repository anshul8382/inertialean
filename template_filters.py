import json

from markupsafe import Markup, escape


def number_format(value, decimal_places=2):
    try:
        if value is None:
            return '0.00'
        return f"{float(value):,.{decimal_places}f}"
    except (ValueError, TypeError):
        return '0.00'


def int_format(value):
    """Comma-separated integer for email/HTML tables (matches number_format thousands style)."""
    try:
        if value is None:
            return "0"
        n = int(round(float(value)))
        return f"{n:,}"
    except (ValueError, TypeError):
        return "0"

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


def nl2br(value):
    """Escape user text and preserve line breaks for HTML rendering."""
    if value is None:
        return ''
    return Markup(str(escape(value)).replace('\n', '<br>\n'))


def from_json(value):
    """Safely parse JSON values in templates; returns {} on failure."""
    if not value:
        return {}
    try:
        if isinstance(value, (dict, list)):
            return value
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def register_template_filters(app):
    app.add_template_filter(number_format, 'number_format')
    app.add_template_filter(int_format, 'int_format')
    app.add_template_filter(status_color, 'status_color') 
    app.add_template_filter(nl2br, 'nl2br')
    app.add_template_filter(from_json, 'from_json')