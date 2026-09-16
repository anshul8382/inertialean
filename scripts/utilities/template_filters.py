def number_format(value, decimal_places=2):
    try:
        if value is None:
            return '0.00'
        return f"{float(value):,.{decimal_places}f}"
    except (ValueError, TypeError):
        return '0.00'

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

def register_template_filters(app):
    app.add_template_filter(number_format, 'number_format')
    app.add_template_filter(status_color, 'status_color') 